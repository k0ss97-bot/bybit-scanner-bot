from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from typing import Any

from binance_client import BinanceClient, BinanceTicker, OpenInterestPoint
from bybit_client import BybitClient, Ticker
from config import Settings
from history import HistoryStore
from state import Snapshot, StateStore, SymbolState


SYMBOL_ALERT_LOCK = threading.Lock()
SYMBOL_ALERTS: dict[str, tuple[int, str, int]] = {}
MARKET_EVIDENCE_LOCK = threading.Lock()
MARKET_EVIDENCE: dict[tuple[str, str], "MarketEvidence"] = {}
DEEP_CANDIDATES_LOCK = threading.Lock()
BINANCE_DEEP_CANDIDATES: set[str] = set()


@dataclass(frozen=True)
class DumpStructureCacheEntry:
    ts: int
    price_growth_lookback_pct: float
    high_price: float


@dataclass(frozen=True)
class DumpTimeframeMetrics:
    label: str
    minutes: int
    price_change_pct: float | None
    oi_change_pct: float | None
    cvd_delta_usdt: float | None


@dataclass(frozen=True)
class DumpSignal:
    source: str
    mode: str
    confirmation_source: str
    symbol: str
    window_minutes: int
    lookback_days: int
    signal_score: int
    price_growth_lookback_pct: float
    drawdown_from_high_pct: float
    oi_change_pct: float
    cvd_delta_usdt: float
    price_change_window_pct: float
    funding_rate: float
    price: float
    high_price: float
    turnover_24h: float
    new_trades: int
    consecutive_matches: int
    confirmation_price_change_pct: float
    confirmation_oi_change_pct: float
    confirmation_cvd_delta_usdt: float
    timeframes: tuple[DumpTimeframeMetrics, ...] = ()


@dataclass(frozen=True)
class MarketEvidence:
    source: str
    symbol: str
    ts: int
    mode: str
    price_change_pct: float
    oi_change_pct: float
    cvd_delta_usdt: float
    cvd_complete: bool


@dataclass(frozen=True)
class DumpWatchlistAlert:
    source: str
    symbol: str
    window_minutes: int
    signal_score: int
    passed_checks: list[str]
    missing_checks: list[str]
    price_growth_lookback_pct: float
    drawdown_from_high_pct: float
    oi_change_pct: float
    cvd_delta_usdt: float
    price_change_window_pct: float
    price: float
    turnover_24h: float


@dataclass(frozen=True)
class DumpScanResult:
    signals: list[DumpSignal]
    watchlist_alerts: list[DumpWatchlistAlert]
    scanned_symbols: int
    screened_symbols: int
    failed_symbols: int
    skipped_symbols: int
    rejection_reasons: dict[str, int]


