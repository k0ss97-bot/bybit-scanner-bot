from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any

from binance_client import BinanceClient, BinanceTicker
from bybit_client import BybitClient, Ticker
from config import Settings
from history import HistoryStore
from state import Snapshot, StateStore, SymbolState


SYMBOL_ALERT_LOCK = threading.Lock()
SYMBOL_ALERTS: dict[str, tuple[int, str, int]] = {}


@dataclass(frozen=True)
class DumpSignal:
    source: str
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
    ) -> None:
        self.source = source.upper()
        self.client = client
        self.store = store
        self.settings = settings
        self.history = history

    def scan_once(self, progress_callback=None) -> DumpScanResult:
        now = int(time.time())
        tickers = self._select_tickers()
        if progress_callback is not None:
            progress_callback(0, len(tickers))

        signals: list[DumpSignal] = []
        watchlist_alerts: list[DumpWatchlistAlert] = []
        failed_symbols = 0
        skipped_symbols = 0
        rejection_reasons: dict[str, int] = {}

        for index, ticker in enumerate(tickers, start=1):
            try:
                state = self.store.get_symbol(ticker.symbol)
                open_interest = self._get_open_interest(ticker)
                new_trades = self._update_cvd(ticker.symbol, state)
                self._add_snapshot(now, ticker, open_interest, state, new_trades)

                signal, watchlist_alert = self._build_signal(now, ticker, state, rejection_reasons)
                if signal is not None:
                    state.last_alert_ts = now
                    state.last_alert_score = signal.signal_score
                    if self.history is not None:
                        self.history.record_signal(
                            signal_type=f"dump_{self.source.lower()}",
                            symbol=self._history_symbol(signal.symbol),
                            ts=now,
                            price=signal.price,
                            open_interest_change_pct=signal.oi_change_pct,
                            futures_cvd_change_pct=0,
                            futures_cvd_delta_usdt=signal.cvd_delta_usdt,
                            spot_cvd_change_pct=0,
                            spot_cvd_delta_usdt=0,
                            price_change_pct=signal.price_change_window_pct,
                            payload=str(signal),
                        )
                    signals.append(signal)
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
            failed_symbols=failed_symbols,
            skipped_symbols=skipped_symbols,
            rejection_reasons=rejection_reasons,
        )

    def _select_tickers(self) -> list[Any]:
        if isinstance(self.client, BinanceClient):
            tickers = [
                ticker
                for ticker in self.client.get_usdt_perp_tickers().values()
                if ticker.quote_volume_24h >= self.settings.dump_min_turnover_24h_usdt
                and ticker.price > 0
                and ticker.high_price_24h > 0
            ]
            tickers.sort(key=lambda item: item.quote_volume_24h, reverse=True)
            return tickers[: self.settings.dump_max_symbols]

        tickers = [
            ticker
            for ticker in self.client.get_linear_tickers()
            if ticker.turnover_24h >= self.settings.dump_min_turnover_24h_usdt
            and ticker.open_interest > 0
            and ticker.price > 0
            and ticker.high_price_24h > 0
        ]
        tickers.sort(key=lambda item: item.turnover_24h, reverse=True)
        return tickers[: self.settings.dump_max_symbols]

    def _get_open_interest(self, ticker: Ticker | BinanceTicker) -> float:
        if isinstance(self.client, BinanceClient):
            return self.client.get_open_interest(ticker.symbol)
        return float(ticker.open_interest)

    def _update_cvd(self, symbol: str, state: SymbolState) -> int:
        trades = self.client.get_recent_trades(symbol, limit=1000)
        seen = set(state.seen_trade_ids)
        new_trades = [trade for trade in trades if trade.exec_id not in seen]
        for trade in new_trades:
            state.cumulative_cvd += trade.signed_notional
        recent_ids = [trade.exec_id for trade in new_trades] + state.seen_trade_ids
        state.seen_trade_ids = recent_ids[:3000]
        return len(new_trades)

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
    ) -> tuple[DumpSignal | None, DumpWatchlistAlert | None]:
        current = state.snapshots[-1]
        previous = self._find_window_snapshot(current.ts, state)
        if previous is None:
            count_reason(rejection_reasons, "warmup_no_window")
            return None, None

        structure = self._get_dump_structure(ticker.symbol, ticker.price)
        if structure is None:
            count_reason(rejection_reasons, "no_dump_structure")
            return None, None

        price_growth_lookback_pct, drawdown_from_high_pct, high_price = structure
        oi_change_pct = pct_change(previous.oi, current.oi)
        cvd_delta = current.cvd - previous.cvd
        price_change_window_pct = pct_change(previous.price, current.price)
        turnover_24h = self._turnover(ticker)

        checks = {
            "volume": turnover_24h >= self.settings.dump_min_turnover_24h_usdt,
            "prior_pump": price_growth_lookback_pct
            >= self.settings.dump_min_price_growth_lookback_pct,
            "drawdown_from_high": drawdown_from_high_pct
            <= -self.settings.dump_min_drawdown_from_high_pct,
            "price_dropping": price_change_window_pct <= -self.settings.dump_min_price_drop_window_pct,
            "sell_cvd": cvd_delta <= -self.settings.dump_min_negative_cvd_delta_usdt,
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
            for reason, passed in checks.items():
                if not passed:
                    count_reason(rejection_reasons, reason)
            if signal_score < self.settings.dump_min_signal_score:
                count_reason(rejection_reasons, "score")
            return None, self._build_watchlist_alert(
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

        if (
            now - state.last_alert_ts < self.settings.dump_alert_cooldown_minutes * 60
            and signal_score < state.last_alert_score + self.settings.dump_alert_score_improvement
        ):
            count_reason(rejection_reasons, "cooldown")
            return None, None

        state.consecutive_matches += 1
        if state.consecutive_matches < self.settings.dump_consecutive_checks:
            count_reason(rejection_reasons, "confirmations_waiting")
            return None, None
        if not self._claim_symbol_alert(now, ticker.symbol, signal_score, rejection_reasons):
            return None, None

        return DumpSignal(
            source=self.source,
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
        ), None

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
        limit = max(3, self.settings.dump_lookback_days + 1)
        klines = self.client.get_daily_klines(symbol, limit=limit)
        if len(klines) < self.settings.dump_lookback_days:
            return None

        lookback = klines[-self.settings.dump_lookback_days :]
        base_open = lookback[0].open_price
        high_price = max(kline.high_price for kline in lookback)
        if base_open <= 0 or high_price <= 0:
            return None
        return pct_change(base_open, high_price), pct_change(high_price, price), high_price

    def _find_window_snapshot(self, now: int, state: SymbolState) -> Snapshot | None:
        target = now - self.settings.dump_window_minutes * 60
        candidates = [snapshot for snapshot in state.snapshots if snapshot.ts <= target]
        if not candidates:
            return None
        return candidates[-1]

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
