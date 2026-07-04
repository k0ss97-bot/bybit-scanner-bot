from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from bybit_client import BybitClient, Ticker
from config import Settings
from history import HistoryStore
from liquidity import OrderbookLiquidity, liquidity_score, unknown_liquidity
from state import Snapshot, StateStore, SymbolState

if TYPE_CHECKING:
    from binance_client import BinanceClient, BinanceTicker


@dataclass(frozen=True)
class LongSignal:
    symbol: str
    window_minutes: int
    lookback_days: int
    base_growth_pct: float
    current_from_base_pct: float
    base_high_price: float
    base_low_price: float
    base_range_pct: float
    price_from_base_high_pct: float
    base_avg_turnover: float
    turnover_ratio_to_base: float
    price_change_24h_pct: float
    signal_score: int
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
    liquidity_quality: str = "unknown"
    spread_bps: float = 0.0
    depth_1pct_usdt: float = 0.0
    depth_coverage_1h: float = 0.0
    setup_type: str = "momentum"


@dataclass(frozen=True)
class LongWatchlistAlert:
    symbol: str
    window_minutes: int
    signal_score: int
    passed_checks: list[str]
    missing_checks: list[str]
    oi_change_pct: float
    cvd_change_pct: float
    cvd_delta_usdt: float
    spot_cvd_change_pct: float
    price_change_pct: float
    turnover_ratio_to_base: float
    price: float
    turnover_24h: float
    liquidity_quality: str = "unknown"
    spread_bps: float = 0.0
    depth_1pct_usdt: float = 0.0
    depth_coverage_1h: float = 0.0


@dataclass(frozen=True)
class BaseStructure:
    lookback_days: int
    base_growth_pct: float
    current_from_base_pct: float
    base_high_price: float
    base_low_price: float
    base_range_pct: float
    base_avg_turnover: float
    turnover_ratio_to_base: float


@dataclass(frozen=True)
class BaseCacheEntry:
    ts: int
    lookback_days: int
    base_growth_pct: float
    base_high_price: float
    base_low_price: float
    base_avg_turnover: float
    base_open_price: float


@dataclass(frozen=True)
class ScanResult:
    signals: list[LongSignal]
    watchlist_alerts: list[LongWatchlistAlert]
    scanned_symbols: int
    failed_symbols: int
    skipped_symbols: int
    rejection_reasons: dict[str, int]