class DumpScanner:
    def __init__(
        self,
        source: str,
        client: BybitClient | BinanceClient,
        store: StateStore,
        settings: Settings,
        history: HistoryStore | None = None,
        allowed_symbols_provider: Callable[[], set[str]] | None = None,
    ) -> None:
        self.source = source.upper()
        self.client = client
        self.store = store
        self.settings = settings
        self.history = history
        self.allowed_symbols_provider = allowed_symbols_provider
        self.structure_cache: dict[str, DumpStructureCacheEntry] = {}

    def scan_once(self, progress_callback=None) -> DumpScanResult:
        now = int(time.time())
        rejection_reasons: dict[str, int] = {}
        screened_tickers = self._select_tickers(now, rejection_reasons)
        tickers = self._select_deep_candidates(now, screened_tickers, rejection_reasons)
        if progress_callback is not None:
            progress_callback(0, len(tickers))

        signals: list[DumpSignal] = []
        watchlist_alerts: list[DumpWatchlistAlert] = []
        failed_symbols = 0
        skipped_symbols = 0

        for index, (source_rank, ticker) in enumerate(tickers, start=1):
            try:
                state = self.store.get_symbol(ticker.symbol)
                open_interest = self._get_open_interest(ticker)
                new_trades, cvd_complete = self._update_cvd(ticker.symbol, state)
                self._add_snapshot(now, ticker, open_interest, state, new_trades)

                signal, watchlist_alert = self._build_signal(
                    now,
                    ticker,
                    state,
                    rejection_reasons,
                    source_rank,
                    cvd_complete,
                )
                if signal is not None:
                    signals.append(signal)
                    state.consecutive_matches = 0
                elif watchlist_alert is not None:
                    if self.history is not None:
                        self.history.record_watchlist_candidate(
                            scanner=f"dump_{self.source.lower()}",
                            symbol=self._history_symbol(watchlist_alert.symbol),
                            score=watchlist_alert.signal_score,
                            price=watchlist_alert.price,
                            passed_checks=watchlist_alert.passed_checks,
                            missing_checks=watchlist_alert.missing_checks,
                            payload=str(watchlist_alert),
                            ts=now,
                        )
                    watchlist_alerts.append(watchlist_alert)
                else:
                    skipped_symbols += 1
            except Exception as error:
                if self._is_symbol_unavailable_error(error):
                    skipped_symbols += 1
                    count_reason(rejection_reasons, "symbol_unavailable")
                else:
                    failed_symbols += 1
                    print(f"{self.source} {ticker.symbol}: dump scan failed: {error}", flush=True)
            finally:
                if progress_callback is not None and (index % 10 == 0 or index == len(tickers)):
                    progress_callback(index, len(tickers))

        self.store.save()
        return DumpScanResult(
            signals=signals,
            watchlist_alerts=watchlist_alerts,
            scanned_symbols=len(tickers),
            screened_symbols=len(screened_tickers),
            failed_symbols=failed_symbols,
            skipped_symbols=skipped_symbols,
            rejection_reasons=rejection_reasons,
        )

    def _select_deep_candidates(
        self,
        now: int,
        tickers: list[tuple[int, Any]],
        rejection_reasons: dict[str, int],
    ) -> list[tuple[int, Any]]:
        eligible: list[tuple[int, Any]] = []
        for source_rank, ticker in tickers:
            try:
                structure = self._get_dump_structure(ticker.symbol, ticker.price)
            except Exception as error:
                if self._is_symbol_unavailable_error(error):
                    count_reason(rejection_reasons, "symbol_unavailable")
                else:
                    count_reason(rejection_reasons, "prefilter_failed")
                continue
            if structure is None:
                count_reason(rejection_reasons, "no_dump_structure")
                continue
            growth, drawdown, _ = structure
            if growth < self.settings.dump_min_price_growth_lookback_pct:
                count_reason(rejection_reasons, "prefilter_prior_pump")
                continue
            if drawdown > -self.settings.dump_min_drawdown_from_high_pct:
                count_reason(rejection_reasons, "prefilter_drawdown")
                continue
            eligible.append((source_rank, ticker))

        if self.source == "BYBIT":
            with DEEP_CANDIDATES_LOCK:
                primary_symbols = set(BINANCE_DEEP_CANDIDATES)
            primary = [item for item in eligible if item[1].symbol in primary_symbols]
            remaining = [item for item in eligible if item[1].symbol not in primary_symbols]
            selected = (primary + remaining)[: self.settings.dump_deep_max_symbols]
        else:
            selected = eligible[: self.settings.dump_deep_max_symbols]
            with DEEP_CANDIDATES_LOCK:
                BINANCE_DEEP_CANDIDATES.clear()
                BINANCE_DEEP_CANDIDATES.update(ticker.symbol for _, ticker in selected)

        selected_symbols = {ticker.symbol for _, ticker in selected}
        for source_rank, ticker in eligible:
            if ticker.symbol in selected_symbols:
                continue
            count_reason(rejection_reasons, "outside_deep_shortlist")
            self._record_selection_evaluation(
                now=now,
                ticker=ticker,
                source_rank=source_rank,
                status="outside_deep_shortlist",
                reason=f"outside_deep_{self.settings.dump_deep_max_symbols}",
                missing_checks=[f"deep_top_{self.settings.dump_deep_max_symbols}"],
            )
        return selected

    def _select_tickers(
        self,
        now: int,
        rejection_reasons: dict[str, int],
    ) -> list[tuple[int, Any]]:
        if isinstance(self.client, BinanceClient):
            raw_tickers = list(self.client.get_usdt_perp_tickers().values())
            raw_tickers.sort(key=lambda item: item.quote_volume_24h, reverse=True)
            allowed_symbols = self._allowed_symbols()
            if allowed_symbols is not None and not allowed_symbols:
                count_reason(rejection_reasons, "bybit_listing_unavailable")
                return []

            ranked_tickers: list[tuple[int, Any]] = []
            not_bybit_count = 0
            for source_rank, ticker in enumerate(raw_tickers, start=1):
                if not self._has_minimum_market_data(ticker):
                    continue
                if allowed_symbols is not None and ticker.symbol not in allowed_symbols:
                    not_bybit_count += 1
                    if not_bybit_count <= self.settings.dump_max_evaluation_symbols:
                        self._record_selection_evaluation(
                            now=now,
                            ticker=ticker,
                            source_rank=source_rank,
                            status="not_on_bybit",
                            reason="not_tradable_on_bybit",
                            missing_checks=["bybit_listing"],
                        )
                    continue
                ranked_tickers.append((source_rank, ticker))

            if not_bybit_count:
                rejection_reasons["not_on_bybit"] = (
                    rejection_reasons.get("not_on_bybit", 0) + not_bybit_count
                )
            return self._select_top_ranked(now, ranked_tickers, rejection_reasons)

        raw_tickers = list(self.client.get_linear_tickers())
        raw_tickers.sort(key=lambda item: item.turnover_24h, reverse=True)
        ranked_tickers = [
            (source_rank, ticker)
            for source_rank, ticker in enumerate(raw_tickers, start=1)
            if self._has_minimum_market_data(ticker)
            and getattr(ticker, "open_interest", 0) > 0
        ]
        return self._select_top_ranked(now, ranked_tickers, rejection_reasons)

    def _select_top_ranked(
        self,
        now: int,
        ranked_tickers: list[tuple[int, Any]],
        rejection_reasons: dict[str, int],
    ) -> list[tuple[int, Any]]:
        selected = ranked_tickers[: self.settings.dump_max_symbols]
        if self.history is not None:
            self.history.record_pending_signal_prices(
                signal_type=f"dump_{self.source.lower()}",
                prices={ticker.symbol: ticker.price for _, ticker in ranked_tickers},
                ts=now,
            )
        outside = ranked_tickers[self.settings.dump_max_symbols :]
        if outside:
            rejection_reasons["outside_top_symbols"] = (
                rejection_reasons.get("outside_top_symbols", 0) + len(outside)
            )
            for source_rank, ticker in outside[: self.settings.dump_max_evaluation_symbols]:
                self._record_selection_evaluation(
                    now=now,
                    ticker=ticker,
                    source_rank=source_rank,
                    status="outside_top_symbols",
                    reason=f"outside_top_{self.settings.dump_max_symbols}",
                    missing_checks=[f"top_{self.settings.dump_max_symbols}"],
                )
        return selected

    def _allowed_symbols(self) -> set[str] | None:
        if self.allowed_symbols_provider is None:
            return None
        return set(self.allowed_symbols_provider())

    def _has_minimum_market_data(self, ticker: Ticker | BinanceTicker) -> bool:
        return (
            self._turnover(ticker) >= self.settings.dump_min_turnover_24h_usdt
            and ticker.price > 0
            and ticker.high_price_24h > 0
        )

    def _record_selection_evaluation(
        self,
        *,
        now: int,
        ticker: Ticker | BinanceTicker,
        source_rank: int,
        status: str,
        reason: str,
        missing_checks: list[str],
    ) -> None:
        self._record_signal_evaluation(
            now=now,
            ticker=ticker,
            source_rank=source_rank,
            status=status,
            reason=reason,
            score=0,
            selected=False,
            turnover_24h=self._turnover(ticker),
            funding_rate=ticker.funding_rate,
            passed_checks=[],
            missing_checks=missing_checks,
        )

    def _record_signal_evaluation(
        self,
        *,
        now: int,
        ticker: Ticker | BinanceTicker,
        source_rank: int,
        status: str,
        reason: str,
        score: int,
        turnover_24h: float,
        selected: bool = True,
        price_growth_lookback_pct: float = 0.0,
        drawdown_from_high_pct: float = 0.0,
        oi_change_pct: float = 0.0,
        cvd_delta_usdt: float = 0.0,
        price_change_window_pct: float = 0.0,
        funding_rate: float = 0.0,
        passed_checks: list[str] | None = None,
        missing_checks: list[str] | None = None,
        payload: str = "",
    ) -> None:
        if self.history is None or not self.settings.dump_evaluation_enabled:
            return
        try:
            self.history.record_scanner_evaluation(
                scanner=f"dump_{self.source.lower()}",
                source=self.source,
                symbol=ticker.symbol,
                ts=now,
                source_rank=source_rank,
                selected=selected,
                status=status,
                reason=reason,
                score=score,
                price=ticker.price,
                turnover_24h=turnover_24h,
                price_growth_lookback_pct=price_growth_lookback_pct,
                drawdown_from_high_pct=drawdown_from_high_pct,
                oi_change_pct=oi_change_pct,
                cvd_delta_usdt=cvd_delta_usdt,
                price_change_window_pct=price_change_window_pct,
                funding_rate=funding_rate,
                passed_checks=passed_checks,
                missing_checks=missing_checks,
                payload=payload,
            )
        except Exception as error:
            print(f"{self.source} {ticker.symbol}: evaluation write failed: {error}", flush=True)

    def _get_open_interest(self, ticker: Ticker | BinanceTicker) -> float:
        if isinstance(self.client, BinanceClient):
            return self.client.get_open_interest(ticker.symbol)
        return float(ticker.open_interest)

    def _update_cvd(self, symbol: str, state: SymbolState) -> tuple[int, bool]:
        migrating_legacy_state = not state.last_trade_id and bool(state.snapshots)
        batch = self.client.get_trades_since(
            symbol,
            last_trade_id=state.last_trade_id,
            last_time_ms=state.last_trade_time_ms,
            limit=1000,
            max_pages=self.settings.dump_trade_max_pages,
        )
        new_trades = batch.trades
        if migrating_legacy_state or not batch.complete:
            state.cumulative_cvd = 0.0
            state.cvd_generation += 1
        for trade in new_trades:
            state.cumulative_cvd += trade.signed_notional
        if new_trades:
            latest = max(new_trades, key=lambda trade: (trade.time_ms, trade.exec_id))
            state.last_trade_id = latest.exec_id
            state.last_trade_time_ms = latest.time_ms
        return len(new_trades), batch.complete

    def _add_snapshot(
        self,
        now: int,
        ticker: Ticker | BinanceTicker,
        open_interest: float,
        state: SymbolState,
        new_trades: int,
    ) -> None:
        state.snapshots.append(
            Snapshot(
                ts=now,
                oi=open_interest,
                cvd=state.cumulative_cvd,
                price=ticker.price,
                funding=ticker.funding_rate,
                turnover_24h=self._turnover(ticker),
                new_trades=new_trades,
                cvd_generation=state.cvd_generation,
            )
        )
        if self.history is not None:
            self.history.record_snapshot(
                scanner=f"dump_{self.source.lower()}",
                symbol=self._history_symbol(ticker.symbol),
                ts=now,
                price=ticker.price,
                open_interest=open_interest,
                futures_cvd=state.cumulative_cvd,
                spot_cvd=0,
                funding=ticker.funding_rate,
                turnover_24h=self._turnover(ticker),
                new_futures_trades=new_trades,
                new_spot_trades=0,
            )
        min_ts = now - self.settings.dump_window_minutes * 60 * 3
        state.snapshots = [snapshot for snapshot in state.snapshots if snapshot.ts >= min_ts]

    def _build_signal(
        self,
        now: int,
        ticker: Ticker | BinanceTicker,
        state: SymbolState,
        rejection_reasons: dict[str, int],
        source_rank: int,
        cvd_complete: bool,
    ) -> tuple[DumpSignal | None, DumpWatchlistAlert | None]:
        current = state.snapshots[-1]
        previous = self._find_window_snapshot(current.ts, state)
        if previous is None:
            count_reason(rejection_reasons, "warmup_no_window")
            self._record_signal_evaluation(
                now=now,
                ticker=ticker,
                source_rank=source_rank,
                status="warmup",
                reason="warmup_no_window",
                score=0,
                turnover_24h=self._turnover(ticker),
                funding_rate=current.funding,
                missing_checks=["window_snapshot"],
            )
            return None, None

        structure = self._get_dump_structure(ticker.symbol, ticker.price)
        if structure is None:
            count_reason(rejection_reasons, "no_dump_structure")
            self._record_signal_evaluation(
                now=now,
                ticker=ticker,
                source_rank=source_rank,
                status="rejected",
                reason="no_dump_structure",
                score=0,
                turnover_24h=self._turnover(ticker),
                funding_rate=current.funding,
                missing_checks=["dump_structure"],
            )
            return None, None

        price_growth_lookback_pct, drawdown_from_high_pct, high_price = structure
        oi_change_pct = pct_change(previous.oi, current.oi)
        cvd_delta = current.cvd - previous.cvd
        price_change_window_pct = pct_change(previous.price, current.price)
        turnover_24h = self._turnover(ticker)
        mode = self._signal_mode(oi_change_pct)
        evidence = MarketEvidence(
            source=self.source,
            symbol=ticker.symbol,
            ts=now,
            mode=mode,
            price_change_pct=price_change_window_pct,
            oi_change_pct=oi_change_pct,
            cvd_delta_usdt=cvd_delta,
            cvd_complete=cvd_complete,
        )
        partner = self._publish_and_get_partner(evidence)
        cross_exchange_ok = self._cross_exchange_ok(evidence, partner)

        checks = {
            "volume": turnover_24h >= self.settings.dump_min_turnover_24h_usdt,
            "prior_pump": price_growth_lookback_pct
            >= self.settings.dump_min_price_growth_lookback_pct,
            "drawdown_from_high": drawdown_from_high_pct
            <= -self.settings.dump_min_drawdown_from_high_pct,
            "price_dropping": price_change_window_pct <= -self.settings.dump_min_price_drop_window_pct,
            "sell_cvd": cvd_delta <= -self.settings.dump_min_negative_cvd_delta_usdt,
            "cvd_complete": cvd_complete,
            "dump_mode": mode != "UNCONFIRMED_OI",
            "cross_exchange": cross_exchange_ok,
        }
        soft_checks = {
            "oi_not_collapsed": oi_change_pct >= -self.settings.dump_max_oi_drop_window_pct,
            "funding_not_positive": current.funding <= self.settings.dump_max_funding_rate,
        }
        signal_score = self._score_signal(
            price_growth_lookback_pct=price_growth_lookback_pct,
            drawdown_from_high_pct=drawdown_from_high_pct,
            oi_change_pct=oi_change_pct,
            cvd_delta=cvd_delta,
            price_change_window_pct=price_change_window_pct,
            funding_rate=current.funding,
        )

        if not all(checks.values()) or signal_score < self.settings.dump_min_signal_score:
            state.consecutive_matches = 0
            for reason, passed in checks.items():
                if not passed:
                    count_reason(rejection_reasons, reason)
            if signal_score < self.settings.dump_min_signal_score:
                count_reason(rejection_reasons, "score")
            watchlist_alert = self._build_watchlist_alert(
                ticker=ticker,
                signal_score=signal_score,
                checks={**checks, **soft_checks},
                price_growth_lookback_pct=price_growth_lookback_pct,
                drawdown_from_high_pct=drawdown_from_high_pct,
                oi_change_pct=oi_change_pct,
                cvd_delta=cvd_delta,
                price_change_window_pct=price_change_window_pct,
                turnover_24h=turnover_24h,
            )
            missing_checks = [name for name, passed in {**checks, **soft_checks}.items() if not passed]
            if signal_score < self.settings.dump_min_signal_score:
                missing_checks.append("score")
            self._record_signal_evaluation(
                now=now,
                ticker=ticker,
                source_rank=source_rank,
                status="watchlist" if watchlist_alert is not None else "rejected",
                reason=",".join(missing_checks) or "checks_failed",
                score=signal_score,
                turnover_24h=turnover_24h,
                price_growth_lookback_pct=price_growth_lookback_pct,
                drawdown_from_high_pct=drawdown_from_high_pct,
                oi_change_pct=oi_change_pct,
                cvd_delta_usdt=cvd_delta,
                price_change_window_pct=price_change_window_pct,
                funding_rate=current.funding,
                passed_checks=[
                    name for name, passed in {**checks, **soft_checks}.items() if passed
                ],
                missing_checks=missing_checks,
            )
            return None, watchlist_alert

        state.consecutive_matches += 1
        if state.consecutive_matches < self.settings.dump_consecutive_checks:
            count_reason(rejection_reasons, "confirmations_waiting")
            self._record_signal_evaluation(
                now=now,
                ticker=ticker,
                source_rank=source_rank,
                status="confirming",
                reason="confirmations_waiting",
                score=signal_score,
                turnover_24h=turnover_24h,
                price_growth_lookback_pct=price_growth_lookback_pct,
                drawdown_from_high_pct=drawdown_from_high_pct,
                oi_change_pct=oi_change_pct,
                cvd_delta_usdt=cvd_delta,
                price_change_window_pct=price_change_window_pct,
                funding_rate=current.funding,
                passed_checks=[name for name, passed in {**checks, **soft_checks}.items() if passed],
                missing_checks=["confirmations_waiting"],
            )
            return None, None
        if not self._claim_symbol_alert(now, ticker.symbol, signal_score, rejection_reasons):
            self._record_signal_evaluation(
                now=now,
                ticker=ticker,
                source_rank=source_rank,
                status="cooldown",
                reason="symbol_cooldown",
                score=signal_score,
                turnover_24h=turnover_24h,
                price_growth_lookback_pct=price_growth_lookback_pct,
                drawdown_from_high_pct=drawdown_from_high_pct,
                oi_change_pct=oi_change_pct,
                cvd_delta_usdt=cvd_delta,
                price_change_window_pct=price_change_window_pct,
                funding_rate=current.funding,
                passed_checks=[name for name, passed in {**checks, **soft_checks}.items() if passed],
                missing_checks=["symbol_cooldown"],
            )
            return None, None

        signal = DumpSignal(
            source="BINANCE+BYBIT" if partner is not None else self.source,
            mode=mode,
            confirmation_source=partner.source if partner is not None else "",
            symbol=ticker.symbol,
            window_minutes=self.settings.dump_window_minutes,
            lookback_days=self.settings.dump_lookback_days,
            signal_score=signal_score,
            price_growth_lookback_pct=price_growth_lookback_pct,
            drawdown_from_high_pct=drawdown_from_high_pct,
            oi_change_pct=oi_change_pct,
            cvd_delta_usdt=cvd_delta,
            price_change_window_pct=price_change_window_pct,
            funding_rate=current.funding,
            price=ticker.price,
            high_price=high_price,
            turnover_24h=turnover_24h,
            new_trades=current.new_trades,
            consecutive_matches=state.consecutive_matches,
            confirmation_price_change_pct=partner.price_change_pct if partner is not None else 0.0,
            confirmation_oi_change_pct=partner.oi_change_pct if partner is not None else 0.0,
            confirmation_cvd_delta_usdt=partner.cvd_delta_usdt if partner is not None else 0.0,
            timeframes=self._load_timeframe_metrics(
                ticker=ticker,
                price_change_1h=price_change_window_pct,
                oi_change_1h=oi_change_pct,
                cvd_delta_1h=cvd_delta,
            ),
        )
        self._record_signal_evaluation(
            now=now,
            ticker=ticker,
            source_rank=source_rank,
            status="signal",
            reason="signal",
            score=signal_score,
            turnover_24h=turnover_24h,
            price_growth_lookback_pct=price_growth_lookback_pct,
            drawdown_from_high_pct=drawdown_from_high_pct,
            oi_change_pct=oi_change_pct,
            cvd_delta_usdt=cvd_delta,
            price_change_window_pct=price_change_window_pct,
            funding_rate=current.funding,
            passed_checks=[name for name, passed in {**checks, **soft_checks}.items() if passed],
            missing_checks=[],
            payload=str(signal),
        )
        return signal, None

    def _build_watchlist_alert(
        self,
        *,
        ticker: Ticker | BinanceTicker,
        signal_score: int,
        checks: dict[str, bool],
        price_growth_lookback_pct: float,
        drawdown_from_high_pct: float,
        oi_change_pct: float,
        cvd_delta: float,
        price_change_window_pct: float,
        turnover_24h: float,
    ) -> DumpWatchlistAlert | None:
        if signal_score < self.settings.dump_watchlist_min_score:
            return None
        passed_checks = [name for name, passed in checks.items() if passed]
        missing_checks = [name for name, passed in checks.items() if not passed]
        if len(passed_checks) < 3:
            return None
        return DumpWatchlistAlert(
            source=self.source,
            symbol=ticker.symbol,
            window_minutes=self.settings.dump_window_minutes,
            signal_score=signal_score,
            passed_checks=passed_checks,
            missing_checks=missing_checks,
            price_growth_lookback_pct=price_growth_lookback_pct,
            drawdown_from_high_pct=drawdown_from_high_pct,
            oi_change_pct=oi_change_pct,
            cvd_delta_usdt=cvd_delta,
            price_change_window_pct=price_change_window_pct,
            price=ticker.price,
            turnover_24h=turnover_24h,
        )

    def _score_signal(
        self,
        *,
        price_growth_lookback_pct: float,
        drawdown_from_high_pct: float,
        oi_change_pct: float,
        cvd_delta: float,
        price_change_window_pct: float,
        funding_rate: float,
    ) -> int:
        score = 0
        if price_growth_lookback_pct >= self.settings.dump_min_price_growth_lookback_pct * 2:
            score += 2
        elif price_growth_lookback_pct >= self.settings.dump_min_price_growth_lookback_pct:
            score += 1

        drawdown = abs(min(0, drawdown_from_high_pct))
        if drawdown >= self.settings.dump_min_drawdown_from_high_pct * 2:
            score += 2
        elif drawdown >= self.settings.dump_min_drawdown_from_high_pct:
            score += 1

        if price_change_window_pct <= -self.settings.dump_min_price_drop_window_pct * 2:
            score += 2
        elif price_change_window_pct <= -self.settings.dump_min_price_drop_window_pct:
            score += 1

        if cvd_delta <= -self.settings.dump_min_negative_cvd_delta_usdt * 3:
            score += 2
        elif cvd_delta <= -self.settings.dump_min_negative_cvd_delta_usdt:
            score += 1

        if oi_change_pct >= 0:
            score += 2
        elif oi_change_pct >= -self.settings.dump_max_oi_drop_window_pct:
            score += 1

        if funding_rate <= self.settings.dump_max_funding_rate:
            score += 1

        return min(score, 10)

    def _get_dump_structure(self, symbol: str, price: float) -> tuple[float, float, float] | None:
        now = int(time.time())
        cache_ttl = max(1, self.settings.dump_structure_cache_minutes) * 60
        cached = self.structure_cache.get(symbol)
        if cached is not None and now - cached.ts < cache_ttl:
            return (
                cached.price_growth_lookback_pct,
                pct_change(cached.high_price, price),
                cached.high_price,
            )

        limit = max(3, self.settings.dump_lookback_days + 1)
        klines = self.client.get_daily_klines(symbol, limit=limit)
        if len(klines) < self.settings.dump_lookback_days:
            return None

        lookback = klines[-self.settings.dump_lookback_days :]
        base_open = lookback[0].open_price
        high_price = max(kline.high_price for kline in lookback)
        if base_open <= 0 or high_price <= 0:
            return None
        price_growth_lookback_pct = pct_change(base_open, high_price)
        self.structure_cache[symbol] = DumpStructureCacheEntry(
            ts=now,
            price_growth_lookback_pct=price_growth_lookback_pct,
            high_price=high_price,
        )
        return price_growth_lookback_pct, pct_change(high_price, price), high_price

    def _find_window_snapshot(self, now: int, state: SymbolState) -> Snapshot | None:
        target = now - self.settings.dump_window_minutes * 60
        candidates = [
            snapshot
            for snapshot in state.snapshots
            if snapshot.ts <= target and snapshot.cvd_generation == state.cvd_generation
        ]
        if not candidates:
            return None
        return candidates[-1]

    def _load_timeframe_metrics(
        self,
        *,
        ticker: Ticker | BinanceTicker,
        price_change_1h: float,
        oi_change_1h: float,
        cvd_delta_1h: float,
    ) -> tuple[DumpTimeframeMetrics, ...]:
        metrics = [
            DumpTimeframeMetrics(
                label="1H",
                minutes=60,
                price_change_pct=price_change_1h,
                oi_change_pct=oi_change_1h,
                cvd_delta_usdt=cvd_delta_1h,
            )
        ]
        klines = []
        oi_points: list[OpenInterestPoint] = []
        if self.source == "BINANCE":
            try:
                klines = self.client.get_klines(ticker.symbol, interval="1h", limit=30)
            except Exception as error:
                print(
                    f"BINANCE {ticker.symbol}: timeframe klines unavailable: {error}",
                    flush=True,
                )
            try:
                oi_points = self.client.get_open_interest_history(
                    ticker.symbol,
                    period="1h",
                    limit=30,
                )
            except Exception as error:
                print(
                    f"BINANCE {ticker.symbol}: timeframe OI unavailable: {error}",
                    flush=True,
                )

        now_ms = int(time.time() * 1000)
        for label, hours in (("4H", 4), ("1D", 24)):
            price_change_pct, cvd_delta_usdt = self._aggregate_hourly_klines(
                klines,
                hours=hours,
                now_ms=now_ms,
            )
            oi_change_pct = self._aggregate_open_interest(oi_points, hours=hours)
            if label == "1D" and price_change_pct is None:
                price_change_pct = getattr(ticker, "price_change_24h_pct", None)
            metrics.append(
                DumpTimeframeMetrics(
                    label=label,
                    minutes=hours * 60,
                    price_change_pct=price_change_pct,
                    oi_change_pct=oi_change_pct,
                    cvd_delta_usdt=cvd_delta_usdt,
                )
            )
        return tuple(metrics)

    @staticmethod
    def _aggregate_hourly_klines(
        klines,
        *,
        hours: int,
        now_ms: int,
    ) -> tuple[float | None, float | None]:
        closed = [
            kline
            for kline in klines
            if kline.start_ms + 60 * 60_000 <= now_ms
        ]
        if len(closed) < hours:
            return None, None
        window = closed[-hours:]
        price_change_pct = pct_change(window[0].open_price, window[-1].close_price)
        if any(kline.taker_buy_turnover is None for kline in window):
            cvd_delta_usdt = None
        else:
            cvd_delta_usdt = sum(
                2 * float(kline.taker_buy_turnover) - kline.turnover
                for kline in window
            )
        return price_change_pct, cvd_delta_usdt

    @staticmethod
    def _aggregate_open_interest(
        points: list[OpenInterestPoint],
        *,
        hours: int,
    ) -> float | None:
        valid = [point for point in points if point.open_interest > 0]
        if len(valid) <= hours:
            return None
        return pct_change(valid[-hours - 1].open_interest, valid[-1].open_interest)

    def _signal_mode(self, oi_change_pct: float) -> str:
        if oi_change_pct <= -self.settings.dump_liquidation_min_oi_drop_pct:
            return "LIQUIDATION_FLUSH"
        if oi_change_pct >= self.settings.dump_trend_min_oi_change_pct:
            return "SHORT_TREND"
        return "UNCONFIRMED_OI"

    def _publish_and_get_partner(self, evidence: MarketEvidence) -> MarketEvidence | None:
        with MARKET_EVIDENCE_LOCK:
            MARKET_EVIDENCE[(evidence.source, evidence.symbol)] = evidence
            partner_source = "BYBIT" if evidence.source == "BINANCE" else "BINANCE"
            return MARKET_EVIDENCE.get((partner_source, evidence.symbol))

    def _cross_exchange_ok(
        self,
        evidence: MarketEvidence,
        partner: MarketEvidence | None,
    ) -> bool:
        if not self.settings.dump_cross_exchange_required:
            return True
        # Binance is the primary market-data source; Bybit confirms tradability and direction.
        if evidence.source != "BINANCE" or partner is None or partner.source != "BYBIT":
            return False
        if abs(evidence.ts - partner.ts) > self.settings.dump_cross_exchange_max_age_seconds:
            return False
        if evidence.mode == "UNCONFIRMED_OI":
            return False
        if not evidence.cvd_complete or not partner.cvd_complete:
            return False
        return (
            partner.price_change_pct <= -self.settings.dump_min_price_drop_window_pct
            and partner.cvd_delta_usdt < 0
        )

    def _turnover(self, ticker: Ticker | BinanceTicker) -> float:
        return getattr(ticker, "turnover_24h", 0) or getattr(ticker, "quote_volume_24h", 0)

    def _history_symbol(self, symbol: str) -> str:
        return f"{self.source}:{symbol}"

    def _is_symbol_unavailable_error(self, error: Exception) -> bool:
        text = str(error).lower()
        return self.source == "BINANCE" and (
            "http error 400" in text
            or "invalid symbol" in text
            or "bad request" in text
        )

    def _claim_symbol_alert(
        self,
        now: int,
        symbol: str,
        signal_score: int,
        rejection_reasons: dict[str, int],
    ) -> bool:
        cooldown_seconds = self.settings.dump_symbol_cooldown_minutes * 60
        if cooldown_seconds <= 0:
            return True

        if self.history is not None:
            claimed, previous_source, previous_ts, previous_score = self.history.claim_dump_symbol_alert(
                symbol=symbol,
                ts=now,
                source=self.source,
                score=signal_score,
                cooldown_minutes=self.settings.dump_symbol_cooldown_minutes,
            )
            if claimed:
                return True
            count_reason(rejection_reasons, "symbol_cooldown")
            age_seconds = now - int(previous_ts or now)
            print(
                f"{self.source} {symbol}: skipped by persistent dump cooldown "
                f"after {previous_source} score={previous_score} age={age_seconds}s",
                flush=True,
            )
            return False

        with SYMBOL_ALERT_LOCK:
            previous = SYMBOL_ALERTS.get(symbol)
            if previous is not None:
                previous_ts, previous_source, previous_score = previous
                if now - previous_ts < cooldown_seconds:
                    count_reason(rejection_reasons, "symbol_cooldown")
                    print(
                        f"{self.source} {symbol}: skipped by shared dump cooldown "
                        f"after {previous_source} score={previous_score}",
                        flush=True,
                    )
                    return False

            SYMBOL_ALERTS[symbol] = (now, self.source, signal_score)
            return True


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / abs(old)) * 100


def count_reason(rejection_reasons: dict[str, int], reason: str) -> None:
    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
