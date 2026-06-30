from __future__ import annotations

from dataclasses import dataclass
import time

from bybit_client import BybitClient, Ticker
from config import Settings
from history import HistoryStore
from state import Snapshot, StateStore, SymbolState


@dataclass(frozen=True)
class LongSignal:
    symbol: str
    window_minutes: int
    lookback_days: int
    base_growth_pct: float
    current_from_base_pct: float
    base_high_price: float
    oi_change_pct: float
    cvd_change_pct: float
    cvd_delta_usdt: float
    spot_cvd_change_pct: float
    spot_cvd_delta_usdt: float
    funding_rate: float
    price_change_pct: float
    price: float
    turnover_24h: float
    new_trades: int
    new_spot_trades: int
    consecutive_matches: int


@dataclass(frozen=True)
class BaseStructure:
    lookback_days: int
    base_growth_pct: float
    current_from_base_pct: float
    base_high_price: float


@dataclass(frozen=True)
class ScanResult:
    signals: list[LongSignal]
    scanned_symbols: int
    failed_symbols: int
    skipped_symbols: int


class LongScanner:
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

    def scan_once(self) -> ScanResult:
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
                            signal_type="long",
                            symbol=signal.symbol,
                            price=signal.price,
                            open_interest_change_pct=signal.oi_change_pct,
                            futures_cvd_change_pct=signal.cvd_change_pct,
                            futures_cvd_delta_usdt=signal.cvd_delta_usdt,
                            spot_cvd_change_pct=signal.spot_cvd_change_pct,
                            spot_cvd_delta_usdt=signal.spot_cvd_delta_usdt,
                            price_change_pct=signal.price_change_pct,
                            payload=str(signal),
                        )
                    signals.append(signal)
                else:
                    skipped_symbols += 1
            except Exception as error:
                failed_symbols += 1
                print(f"{ticker.symbol}: scan failed: {error}")

        self.store.save()
        return ScanResult(
            signals=signals,
            scanned_symbols=len(tickers),
            failed_symbols=failed_symbols,
            skipped_symbols=skipped_symbols,
        )

    def _select_tickers(self) -> list[Ticker]:
        tickers = [
            ticker
            for ticker in self.client.get_linear_tickers()
            if ticker.turnover_24h >= self.settings.min_turnover_24h_usdt
            and ticker.open_interest > 0
        ]
        tickers.sort(key=lambda item: item.turnover_24h, reverse=True)
        return tickers[: self.settings.max_symbols]

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
                scanner="long",
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
        min_ts = now - self.settings.window_minutes * 60 * 3
        state.snapshots = [snapshot for snapshot in state.snapshots if snapshot.ts >= min_ts]

    def _build_signal(
        self,
        now: int,
        ticker: Ticker,
        state: SymbolState,
    ) -> LongSignal | None:
        if now - state.last_alert_ts < self.settings.alert_cooldown_minutes * 60:
            return None

        current = state.snapshots[-1]
        previous = self._find_window_snapshot(current.ts, state)
        if previous is None:
            state.consecutive_matches = 0
            return None

        oi_change_pct = pct_change(previous.oi, current.oi)
        cvd_delta = current.cvd - previous.cvd
        cvd_change_pct = pct_change(previous.cvd, current.cvd)
        spot_cvd_delta = current.spot_cvd - previous.spot_cvd
        spot_cvd_change_pct = pct_change(previous.spot_cvd, current.spot_cvd)
        price_change_pct = pct_change(previous.price, current.price)
        base_structure = self._get_base_structure(ticker)
        if base_structure is None:
            state.consecutive_matches = 0
            return None

        matched = (
            oi_change_pct >= self.settings.oi_threshold_pct
            and cvd_delta >= self.settings.min_cvd_delta_usdt
            and cvd_change_pct >= self.settings.cvd_threshold_pct
            and current.new_trades >= self.settings.min_new_trades
            and base_structure.base_growth_pct
            <= self.settings.long_max_price_growth_lookback_pct
            and price_change_pct <= self.settings.long_max_price_change_window_pct
            and (
                not self.settings.require_price_hold
                or price_change_pct >= self.settings.price_min_change_pct
            )
        )

        if not matched:
            state.consecutive_matches = 0
            return None

        state.consecutive_matches += 1
        if state.consecutive_matches < self.settings.consecutive_checks:
            return None

        return LongSignal(
            symbol=ticker.symbol,
            window_minutes=self.settings.window_minutes,
            lookback_days=base_structure.lookback_days,
            base_growth_pct=base_structure.base_growth_pct,
            current_from_base_pct=base_structure.current_from_base_pct,
            base_high_price=base_structure.base_high_price,
            oi_change_pct=oi_change_pct,
            cvd_change_pct=cvd_change_pct,
            cvd_delta_usdt=cvd_delta,
            spot_cvd_change_pct=spot_cvd_change_pct,
            spot_cvd_delta_usdt=spot_cvd_delta,
            funding_rate=ticker.funding_rate,
            price_change_pct=price_change_pct,
            price=ticker.price,
            turnover_24h=ticker.turnover_24h,
            new_trades=current.new_trades,
            new_spot_trades=current.new_spot_trades,
            consecutive_matches=state.consecutive_matches,
        )

    def _get_base_structure(self, ticker: Ticker) -> BaseStructure | None:
        lookback_days = max(2, self.settings.long_lookback_days)
        klines = self.client.get_daily_klines(ticker.symbol, limit=lookback_days + 1)
        if len(klines) < lookback_days:
            return None

        closed_klines = klines[:-1] if len(klines) > lookback_days else klines
        base_klines = closed_klines[-lookback_days:]
        if not base_klines:
            return None

        base_open = base_klines[0].open_price
        base_high = max(kline.high_price for kline in base_klines)
        return BaseStructure(
            lookback_days=len(base_klines),
            base_growth_pct=pct_change(base_open, base_high),
            current_from_base_pct=pct_change(base_open, ticker.price),
            base_high_price=base_high,
        )

    def _find_window_snapshot(self, now: int, state: SymbolState) -> Snapshot | None:
        target = now - self.settings.window_minutes * 60
        candidates = [snapshot for snapshot in state.snapshots if snapshot.ts <= target]
        if not candidates:
            return None
        return candidates[-1]


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 100.0 if new > 0 else 0.0
    return ((new - old) / abs(old)) * 100
