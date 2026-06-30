from __future__ import annotations

from dataclasses import dataclass
import time

from bybit_client import BybitClient, Ticker
from config import Settings
from history import HistoryStore
from long_scanner import pct_change
from state import Snapshot, StateStore, SymbolState


@dataclass(frozen=True)
class PumpExhaustionSignal:
    symbol: str
    window_minutes: int
    lookback_days: int
    price_growth_lookback_pct: float
    drawdown_from_high_pct: float
    required_oi_drop_pct: float
    oi_change_pct: float
    cvd_change_pct: float
    cvd_delta_usdt: float
    spot_cvd_change_pct: float
    spot_cvd_delta_usdt: float
    funding_rate: float
    price_change_window_pct: float
    price: float
    high_price_24h: float
    turnover_24h: float
    new_trades: int
    new_spot_trades: int
    consecutive_matches: int


@dataclass(frozen=True)
class PumpScanResult:
    signals: list[PumpExhaustionSignal]
    scanned_symbols: int
    failed_symbols: int
    skipped_symbols: int


class PumpExhaustionScanner:
    def __init__(
        self,
        client: BybitClient,
        store: StateStore,
        settings: Settings,
        history: HistoryStore | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.settings = settings
        self.history = history
        self.no_spot_symbols: set[str] = set()
        self.last_spot_cvd_update_ts: dict[str, int] = {}

    def scan_once(self) -> PumpScanResult:
        now = int(time.time())
        tickers = self._select_tickers()
        signals = []
        failed_symbols = 0
        skipped_symbols = 0

        for ticker in tickers:
            try:
                state = self.store.get_symbol(ticker.symbol)
                new_trades = self._update_cvd(ticker.symbol, state)
                new_spot_trades = self._update_spot_cvd(ticker.symbol, state)
                self._add_snapshot(now, ticker, state, new_trades, new_spot_trades)

                signal = self._build_signal(now, ticker, state)
                if signal is not None:
                    state.last_alert_ts = now
                    if self.history is not None:
                        self.history.record_signal(
                            signal_type="pump_exhaustion",
                            symbol=signal.symbol,
                            ts=now,
                            price=signal.price,
                            open_interest_change_pct=signal.oi_change_pct,
                            futures_cvd_change_pct=signal.cvd_change_pct,
                            futures_cvd_delta_usdt=signal.cvd_delta_usdt,
                            spot_cvd_change_pct=signal.spot_cvd_change_pct,
                            spot_cvd_delta_usdt=signal.spot_cvd_delta_usdt,
                            price_change_pct=signal.price_change_window_pct,
                            payload=str(signal),
                        )
                    signals.append(signal)
                else:
                    skipped_symbols += 1
            except Exception as error:
                failed_symbols += 1
                print(f"{ticker.symbol}: pump scan failed: {error}")

        self.store.save()
        return PumpScanResult(
            signals=signals,
            scanned_symbols=len(tickers),
            failed_symbols=failed_symbols,
            skipped_symbols=skipped_symbols,
        )

    def _select_tickers(self) -> list[Ticker]:
        tickers = [
            ticker
            for ticker in self.client.get_linear_tickers()
            if ticker.turnover_24h >= self.settings.pump_min_turnover_24h_usdt
            and ticker.open_interest > 0
            and ticker.high_price_24h > 0
        ]
        tickers.sort(key=lambda item: item.turnover_24h, reverse=True)
        return tickers[: self.settings.pump_max_symbols]

    def _update_cvd(self, symbol: str, state: SymbolState) -> int:
        seen = set(state.seen_trade_ids)
        new_trades = []
        for trade in self.client.get_recent_trades(symbol):
            if trade.exec_id not in seen:
                new_trades.append(trade)

        for trade in new_trades:
            state.cumulative_cvd += trade.signed_notional

        recent_ids = [trade.exec_id for trade in new_trades] + state.seen_trade_ids
        state.seen_trade_ids = recent_ids[:3000]
        return len(new_trades)

    def _update_spot_cvd(self, symbol: str, state: SymbolState) -> int:
        if symbol in self.no_spot_symbols:
            return 0
        now = int(time.time())
        last_update_ts = self.last_spot_cvd_update_ts.get(symbol, 0)
        if now - last_update_ts < self.settings.spot_cvd_update_interval_seconds:
            return 0
        self.last_spot_cvd_update_ts[symbol] = now

        seen = set(state.seen_spot_trade_ids)
        new_trades = []
        try:
            trades = self.client.get_recent_trades(symbol, category="spot")
        except Exception as error:
            if "Not supported symbols" in str(error):
                self.no_spot_symbols.add(symbol)
            else:
                print(f"{symbol}: spot CVD unavailable: {error}")
            return 0

        for trade in trades:
            if trade.exec_id not in seen:
                new_trades.append(trade)

        for trade in new_trades:
            state.cumulative_spot_cvd += trade.signed_notional

        recent_ids = [trade.exec_id for trade in new_trades] + state.seen_spot_trade_ids
        state.seen_spot_trade_ids = recent_ids[:3000]
        return len(new_trades)

    def _add_snapshot(
        self,
        now: int,
        ticker: Ticker,
        state: SymbolState,
        new_trades: int,
        new_spot_trades: int,
    ) -> None:
        state.snapshots.append(
            Snapshot(
                ts=now,
                oi=ticker.open_interest,
                cvd=state.cumulative_cvd,
                spot_cvd=state.cumulative_spot_cvd,
                price=ticker.price,
                funding=ticker.funding_rate,
                turnover_24h=ticker.turnover_24h,
                new_trades=new_trades,
                new_spot_trades=new_spot_trades,
            )
        )
        if self.history is not None:
            self.history.record_snapshot(
                scanner="pump",
                symbol=ticker.symbol,
                ts=now,
                price=ticker.price,
                open_interest=ticker.open_interest,
                futures_cvd=state.cumulative_cvd,
                spot_cvd=state.cumulative_spot_cvd,
                funding=ticker.funding_rate,
                turnover_24h=ticker.turnover_24h,
                new_futures_trades=new_trades,
                new_spot_trades=new_spot_trades,
            )
        min_ts = now - self.settings.pump_window_minutes * 60 * 3
        state.snapshots = [snapshot for snapshot in state.snapshots if snapshot.ts >= min_ts]

    def _build_signal(
        self,
        now: int,
        ticker: Ticker,
        state: SymbolState,
    ) -> PumpExhaustionSignal | None:
        if now - state.last_alert_ts < self.settings.pump_alert_cooldown_minutes * 60:
            return None

        current = state.snapshots[-1]
        previous = self._find_window_snapshot(current.ts, state)
        if previous is None:
            state.consecutive_matches = 0
            return None

        structure = self._get_pump_structure(ticker)
        if structure is None:
            state.consecutive_matches = 0
            return None

        price_growth_lookback_pct, drawdown_from_high_pct, high_price = structure
        oi_change_pct = pct_change(previous.oi, current.oi)
        cvd_delta = current.cvd - previous.cvd
        cvd_change_pct = pct_change(previous.cvd, current.cvd)
        spot_cvd_delta = current.spot_cvd - previous.spot_cvd
        spot_cvd_change_pct = pct_change(previous.spot_cvd, current.spot_cvd)
        price_change_window_pct = pct_change(previous.price, current.price)
        required_oi_drop_pct = self._required_oi_drop_pct(drawdown_from_high_pct)

        matched = (
            price_growth_lookback_pct >= self.settings.pump_min_price_growth_lookback_pct
            and drawdown_from_high_pct <= -self.settings.pump_min_drawdown_from_high_pct
            and oi_change_pct <= self.settings.pump_max_oi_change_pct
            and oi_change_pct <= -required_oi_drop_pct
            and cvd_delta <= -self.settings.pump_min_negative_cvd_delta_usdt
            and cvd_change_pct <= -self.settings.pump_min_negative_cvd_change_pct
            and price_change_window_pct <= self.settings.pump_max_price_change_window_pct
            and current.new_trades >= self.settings.pump_min_new_trades
        )

        if not matched:
            state.consecutive_matches = 0
            return None

        state.consecutive_matches += 1
        if state.consecutive_matches < self.settings.pump_consecutive_checks:
            return None

        return PumpExhaustionSignal(
            symbol=ticker.symbol,
            window_minutes=self.settings.pump_window_minutes,
            lookback_days=self.settings.pump_lookback_days,
            price_growth_lookback_pct=price_growth_lookback_pct,
            drawdown_from_high_pct=drawdown_from_high_pct,
            required_oi_drop_pct=required_oi_drop_pct,
            oi_change_pct=oi_change_pct,
            cvd_change_pct=cvd_change_pct,
            cvd_delta_usdt=cvd_delta,
            spot_cvd_change_pct=spot_cvd_change_pct,
            spot_cvd_delta_usdt=spot_cvd_delta,
            funding_rate=ticker.funding_rate,
            price_change_window_pct=price_change_window_pct,
            price=ticker.price,
            high_price_24h=high_price,
            turnover_24h=ticker.turnover_24h,
            new_trades=current.new_trades,
            new_spot_trades=current.new_spot_trades,
            consecutive_matches=state.consecutive_matches,
        )

    def _get_pump_structure(self, ticker: Ticker) -> tuple[float, float, float] | None:
        limit = max(3, self.settings.pump_lookback_days + 1)
        klines = self.client.get_daily_klines(ticker.symbol, limit=limit)
        if len(klines) < self.settings.pump_lookback_days:
            return None

        lookback = klines[-self.settings.pump_lookback_days :]
        base_open = lookback[0].open_price
        high_price = max(kline.high_price for kline in lookback)
        if base_open <= 0 or high_price <= 0:
            return None

        price_growth_lookback_pct = pct_change(base_open, high_price)
        drawdown_from_high_pct = pct_change(high_price, ticker.price)
        return price_growth_lookback_pct, drawdown_from_high_pct, high_price

    def _required_oi_drop_pct(self, drawdown_from_high_pct: float) -> float:
        drawdown = abs(min(0, drawdown_from_high_pct))
        return drawdown * self.settings.pump_oi_drop_ratio_to_drawdown

    def _find_window_snapshot(self, now: int, state: SymbolState) -> Snapshot | None:
        target = now - self.settings.pump_window_minutes * 60
        candidates = [snapshot for snapshot in state.snapshots if snapshot.ts <= target]
        if not candidates:
            return None
        return candidates[-1]
