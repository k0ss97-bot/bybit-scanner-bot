from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from bybit_client import BybitClient, Ticker
from config import Settings
from history import HistoryStore
from long_scanner import pct_change
from state import Snapshot, StateStore, SymbolState

if TYPE_CHECKING:
    from binance_client import BinanceClient, BinanceTicker


@dataclass(frozen=True)
class PumpExhaustionSignal:
    symbol: str
    window_minutes: int
    lookback_days: int
    signal_score: int
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
class PumpWatchlistAlert:
    symbol: str
    window_minutes: int
    lookback_days: int
    signal_score: int
    passed_checks: list[str]
    missing_checks: list[str]
    price_growth_lookback_pct: float
    drawdown_from_high_pct: float
    required_oi_drop_pct: float
    oi_change_pct: float
    cvd_change_pct: float
    cvd_delta_usdt: float
    price_change_window_pct: float
    price: float
    turnover_24h: float


@dataclass(frozen=True)
class PumpScanResult:
    signals: list[PumpExhaustionSignal]
    watchlist_alerts: list[PumpWatchlistAlert]
    scanned_symbols: int
    failed_symbols: int
    skipped_symbols: int
    rejection_reasons: dict[str, int]


class PumpExhaustionScanner:
    def __init__(
        self,
        client: BybitClient,
        store: StateStore,
        settings: Settings,
        history: HistoryStore | None = None,
        binance_client: BinanceClient | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.settings = settings
        self.history = history
        self.binance_client = binance_client
        self.no_spot_symbols: set[str] = set()
        self.last_spot_cvd_update_ts: dict[str, int] = {}

    def scan_once(self, progress_callback=None) -> PumpScanResult:
        now = int(time.time())
        tickers = self._select_tickers()
        if progress_callback is not None:
            progress_callback(0, len(tickers))
        binance_tickers = self._get_binance_tickers()
        signals = []
        watchlist_alerts = []
        failed_symbols = 0
        skipped_symbols = 0
        rejection_reasons: dict[str, int] = {}

        for index, ticker in enumerate(tickers, start=1):
            try:
                state = self.store.get_symbol(ticker.symbol)
                new_trades = self._update_cvd(ticker.symbol, state)
                new_spot_trades = self._update_spot_cvd(ticker.symbol, state)
                self._add_snapshot(now, ticker, state, new_trades, new_spot_trades)

                signal, watchlist_alert = self._build_signal(
                    now,
                    ticker,
                    state,
                    rejection_reasons,
                    binance_tickers.get(ticker.symbol),
                )
                if signal is not None:
                    state.last_alert_ts = now
                    state.last_alert_score = signal.signal_score
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
                elif (
                    watchlist_alert is not None
                    and len(watchlist_alerts) < self.settings.watchlist_max_alerts_per_scan
                ):
                    state.last_watchlist_ts = now
                    watchlist_alerts.append(watchlist_alert)
                else:
                    skipped_symbols += 1
            except Exception as error:
                failed_symbols += 1
                print(f"{ticker.symbol}: pump scan failed: {error}")
            finally:
                if progress_callback is not None and (index % 10 == 0 or index == len(tickers)):
                    progress_callback(index, len(tickers))

        self.store.save()
        return PumpScanResult(
            signals=signals,
            watchlist_alerts=watchlist_alerts,
            scanned_symbols=len(tickers),
            failed_symbols=failed_symbols,
            skipped_symbols=skipped_symbols,
            rejection_reasons=rejection_reasons,
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

    def _get_binance_tickers(self) -> dict[str, BinanceTicker]:
        if not self.settings.binance_confirm_enabled or self.binance_client is None:
            return {}
        try:
            return self.binance_client.get_usdt_perp_tickers()
        except Exception as error:
            print(f"Binance confirmation unavailable: {error}", flush=True)
            return {}

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
        rejection_reasons: dict[str, int],
        binance_ticker: BinanceTicker | None,
    ) -> tuple[PumpExhaustionSignal | None, PumpWatchlistAlert | None]:
        current = state.snapshots[-1]
        previous = self._find_window_snapshot(current.ts, state)
        if previous is None:
            state.consecutive_matches = 0
            count_reason(rejection_reasons, "warmup_no_window")
            return None, None

        structure = self._get_pump_structure(ticker)
        if structure is None:
            state.consecutive_matches = 0
            count_reason(rejection_reasons, "no_pump_structure")
            return None, None

        price_growth_lookback_pct, drawdown_from_high_pct, high_price = structure
        oi_change_pct = pct_change(previous.oi, current.oi)
        cvd_delta = current.cvd - previous.cvd
        cvd_change_pct = pct_change(previous.cvd, current.cvd)
        spot_cvd_delta = current.spot_cvd - previous.spot_cvd
        spot_cvd_change_pct = pct_change(previous.spot_cvd, current.spot_cvd)
        price_change_window_pct = pct_change(previous.price, current.price)
        required_oi_drop_pct = self._required_oi_drop_pct(drawdown_from_high_pct)

        checks = {
            "price_growth": price_growth_lookback_pct
            >= self.settings.pump_min_price_growth_lookback_pct,
            "drawdown": drawdown_from_high_pct
            <= -self.settings.pump_min_drawdown_from_high_pct,
            "oi_not_rising": oi_change_pct <= self.settings.pump_max_oi_change_pct,
            "oi_drop_required": oi_change_pct <= -required_oi_drop_pct,
            "cvd_delta": cvd_delta <= -self.settings.pump_min_negative_cvd_delta_usdt,
            "cvd_pct": cvd_change_pct <= -self.settings.pump_min_negative_cvd_change_pct,
            "price_window": price_change_window_pct
            <= self.settings.pump_max_price_change_window_pct,
            "binance_volume": (
                not self.settings.binance_confirmation_required
                or (
                    binance_ticker is not None
                    and binance_ticker.quote_volume_24h
                    >= self.settings.binance_min_quote_volume_24h_usdt
                )
            ),
        }
        signal_score = self._score_signal(
            price_growth_lookback_pct=price_growth_lookback_pct,
            drawdown_from_high_pct=drawdown_from_high_pct,
            oi_change_pct=oi_change_pct,
            required_oi_drop_pct=required_oi_drop_pct,
            cvd_delta=cvd_delta,
            cvd_change_pct=cvd_change_pct,
            price_change_window_pct=price_change_window_pct,
        )

        if not all(checks.values()) or signal_score < self.settings.pump_min_signal_score:
            state.consecutive_matches = 0
            for reason, passed in checks.items():
                if not passed:
                    count_reason(rejection_reasons, reason)
            if signal_score < self.settings.pump_min_signal_score:
                count_reason(rejection_reasons, "score")
            return None, self._build_watchlist_alert(
                now=now,
                ticker=ticker,
                state=state,
                signal_score=signal_score,
                checks=checks,
                price_growth_lookback_pct=price_growth_lookback_pct,
                drawdown_from_high_pct=drawdown_from_high_pct,
                required_oi_drop_pct=required_oi_drop_pct,
                oi_change_pct=oi_change_pct,
                cvd_change_pct=cvd_change_pct,
                cvd_delta=cvd_delta,
                price_change_window_pct=price_change_window_pct,
            )

        if (
            now - state.last_alert_ts < self.settings.pump_alert_cooldown_minutes * 60
            and signal_score < state.last_alert_score + self.settings.pump_alert_score_improvement
        ):
            count_reason(rejection_reasons, "cooldown")
            return None, None

        state.consecutive_matches += 1
        if state.consecutive_matches < self.settings.pump_consecutive_checks:
            count_reason(rejection_reasons, "confirmations_waiting")
            return None, self._build_watchlist_alert(
                now=now,
                ticker=ticker,
                state=state,
                signal_score=signal_score,
                checks=checks,
                price_growth_lookback_pct=price_growth_lookback_pct,
                drawdown_from_high_pct=drawdown_from_high_pct,
                required_oi_drop_pct=required_oi_drop_pct,
                oi_change_pct=oi_change_pct,
                cvd_change_pct=cvd_change_pct,
                cvd_delta=cvd_delta,
                price_change_window_pct=price_change_window_pct,
            )

        return PumpExhaustionSignal(
            symbol=ticker.symbol,
            window_minutes=self.settings.pump_window_minutes,
            lookback_days=self.settings.pump_lookback_days,
            signal_score=signal_score,
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
        ), None

    def _score_signal(
        self,
        *,
        price_growth_lookback_pct: float,
        drawdown_from_high_pct: float,
        oi_change_pct: float,
        required_oi_drop_pct: float,
        cvd_delta: float,
        cvd_change_pct: float,
        price_change_window_pct: float,
    ) -> int:
        score = 0
        if price_growth_lookback_pct >= self.settings.pump_min_price_growth_lookback_pct * 1.5:
            score += 2
        elif price_growth_lookback_pct >= self.settings.pump_min_price_growth_lookback_pct:
            score += 1

        drawdown = abs(min(0, drawdown_from_high_pct))
        if drawdown >= self.settings.pump_min_drawdown_from_high_pct * 1.5:
            score += 2
        elif drawdown >= self.settings.pump_min_drawdown_from_high_pct:
            score += 1

        if oi_change_pct <= -required_oi_drop_pct * 1.5:
            score += 2
        elif oi_change_pct <= -required_oi_drop_pct:
            score += 1

        if (
            cvd_delta <= -self.settings.pump_min_negative_cvd_delta_usdt
            and cvd_change_pct <= -self.settings.pump_min_negative_cvd_change_pct
        ):
            score += 2
        elif cvd_delta < 0 and cvd_change_pct < 0:
            score += 1

        if price_change_window_pct < 0:
            score += 2
        elif price_change_window_pct <= self.settings.pump_max_price_change_window_pct:
            score += 1

        return min(score, 10)

    def _build_watchlist_alert(
        self,
        *,
        now: int,
        ticker: Ticker,
        state: SymbolState,
        signal_score: int,
        checks: dict[str, bool],
        price_growth_lookback_pct: float,
        drawdown_from_high_pct: float,
        required_oi_drop_pct: float,
        oi_change_pct: float,
        cvd_change_pct: float,
        cvd_delta: float,
        price_change_window_pct: float,
    ) -> PumpWatchlistAlert | None:
        if not self.settings.watchlist_enabled:
            return None
        if signal_score < self.settings.pump_watchlist_min_score:
            return None
        if now - state.last_watchlist_ts < self.settings.watchlist_cooldown_minutes * 60:
            return None

        passed_checks = [name for name, passed in checks.items() if passed]
        missing_checks = [name for name, passed in checks.items() if not passed]
        if len(passed_checks) < 4:
            return None

        return PumpWatchlistAlert(
            symbol=ticker.symbol,
            window_minutes=self.settings.pump_window_minutes,
            lookback_days=self.settings.pump_lookback_days,
            signal_score=signal_score,
            passed_checks=passed_checks,
            missing_checks=missing_checks,
            price_growth_lookback_pct=price_growth_lookback_pct,
            drawdown_from_high_pct=drawdown_from_high_pct,
            required_oi_drop_pct=required_oi_drop_pct,
            oi_change_pct=oi_change_pct,
            cvd_change_pct=cvd_change_pct,
            cvd_delta_usdt=cvd_delta,
            price_change_window_pct=price_change_window_pct,
            price=ticker.price,
            turnover_24h=ticker.turnover_24h,
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


def count_reason(rejection_reasons: dict[str, int], reason: str) -> None:
    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