class LongScanner:
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
        self.base_cache: dict[str, BaseCacheEntry] = {}
        self.liquidity_cache: dict[str, tuple[int, OrderbookLiquidity]] = {}
        self.last_spot_cvd_update_ts: dict[str, int] = {}

    def scan_once(self, progress_callback=None) -> ScanResult:
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
        watchlist_limit = max(0, self.settings.watchlist_max_alerts_per_scan)
        watchlist_cooldown_seconds = max(0, self.settings.watchlist_cooldown_minutes) * 60

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
                        signal_type = {
                            "accumulation": "long_accumulation",
                            "breakout": "long_breakout",
                        }.get(signal.setup_type, "long")
                        self.history.record_signal(
                            signal_type=signal_type,
                            symbol=signal.symbol,
                            ts=now,
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
                elif (
                    watchlist_alert is not None
                    and len(watchlist_alerts) < watchlist_limit
                ):
                    state.last_watchlist_ts = now
                    if self.history is not None and self.settings.candidate_tracking_enabled:
                        self.history.record_watchlist_candidate(
                            scanner="long",
                            symbol=watchlist_alert.symbol,
                            score=watchlist_alert.signal_score,
                            price=watchlist_alert.price,
                            passed_checks=watchlist_alert.passed_checks,
                            missing_checks=watchlist_alert.missing_checks,
                            payload=str(watchlist_alert),
                            ts=now,
                            cooldown_seconds=watchlist_cooldown_seconds,
                        )
                    watchlist_alerts.append(watchlist_alert)
                else:
                    skipped_symbols += 1
            except Exception as error:
                failed_symbols += 1
                print(f"{ticker.symbol}: scan failed: {error}")
            finally:
                if progress_callback is not None and (index % 10 == 0 or index == len(tickers)):
                    progress_callback(index, len(tickers))

        self.store.save()
        return ScanResult(
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
            if ticker.turnover_24h >= self.settings.min_turnover_24h_usdt
            and ticker.open_interest > 0
        ]
        tickers.sort(key=lambda item: item.turnover_24h, reverse=True)
        return tickers[: self.settings.max_symbols]

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
        retention_minutes = max(
            self.settings.window_minutes,
            self.settings.long_accumulation_window_minutes,
            self.settings.long_breakout_window_minutes,
            *self.settings.long_accumulation_windows_minutes,
        )
        min_ts = now - retention_minutes * 60 * 3
        state.snapshots = [snapshot for snapshot in state.snapshots if snapshot.ts >= min_ts]

    def _build_signal(
        self,
        now: int,
        ticker: Ticker,
        state: SymbolState,
        rejection_reasons: dict[str, int],
        binance_ticker: BinanceTicker | None,
    ) -> tuple[LongSignal | None, LongWatchlistAlert | None]:
        current = state.snapshots[-1]
        previous = self._find_window_snapshot(current.ts, state)
        if previous is None:
            state.consecutive_matches = 0
            count_reason(rejection_reasons, "warmup_no_window")
            return None, None

        oi_change_pct = pct_change(previous.oi, current.oi)
        cvd_delta = current.cvd - previous.cvd
        cvd_change_pct = pct_change(previous.cvd, current.cvd)
        spot_cvd_delta = current.spot_cvd - previous.spot_cvd
        spot_cvd_change_pct = pct_change(previous.spot_cvd, current.spot_cvd)
        price_change_pct = pct_change(previous.price, current.price)
        base_structure = self._get_base_structure(ticker)
        if base_structure is None:
            state.consecutive_matches = 0
            count_reason(rejection_reasons, "no_base_structure")
            return None, None

        binance_volume_ok = (
            not self.settings.binance_confirmation_required
            or (
                binance_ticker is not None
                and binance_ticker.quote_volume_24h
                >= self.settings.binance_min_quote_volume_24h_usdt
            )
        )
        signal_score = self._score_signal(
            oi_change_pct=oi_change_pct,
            cvd_change_pct=cvd_change_pct,
            cvd_delta=cvd_delta,
            spot_cvd_change_pct=spot_cvd_change_pct,
            price_change_pct=price_change_pct,
            base_structure=base_structure,
            price_change_24h_pct=ticker.price_change_24h_pct,
        )
        liquidity = self._get_liquidity_if_near_signal(
            ticker,
            signal_score,
            self.settings.long_min_signal_score,
        )
        signal_score = min(10, signal_score + liquidity_score(liquidity))
        momentum_required_cvd = self._required_cvd_delta(
            ticker,
            self.settings.window_minutes,
            self.settings.min_cvd_delta_usdt,
        )
        price_momentum_ok = price_change_pct >= self.settings.price_min_change_pct
        cvd_flow_ok = positive_flow_ok(
            cvd_delta,
            cvd_change_pct,
            momentum_required_cvd,
            self.settings.cvd_threshold_pct,
        )

        checks = {
            "momentum_enabled": self.settings.long_momentum_enabled,
            "price_momentum": price_momentum_ok,
            "cvd_delta": cvd_flow_ok,
            "score": signal_score >= self.settings.long_min_signal_score,
            "price_too_high": price_change_pct
            <= self.settings.long_max_price_change_window_pct,
            "not_24h_overheated": ticker.price_change_24h_pct
            <= self.settings.long_max_24h_price_change_pct,
            "binance_volume": binance_volume_ok,
        }

        setup_type = "momentum"
        signal_window_minutes = self.settings.window_minutes
        signal_checks = checks
        best_watchlist = (
            signal_score,
            checks,
            oi_change_pct,
            cvd_change_pct,
            cvd_delta,
            spot_cvd_change_pct,
            price_change_pct,
            self.settings.window_minutes,
            liquidity,
        )

        if not all(checks.values()):
            breakout_candidate = self._build_breakout_candidate(
                ticker=ticker,
                current=current,
                state=state,
                base_structure=base_structure,
                binance_volume_ok=binance_volume_ok,
            )
            if breakout_candidate is not None:
                best_watchlist = best_candidate(
                    best_watchlist,
                    (
                        *breakout_candidate[:7],
                        self.settings.long_breakout_window_minutes,
                        breakout_candidate[7],
                    ),
                )
                if all(breakout_candidate[1].values()):
                    (
                        signal_score,
                        signal_checks,
                        oi_change_pct,
                        cvd_change_pct,
                        cvd_delta,
                        spot_cvd_change_pct,
                        price_change_pct,
                        liquidity,
                    ) = breakout_candidate
                    spot_cvd_delta = current.spot_cvd - self._find_window_snapshot(
                        current.ts,
                        state,
                        self.settings.long_breakout_window_minutes,
                    ).spot_cvd
                    setup_type = "breakout"
                    signal_window_minutes = self.settings.long_breakout_window_minutes

        if setup_type == "momentum" and not all(checks.values()):
            accumulation_candidate = self._best_accumulation_candidate(
                ticker=ticker,
                current=current,
                state=state,
                base_structure=base_structure,
                binance_volume_ok=binance_volume_ok,
                rejection_reasons=rejection_reasons,
            )
            if accumulation_candidate is not None:
                best_watchlist = best_candidate(
                    best_watchlist,
                    (
                        *accumulation_candidate[:7],
                        accumulation_candidate[8],
                        accumulation_candidate[9],
                    ),
                )
                if all(accumulation_candidate[1].values()):
                    (
                        signal_score,
                        signal_checks,
                        oi_change_pct,
                        cvd_change_pct,
                        cvd_delta,
                        spot_cvd_change_pct,
                        price_change_pct,
                        spot_cvd_delta,
                        signal_window_minutes,
                        liquidity,
                    ) = accumulation_candidate
                    setup_type = "accumulation"

        if setup_type == "momentum" and not all(checks.values()):
            state.consecutive_matches = 0
            (
                best_score,
                best_checks,
                best_oi,
                best_cvd_pct,
                best_cvd_delta,
                best_spot_cvd,
                best_price,
                best_window_minutes,
                best_liquidity,
            ) = best_watchlist
            for reason, passed in best_checks.items():
                if not passed:
                    count_reason(rejection_reasons, reason)
            return None, self._build_watchlist_alert(
                now=now,
                ticker=ticker,
                state=state,
                signal_score=best_score,
                checks=best_checks,
                oi_change_pct=best_oi,
                cvd_change_pct=best_cvd_pct,
                cvd_delta=best_cvd_delta,
                spot_cvd_change_pct=best_spot_cvd,
                price_change_pct=best_price,
                turnover_ratio_to_base=base_structure.turnover_ratio_to_base,
                window_minutes=best_window_minutes,
                liquidity=best_liquidity,
            )

        repeat_rejection = self._repeat_alert_rejection(now, state, signal_score)
        if repeat_rejection is not None:
            count_reason(rejection_reasons, repeat_rejection)
            return None, None

        state.consecutive_matches += 1
        if state.consecutive_matches < self.settings.consecutive_checks:
            count_reason(rejection_reasons, "confirmations_waiting")
            return None, self._build_watchlist_alert(
                now=now,
                ticker=ticker,
                state=state,
                signal_score=signal_score,
                checks=signal_checks,
                oi_change_pct=oi_change_pct,
                cvd_change_pct=cvd_change_pct,
                cvd_delta=cvd_delta,
                spot_cvd_change_pct=spot_cvd_change_pct,
                price_change_pct=price_change_pct,
                turnover_ratio_to_base=base_structure.turnover_ratio_to_base,
                window_minutes=signal_window_minutes,
                liquidity=liquidity,
            )

        return LongSignal(
            symbol=ticker.symbol,
            window_minutes=signal_window_minutes,
            lookback_days=base_structure.lookback_days,
            base_growth_pct=base_structure.base_growth_pct,
            current_from_base_pct=base_structure.current_from_base_pct,
            base_high_price=base_structure.base_high_price,
            base_low_price=base_structure.base_low_price,
            base_range_pct=base_structure.base_range_pct,
            price_from_base_high_pct=pct_change(base_structure.base_high_price, ticker.price),
            base_avg_turnover=base_structure.base_avg_turnover,
            turnover_ratio_to_base=base_structure.turnover_ratio_to_base,
            price_change_24h_pct=ticker.price_change_24h_pct,
            signal_score=signal_score,
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
            liquidity_quality=liquidity.quality,
            spread_bps=liquidity.spread_bps,
            depth_1pct_usdt=liquidity.depth_1pct_usdt,
            depth_coverage_1h=liquidity.depth_coverage_1h,
            setup_type=setup_type,
        ), None

    def _build_breakout_candidate(
        self,
        *,
        ticker: Ticker,
        current: Snapshot,
        state: SymbolState,
        base_structure: BaseStructure,
        binance_volume_ok: bool,
    ) -> tuple[int, dict[str, bool], float, float, float, float, float, OrderbookLiquidity] | None:
        previous = self._find_window_snapshot(
            current.ts,
            state,
            self.settings.long_breakout_window_minutes,
        )
        if previous is None:
            return (
                0,
                {"breakout_window": False},
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                unknown_liquidity(ticker.symbol),
            )

        oi_change_pct = pct_change(previous.oi, current.oi)
        cvd_delta = current.cvd - previous.cvd
        cvd_change_pct = pct_change(previous.cvd, current.cvd)
        spot_cvd_change_pct = pct_change(previous.spot_cvd, current.spot_cvd)
        price_change_pct = pct_change(previous.price, current.price)
        required_cvd = self._required_cvd_delta(
            ticker,
            self.settings.long_breakout_window_minutes,
            self.settings.long_breakout_min_cvd_delta_usdt,
        )
        signal_score = self._score_signal(
            oi_change_pct=oi_change_pct,
            cvd_change_pct=cvd_change_pct,
            cvd_delta=cvd_delta,
            spot_cvd_change_pct=spot_cvd_change_pct,
            price_change_pct=price_change_pct,
            base_structure=base_structure,
            price_change_24h_pct=ticker.price_change_24h_pct,
        )
        liquidity = self._get_liquidity_if_near_signal(
            ticker,
            signal_score,
            self.settings.long_breakout_min_signal_score,
        )
        signal_score = min(10, signal_score + liquidity_score(liquidity))
        checks = {
            "breakout_enabled": self.settings.long_breakout_enabled,
            "breakout_price": price_change_pct
            >= self.settings.long_breakout_min_price_change_pct,
            "breakout_not_overheated": price_change_pct
            <= self.settings.long_breakout_max_price_change_pct,
            "breakout_oi": oi_change_pct
            >= self.settings.long_breakout_min_oi_change_pct,
            "breakout_cvd": positive_flow_ok(
                cvd_delta,
                cvd_change_pct,
                required_cvd,
                self.settings.cvd_threshold_pct,
            ),
            "breakout_not_overextended": base_structure.current_from_base_pct
            <= self.settings.long_breakout_max_current_from_base_pct,
            "breakout_not_24h_overheated": ticker.price_change_24h_pct
            <= self.settings.long_max_24h_price_change_pct,
            "breakout_score": signal_score
            >= self.settings.long_breakout_min_signal_score,
            "binance_volume": binance_volume_ok,
        }
        return (
            signal_score,
            checks,
            oi_change_pct,
            cvd_change_pct,
            cvd_delta,
            spot_cvd_change_pct,
            price_change_pct,
            liquidity,
        )

    def _best_accumulation_candidate(
        self,
        *,
        ticker: Ticker,
        current: Snapshot,
        state: SymbolState,
        base_structure: BaseStructure,
        binance_volume_ok: bool,
        rejection_reasons: dict[str, int],
    ) -> tuple[int, dict[str, bool], float, float, float, float, float, float, int, OrderbookLiquidity] | None:
        best = None
        windows = sorted(
            set(
                self.settings.long_accumulation_windows_minutes
                or (self.settings.long_accumulation_window_minutes,)
            )
        )
        for window_minutes in windows:
            accumulation_previous = self._find_window_snapshot(
                current.ts,
                state,
                window_minutes,
            )
            if accumulation_previous is None:
                count_reason(rejection_reasons, "acc_warmup_no_window")
                candidate = (
                    0,
                    {f"acc_{window_minutes}m_window": False},
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    window_minutes,
                    unknown_liquidity(ticker.symbol),
                )
            else:
                acc_oi_change_pct = pct_change(accumulation_previous.oi, current.oi)
                acc_cvd_delta = current.cvd - accumulation_previous.cvd
                acc_cvd_change_pct = pct_change(accumulation_previous.cvd, current.cvd)
                acc_spot_cvd_delta = current.spot_cvd - accumulation_previous.spot_cvd
                acc_spot_cvd_change_pct = pct_change(
                    accumulation_previous.spot_cvd,
                    current.spot_cvd,
                )
                acc_price_change_pct = pct_change(accumulation_previous.price, current.price)
                required_cvd = self._required_cvd_delta(
                    ticker,
                    window_minutes,
                    self.settings.long_accumulation_min_cvd_delta_usdt,
                )
                accumulation_score = self._score_signal(
                    oi_change_pct=acc_oi_change_pct,
                    cvd_change_pct=acc_cvd_change_pct,
                    cvd_delta=acc_cvd_delta,
                    spot_cvd_change_pct=acc_spot_cvd_change_pct,
                    price_change_pct=acc_price_change_pct,
                    base_structure=base_structure,
                    price_change_24h_pct=ticker.price_change_24h_pct,
                )
                liquidity = self._get_liquidity_if_near_signal(
                    ticker,
                    accumulation_score,
                    self.settings.long_accumulation_min_signal_score,
                )
                accumulation_score = min(10, accumulation_score + liquidity_score(liquidity))
                accumulation_checks = {
                    f"acc_{window_minutes}m_enabled": self.settings.long_accumulation_enabled,
                    f"acc_{window_minutes}m_price_flat": self.settings.long_accumulation_min_price_change_pct
                    <= acc_price_change_pct
                    <= self.settings.long_accumulation_max_price_change_pct,
                    f"acc_{window_minutes}m_oi_building": acc_oi_change_pct
                    >= self.settings.long_accumulation_min_oi_change_pct,
                    f"acc_{window_minutes}m_cvd_building": positive_flow_ok(
                        acc_cvd_delta,
                        acc_cvd_change_pct,
                        required_cvd,
                        self.settings.cvd_threshold_pct,
                    ),
                    f"acc_{window_minutes}m_not_overextended": base_structure.current_from_base_pct
                    <= self.settings.long_accumulation_max_current_from_base_pct,
                    f"acc_{window_minutes}m_not_24h_overheated": ticker.price_change_24h_pct
                    <= self.settings.long_max_24h_price_change_pct,
                    f"acc_{window_minutes}m_score": accumulation_score
                    >= self.settings.long_accumulation_min_signal_score,
                    "binance_volume": binance_volume_ok,
                }
                candidate = (
                    accumulation_score,
                    accumulation_checks,
                    acc_oi_change_pct,
                    acc_cvd_change_pct,
                    acc_cvd_delta,
                    acc_spot_cvd_change_pct,
                    acc_price_change_pct,
                    acc_spot_cvd_delta,
                    window_minutes,
                    liquidity,
                )

            best = best_candidate(best, candidate)
        return best

    def _build_watchlist_alert(
        self,
        *,
        now: int,
        ticker: Ticker,
        state: SymbolState,
        signal_score: int,
        checks: dict[str, bool],
        oi_change_pct: float,
        cvd_change_pct: float,
        cvd_delta: float,
        spot_cvd_change_pct: float,
        price_change_pct: float,
        turnover_ratio_to_base: float,
        window_minutes: int,
        liquidity: OrderbookLiquidity,
    ) -> LongWatchlistAlert | None:
        if signal_score < self.settings.long_watchlist_min_score:
            return None

        passed_checks = [name for name, passed in checks.items() if passed]
        missing_checks = [name for name, passed in checks.items() if not passed]
        if len(passed_checks) < 3:
            return None

        return LongWatchlistAlert(
            symbol=ticker.symbol,
            window_minutes=window_minutes,
            signal_score=signal_score,
            passed_checks=passed_checks,
            missing_checks=missing_checks,
            oi_change_pct=oi_change_pct,
            cvd_change_pct=cvd_change_pct,
            cvd_delta_usdt=cvd_delta,
            spot_cvd_change_pct=spot_cvd_change_pct,
            price_change_pct=price_change_pct,
            turnover_ratio_to_base=turnover_ratio_to_base,
            price=ticker.price,
            turnover_24h=ticker.turnover_24h,
            liquidity_quality=liquidity.quality,
            spread_bps=liquidity.spread_bps,
            depth_1pct_usdt=liquidity.depth_1pct_usdt,
            depth_coverage_1h=liquidity.depth_coverage_1h,
        )

    def _get_liquidity_if_near_signal(
        self,
        ticker: Ticker,
        signal_score: int,
        min_signal_score: int,
    ) -> OrderbookLiquidity:
        if signal_score < max(0, min_signal_score - 2):
            return unknown_liquidity(ticker.symbol)
        return self._get_liquidity(ticker)

    def _get_liquidity(self, ticker: Ticker) -> OrderbookLiquidity:
        if not self.settings.orderbook_enabled:
            return unknown_liquidity(ticker.symbol)

        now = int(time.time())
        cached = self.liquidity_cache.get(ticker.symbol)
        if cached is not None:
            ts, liquidity = cached
            if now - ts < max(1, self.settings.orderbook_cache_seconds):
                return liquidity

        try:
            liquidity = self.client.get_orderbook_liquidity(
                ticker.symbol,
                turnover_24h=ticker.turnover_24h,
                limit=self.settings.orderbook_limit,
                depth_pct=self.settings.orderbook_depth_pct,
            )
        except Exception as error:
            print(f"{ticker.symbol}: liquidity unavailable: {error}", flush=True)
            liquidity = unknown_liquidity(ticker.symbol)
        self.liquidity_cache[ticker.symbol] = (now, liquidity)
        return liquidity

    def _repeat_alert_rejection(
        self,
        now: int,
        state: SymbolState,
        signal_score: int,
    ) -> str | None:
        if state.last_alert_ts <= 0:
            return None

        cooldown_seconds = max(0, self.settings.alert_cooldown_minutes) * 60
        if now - state.last_alert_ts < cooldown_seconds:
            return "cooldown"

        required_score = state.last_alert_score + max(1, self.settings.alert_score_improvement)
        if signal_score < required_score:
            return "no_score_improvement"

        return None

    def _required_cvd_delta(
        self,
        ticker: Ticker,
        window_minutes: int,
        configured_min: float,
    ) -> float:
        turnover = max(0.0, ticker.turnover_24h)
        if turnover < 3_000_000:
            turnover_cap = 5_000
        elif turnover < 10_000_000:
            turnover_cap = 8_000
        elif turnover < 50_000_000:
            turnover_cap = 15_000
        else:
            turnover_cap = 30_000

        expected_window_turnover = turnover * (max(1, window_minutes) / 1440)
        flow_based_min = expected_window_turnover * 0.03
        return max(2_000, min(max(configured_min, flow_based_min), turnover_cap))

    def _get_base_structure(self, ticker: Ticker) -> BaseStructure | None:
        now = int(time.time())
        cached = self.base_cache.get(ticker.symbol)
        cache_ttl = max(1, self.settings.long_base_cache_minutes) * 60
        if cached is not None and now - cached.ts < cache_ttl:
            return BaseStructure(
                lookback_days=cached.lookback_days,
                base_growth_pct=cached.base_growth_pct,
                current_from_base_pct=pct_change(cached.base_open_price, ticker.price),
                base_high_price=cached.base_high_price,
                base_low_price=cached.base_low_price,
                base_range_pct=pct_change(cached.base_low_price, cached.base_high_price),
                base_avg_turnover=cached.base_avg_turnover,
                turnover_ratio_to_base=safe_ratio(ticker.turnover_24h, cached.base_avg_turnover),
            )

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
        base_low = min(kline.low_price for kline in base_klines)
        base_avg_turnover = sum(kline.turnover for kline in base_klines) / len(base_klines)
        base_growth_pct = pct_change(base_open, base_high)
        self.base_cache[ticker.symbol] = BaseCacheEntry(
            ts=now,
            lookback_days=len(base_klines),
            base_growth_pct=base_growth_pct,
            base_high_price=base_high,
            base_low_price=base_low,
            base_avg_turnover=base_avg_turnover,
            base_open_price=base_open,
        )
        return BaseStructure(
            lookback_days=len(base_klines),
            base_growth_pct=base_growth_pct,
            current_from_base_pct=pct_change(base_open, ticker.price),
            base_high_price=base_high,
            base_low_price=base_low,
            base_range_pct=pct_change(base_low, base_high),
            base_avg_turnover=base_avg_turnover,
            turnover_ratio_to_base=safe_ratio(ticker.turnover_24h, base_avg_turnover),
        )

    def _score_signal(
        self,
        oi_change_pct: float,
        cvd_change_pct: float,
        cvd_delta: float,
        spot_cvd_change_pct: float,
        price_change_pct: float,
        base_structure: BaseStructure,
        price_change_24h_pct: float,
    ) -> int:
        score = 0
        if base_structure.base_growth_pct <= 20:
            score += 2
        elif base_structure.base_growth_pct <= self.settings.long_max_price_growth_lookback_pct:
            score += 1

        if base_structure.base_range_pct <= self.settings.long_compression_max_base_range_pct * 0.5:
            score += 2
        elif base_structure.base_range_pct <= self.settings.long_compression_max_base_range_pct:
            score += 1

        if base_structure.turnover_ratio_to_base >= 3:
            score += 2
        elif base_structure.turnover_ratio_to_base >= self.settings.long_min_turnover_ratio_to_base:
            score += 1

        if price_change_24h_pct <= self.settings.long_max_24h_price_change_pct:
            score += 1

        if oi_change_pct >= self.settings.oi_threshold_pct * 2:
            score += 2
        elif oi_change_pct >= self.settings.oi_threshold_pct:
            score += 1

        if cvd_delta >= self.settings.min_cvd_delta_usdt * 3:
            score += 2
        elif cvd_delta >= self.settings.min_cvd_delta_usdt:
            score += 1

        if cvd_change_pct >= self.settings.cvd_threshold_pct:
            score += 1

        if spot_cvd_change_pct > 0:
            score += 1

        if price_change_pct >= self.settings.price_min_change_pct * 3:
            score += 2
        elif price_change_pct >= self.settings.price_min_change_pct:
            score += 1

        return min(score, 10)

    def _find_window_snapshot(
        self,
        now: int,
        state: SymbolState,
        window_minutes: int | None = None,
    ) -> Snapshot | None:
        window = window_minutes if window_minutes is not None else self.settings.window_minutes
        target = now - window * 60
        candidates = [snapshot for snapshot in state.snapshots if snapshot.ts <= target]
        if not candidates:
            return None
        return candidates[-1]


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 100.0 if new > 0 else 0.0
    return ((new - old) / abs(old)) * 100


def safe_ratio(value: float, base: float) -> float:
    if base <= 0:
        return 999.0 if value > 0 else 0.0
    return value / base


def candidate_rank(candidate: tuple) -> tuple[int, int]:
    score = int(candidate[0])
    checks = candidate[1]
    passed_count = sum(1 for passed in checks.values() if passed)
    return passed_count, score


def best_candidate(current: tuple | None, candidate: tuple) -> tuple:
    if current is None:
        return candidate
    if candidate_rank(candidate) > candidate_rank(current):
        return candidate
    return current


def count_reason(rejection_reasons: dict[str, int], reason: str) -> None:
    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1


def positive_flow_ok(
    cvd_delta: float,
    cvd_change_pct: float,
    min_delta_usdt: float,
    min_change_pct: float,
) -> bool:
    if cvd_delta >= min_delta_usdt:
        return True
    return cvd_delta > 0 and cvd_change_pct >= min_change_pct
