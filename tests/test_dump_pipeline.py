from dataclasses import replace
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bybit_client import BybitClient, Kline, OrderbookQuote, Ticker, Trade
from binance_client import BinanceClient, BinanceTicker, OpenInterestPoint
from chart_renderer import render_dump_chart
from config import get_settings
from dump_scanner import (
    BINANCE_DEEP_CANDIDATES,
    DUMP_MODEL_VERSION,
    DumpSignal,
    DumpScanner,
    DumpTimeframeMetrics,
    MARKET_EVIDENCE,
    SYMBOL_ALERTS,
    MarketEvidence,
)
from history import HistoryStore
from main_bothost import enrich_telegram_signal, send_signal_with_symbol_cooldown
from state import Snapshot, StateStore, SymbolState
from telegram import TelegramNotifier, format_dump_signal


class FakeBybitClient(BybitClient):
    def __init__(self, trades):
        self.trades = trades

    def get_recent_trades(self, symbol, limit=1000, category="linear"):
        return list(self.trades)


class FakeNotifier:
    def __init__(self, fail=False):
        self.fail = fail
        self.photos = []
        self.messages = []
        self.caption_edits = []
        self.text_edits = []

    def send_message(self, text, reply_markup=None):
        if self.fail:
            raise RuntimeError("send failed")
        self.messages.append(text)
        return {"result": {"message_id": 41}}

    def send_photo(self, photo, caption=""):
        if self.fail:
            raise RuntimeError("photo failed")
        self.photos.append((photo, caption))
        return {"result": {"message_id": 42}}

    def edit_message_caption(self, message_id, caption):
        self.caption_edits.append((message_id, caption))

    def edit_message_text(self, message_id, text):
        self.text_edits.append((message_id, text))


class FakeAnalyzer:
    enabled = True

    def analyze(self, signal, chart=None):
        return (
            "AI: SHORT, уверенность 7/10\n"
            "Фон: значимых новостей не найдено\n"
            "План: вход 99-100; отмена 103; цели 95 и 90"
        )


class FakeBinanceClient(BinanceClient):
    def __init__(self):
        pass

    def _get(self, path, params=None):
        start = int(params["fromId"])
        limit = int(params["limit"])
        return [
            {"a": trade_id, "m": True, "p": "10", "q": "1", "T": trade_id * 10}
            for trade_id in range(start, start + limit)
        ]


class FakeBinanceTickerClient(BinanceClient):
    def __init__(self, funding_error=False):
        super().__init__(base_url="https://example.invalid")
        self.funding_error = funding_error

    def _get(self, path, params=None):
        if path == "/fapi/v1/ticker/24hr":
            return [
                {
                    "symbol": "AAAUSDT",
                    "priceChangePercent": "-5",
                    "quoteVolume": "5000000",
                    "lastPrice": "10",
                    "volume": "500000",
                    "highPrice": "12",
                    "lowPrice": "9",
                }
            ]
        if path == "/fapi/v1/premiumIndex":
            if self.funding_error:
                raise RuntimeError("funding endpoint unavailable")
            return [{"symbol": "AAAUSDT", "lastFundingRate": "-0.00025"}]
        raise AssertionError(f"unexpected path: {path}")


class FakeChartClient:
    def get_klines(self, symbol, interval="1h", limit=192):
        base_ts = 1_700_000_000_000
        interval_minutes = {"1h": 60, "4h": 240}.get(interval, 60)
        klines = []
        for index in range(limit):
            base = 100 + index * 0.05
            close = base + (0.3 if index % 3 else -0.2)
            klines.append(
                Kline(
                    start_ms=base_ts + index * interval_minutes * 60_000,
                    open_price=base,
                    high_price=max(base, close) + 0.4,
                    low_price=min(base, close) - 0.4,
                    close_price=close,
                    volume=1_000 + index,
                    turnover=100_000 + index * 1_000,
                )
            )
        return klines


class FakeChartHistory:
    def get_market_snapshots(self, **kwargs):
        base_ts = 1_700_000_000
        return [
            (base_ts + index * 120, 100, 1_000 + index * 2, -index * 2_000, 0, 5_000_000)
            for index in range(120)
        ]


class FakeMetricsClient:
    def get_klines(self, symbol, interval="1h", limit=30):
        hour_ms = 60 * 60_000
        current_hour = int(time.time() * 1000) // hour_ms * hour_ms
        return [
            Kline(
                start_ms=current_hour - (30 - index) * hour_ms,
                open_price=100 + index,
                high_price=102 + index,
                low_price=99 + index,
                close_price=101 + index,
                volume=1_000,
                turnover=1_000_000,
                taker_buy_turnover=400_000,
            )
            for index in range(30)
        ]

    def get_open_interest_history(self, symbol, period="1h", limit=30):
        hour_ms = 60 * 60_000
        current_hour = int(time.time() * 1000) // hour_ms * hour_ms
        return [
            OpenInterestPoint(
                timestamp_ms=current_hour - (30 - index) * hour_ms,
                open_interest=1_000 + index * 10,
            )
            for index in range(31)
        ]


class FakeHttpResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"ok": true, "result": {}}'


class DumpPipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(
            get_settings(),
            dump_window_minutes=60,
            dump_chart_lookback_hours=168,
            dump_chart_interval="1h",
            dump_max_symbols=100,
            dump_deep_max_symbols=30,
            dump_cross_exchange_required=True,
            dump_cross_exchange_max_age_seconds=300,
        )

    @staticmethod
    def _signal(**overrides):
        values = {
            "source": "BINANCE+BYBIT",
            "mode": "SHORT_TREND",
            "confirmation_source": "BYBIT",
            "symbol": "AAAUSDT",
            "window_minutes": 60,
            "lookback_days": 2,
            "signal_score": 8,
            "price_growth_lookback_pct": 30,
            "drawdown_from_high_pct": -10,
            "oi_change_pct": 1,
            "cvd_delta_usdt": -20_000,
            "price_change_window_pct": -2,
            "funding_rate": -0.0001,
            "price": 100,
            "high_price": 120,
            "turnover_24h": 10_000_000,
            "new_trades": 500,
            "consecutive_matches": 1,
            "confirmation_price_change_pct": -1,
            "confirmation_oi_change_pct": 0.5,
            "confirmation_cvd_delta_usdt": -10_000,
            "market_observed_ts": int(time.time()),
            "decision_ts": int(time.time()),
            "cvd_complete": True,
            "confirmation_cvd_complete": True,
            "cvd_coverage_seconds": 3_600,
            "confirmation_cvd_coverage_seconds": 3_600,
        }
        values.update(overrides)
        return DumpSignal(**values)

    def test_bybit_trade_gap_is_detected(self):
        client = FakeBybitClient(
            [
                Trade("new-1", "AAAUSDT", 10, 1, "Sell", 2_000),
                Trade("new-2", "AAAUSDT", 9, 1, "Sell", 2_100),
            ]
        )
        batch = client.get_trades_since(
            "AAAUSDT",
            last_trade_id="old",
            last_time_ms=1_000,
        )
        self.assertFalse(batch.complete)
        self.assertEqual([trade.exec_id for trade in batch.trades], ["new-1", "new-2"])

    def test_bybit_same_timestamp_without_last_trade_is_incomplete(self):
        client = FakeBybitClient(
            [Trade("unknown", "AAAUSDT", 10, 1, "Sell", 1_000)]
        )
        batch = client.get_trades_since(
            "AAAUSDT",
            last_trade_id="missing",
            last_time_ms=1_000,
        )
        self.assertFalse(batch.complete)
        self.assertEqual(batch.trades, [])

    def test_bybit_best_bid_ask_uses_orderbook_server_time(self):
        client = BybitClient("https://example.invalid")
        payload = {
            "time": 1_700_000_000_999,
            "result": {
                "ts": 1_700_000_000_123,
                "b": [["99.5", "12"]],
                "a": [["100.0", "8"]],
            },
        }
        with patch.object(client, "_get", return_value=payload):
            quote = client.get_best_bid_ask("AAAUSDT")
        self.assertEqual(quote.ts, 1_700_000_000)
        self.assertEqual(quote.bid_price, 99.5)
        self.assertEqual(quote.ask_price, 100.0)
        self.assertGreater(quote.spread_bps, 0)

    def test_binance_trades_are_paginated_and_saturation_is_marked(self):
        batch = FakeBinanceClient().get_trades_since(
            "AAAUSDT",
            last_trade_id="100",
            limit=3,
            max_pages=2,
        )
        self.assertEqual([trade.exec_id for trade in batch.trades], ["101", "102", "103", "104", "105", "106"])
        self.assertFalse(batch.complete)

    def test_binance_tickers_include_bulk_funding(self):
        ticker = FakeBinanceTickerClient().get_usdt_perp_tickers()["AAAUSDT"]
        self.assertTrue(ticker.funding_rate_available)
        self.assertAlmostEqual(ticker.funding_rate, -0.00025)

    def test_stale_funding_is_not_treated_as_available(self):
        client = FakeBinanceTickerClient(funding_error=True)
        client._funding_rates_cache = {"AAAUSDT": -0.001}
        client._funding_rates_cache_ts = 0
        ticker = client.get_usdt_perp_tickers()["AAAUSDT"]
        self.assertFalse(ticker.funding_rate_available)
        self.assertEqual(ticker.funding_rate, 0)

    def test_missing_funding_does_not_add_signal_score(self):
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        metrics = {
            "price_growth_lookback_pct": self.settings.dump_min_price_growth_lookback_pct,
            "drawdown_from_high_pct": -self.settings.dump_min_drawdown_from_high_pct,
            "oi_change_pct": -1,
            "cvd_delta": -self.settings.dump_min_negative_cvd_delta_usdt,
            "price_change_window_pct": -self.settings.dump_min_price_drop_window_pct,
        }
        without_funding = scanner._score_signal(**metrics, funding_rate=None)
        with_funding = scanner._score_signal(**metrics, funding_rate=0)
        self.assertEqual(with_funding, without_funding + 1)

    def test_missing_open_interest_does_not_add_signal_score(self):
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        metrics = {
            "price_growth_lookback_pct": self.settings.dump_min_price_growth_lookback_pct,
            "drawdown_from_high_pct": -self.settings.dump_min_drawdown_from_high_pct,
            "cvd_delta": -self.settings.dump_min_negative_cvd_delta_usdt,
            "price_change_window_pct": -self.settings.dump_min_price_drop_window_pct,
            "funding_rate": None,
        }
        without_oi = scanner._score_signal(**metrics, oi_change_pct=None)
        with_flat_oi = scanner._score_signal(**metrics, oi_change_pct=0)
        self.assertEqual(with_flat_oi, without_oi + 2)

    def test_dump_modes_are_separate(self):
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        self.assertEqual(scanner._signal_mode(-2.0), "LIQUIDATION_FLUSH")
        self.assertEqual(scanner._signal_mode(0.5), "SHORT_TREND")
        self.assertEqual(scanner._signal_mode(-1.0), "UNCONFIRMED_OI")

    def test_hour_window_rejects_snapshot_that_is_too_old(self):
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        now = 10_000
        state = SymbolState(
            snapshots=[
                Snapshot(
                    now - 7_200,
                    100,
                    0,
                    10,
                    0,
                    5_000_000,
                    cvd_generation=0,
                )
            ],
            cvd_generation=0,
        )
        self.assertIsNone(scanner._find_window_snapshot(now, state))

    def test_top_100_is_reduced_to_deep_shortlist(self):
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        scanner._get_dump_structure = lambda symbol, price: (20.0, -5.0, 11.0)
        tickers = [
            (
                rank,
                BinanceTicker(
                    symbol=f"COIN{rank}USDT",
                    price_change_24h_pct=-1,
                    quote_volume_24h=1_000_000 - rank,
                    price=10,
                    high_price_24h=11,
                ),
            )
            for rank in range(1, 101)
        ]
        reasons = {}
        selected = scanner._select_deep_candidates(1_000, tickers, reasons)
        self.assertEqual(len(selected), 30)
        self.assertEqual(reasons["outside_deep_shortlist"], 70)

    def test_binance_candidate_bypasses_bybit_top_and_structure_prefilter(self):
        settings = replace(self.settings, dump_max_symbols=2, dump_deep_max_symbols=1)
        scanner = DumpScanner("BYBIT", object(), StateStore("unused.json"), settings)
        tickers = [
            (
                rank,
                Ticker(
                    symbol=symbol,
                    price=10,
                    open_interest=100,
                    funding_rate=0,
                    turnover_24h=10_000_000 - rank,
                    volume_24h=1_000,
                    high_price_24h=12,
                    low_price_24h=9,
                    price_change_24h_pct=-1,
                ),
            )
            for rank, symbol in (
                (1, "TOP1USDT"),
                (2, "TOP2USDT"),
                (101, "DIRECTUSDT"),
            )
        ]
        BINANCE_DEEP_CANDIDATES.clear()
        BINANCE_DEEP_CANDIDATES.add("DIRECTUSDT")
        try:
            top_selected = scanner._select_top_ranked(1_000, tickers, {})
            self.assertIn("DIRECTUSDT", [ticker.symbol for _, ticker in top_selected])
            scanner._get_dump_structure = lambda symbol, price: None
            deep_selected = scanner._select_deep_candidates(1_000, top_selected, {})
            self.assertEqual([ticker.symbol for _, ticker in deep_selected], ["DIRECTUSDT"])
        finally:
            BINANCE_DEEP_CANDIDATES.clear()

    def test_binance_requires_recent_bybit_confirmation(self):
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        primary = MarketEvidence("BINANCE", "AAAUSDT", 1_000, "SHORT_TREND", -1.0, 1.0, -20_000, True)
        partner = MarketEvidence("BYBIT", "AAAUSDT", 900, "SHORT_TREND", -0.8, 0.5, -2_000, True)
        self.assertTrue(scanner._cross_exchange_ok(primary, partner))
        stale = replace(partner, ts=600)
        self.assertFalse(scanner._cross_exchange_ok(primary, stale))
        bybit_primary = replace(primary, source="BYBIT")
        self.assertFalse(scanner._cross_exchange_ok(bybit_primary, partner))

    def test_combined_signal_is_built_from_both_exchanges(self):
        MARKET_EVIDENCE.clear()
        SYMBOL_ALERTS.clear()
        bybit_scanner = DumpScanner("BYBIT", object(), StateStore("unused.json"), self.settings)
        bybit_scanner._publish_and_get_partner(
            MarketEvidence("BYBIT", "AAAUSDT", 10_000, "SHORT_TREND", -1.0, 0.2, -2_000, True)
        )
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        scanner._get_dump_structure = lambda symbol, price: (20.0, -5.0, 10.5)
        ticker = BinanceTicker(
            symbol="AAAUSDT",
            price_change_24h_pct=-5,
            quote_volume_24h=5_000_000,
            price=9.9,
            funding_rate=0,
            funding_rate_available=True,
            high_price_24h=10.5,
        )
        state = SymbolState(
            cumulative_cvd=-10_000,
            snapshots=[
                Snapshot(6_400, 100, 0, 10, 0, 5_000_000, cvd_generation=0),
                Snapshot(10_000, 101, -10_000, 9.9, 0, 5_000_000, cvd_generation=0),
            ],
        )
        signal, _ = scanner._build_signal(10_000, ticker, state, {}, 1, True)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.source, "BINANCE+BYBIT")
        self.assertEqual(signal.mode, "SHORT_TREND")
        self.assertEqual([item.label for item in signal.timeframes], ["1H", "4H", "1D"])

    def test_signal_is_blocked_when_open_interest_is_missing(self):
        MARKET_EVIDENCE.clear()
        SYMBOL_ALERTS.clear()
        bybit_scanner = DumpScanner("BYBIT", object(), StateStore("unused.json"), self.settings)
        bybit_scanner._publish_and_get_partner(
            MarketEvidence("BYBIT", "AAAUSDT", 10_000, "SHORT_TREND", -1.0, 0.2, -2_000, True)
        )
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        scanner._get_dump_structure = lambda symbol, price: (20.0, -5.0, 10.5)
        ticker = BinanceTicker(
            symbol="AAAUSDT",
            price_change_24h_pct=-5,
            quote_volume_24h=5_000_000,
            price=9.9,
            funding_rate=0,
            funding_rate_available=True,
            high_price_24h=10.5,
        )
        state = SymbolState(
            cumulative_cvd=-10_000,
            snapshots=[
                Snapshot(6_400, 0, 0, 10, 0, 5_000_000, cvd_generation=0),
                Snapshot(10_000, 0, -10_000, 9.9, 0, 5_000_000, cvd_generation=0),
            ],
        )
        reasons = {}
        signal, _ = scanner._build_signal(10_000, ticker, state, reasons, 1, True)
        self.assertIsNone(signal)
        self.assertEqual(reasons["oi_available"], 1)

    def test_multitimeframe_metrics_use_hourly_binance_history(self):
        scanner = DumpScanner(
            "BINANCE",
            FakeMetricsClient(),
            StateStore("unused.json"),
            self.settings,
        )
        ticker = BinanceTicker(
            symbol="AAAUSDT",
            price_change_24h_pct=-12,
            quote_volume_24h=5_000_000,
            price=100,
        )
        metrics = scanner._load_timeframe_metrics(
            ticker=ticker,
            price_change_1h=-1,
            oi_change_1h=0.5,
            cvd_delta_1h=-10_000,
        )
        self.assertEqual([item.label for item in metrics], ["1H", "4H", "1D"])
        self.assertIsNotNone(metrics[1].price_change_pct)
        self.assertIsNotNone(metrics[1].oi_change_pct)
        self.assertLess(metrics[1].cvd_delta_usdt, 0)
        self.assertIsNotNone(metrics[2].price_change_pct)
        self.assertIsNotNone(metrics[2].oi_change_pct)

    def test_legacy_15m_dump_settings_are_upgraded(self):
        with patch.dict(
            "os.environ",
            {
                "DUMP_WINDOW_MINUTES": "15",
                "DUMP_CHART_LOOKBACK_HOURS": "48",
                "DUMP_CHART_INTERVAL": "15m",
            },
        ):
            settings = get_settings()
        self.assertEqual(settings.dump_window_minutes, 60)
        self.assertEqual(settings.dump_chart_lookback_hours, 168)
        self.assertEqual(settings.dump_chart_interval, "1h")

    def test_dump_caption_contains_1h_4h_and_1d_metrics(self):
        signal = SimpleNamespace(
            symbol="AAAUSDT",
            source="BINANCE+BYBIT",
            mode="SHORT_TREND",
            signal_score=8,
            lookback_days=2,
            price_growth_lookback_pct=30,
            drawdown_from_high_pct=-12,
            funding_rate=-0.001,
            price=100,
            high_price=120,
            turnover_24h=10_000_000,
            confirmation_source="BYBIT",
            confirmation_price_change_pct=-1,
            confirmation_oi_change_pct=0.5,
            confirmation_cvd_delta_usdt=-20_000,
            timeframes=(
                DumpTimeframeMetrics("1H", 60, -1, 0.5, -10_000),
                DumpTimeframeMetrics("4H", 240, -5, 2, -500_000),
                DumpTimeframeMetrics("1D", 1440, -12, 4, -5_000_000),
            ),
        )
        caption = format_dump_signal(signal)
        self.assertIn("1H |", caption)
        self.assertIn("4H |", caption)
        self.assertIn("1D |", caption)
        self.assertNotIn("15m", caption)
        self.assertLess(len(caption), 800)

    def test_dump_caption_marks_missing_funding(self):
        signal = SimpleNamespace(
            symbol="AAAUSDT",
            source="BINANCE+BYBIT",
            mode="SHORT_TREND",
            signal_score=7,
            lookback_days=2,
            price_growth_lookback_pct=20,
            drawdown_from_high_pct=-8,
            funding_rate=0,
            funding_available=False,
            price=100,
            high_price=110,
            turnover_24h=5_000_000,
            confirmation_source="BYBIT",
            confirmation_price_change_pct=-1,
            confirmation_oi_change_pct=0.5,
            confirmation_cvd_delta_usdt=-20_000,
            timeframes=(),
        )
        self.assertIn("Funding: нет данных", format_dump_signal(signal))

    def test_signal_reviews_use_15_30_60_240_minute_horizons(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            signal_ts = 10_000
            history.record_signal(
                signal_type="dump_binance",
                symbol="BINANCE:AAAUSDT",
                ts=signal_ts,
                price=100,
                open_interest_change_pct=1,
                futures_cvd_change_pct=0,
                futures_cvd_delta_usdt=-10_000,
                spot_cvd_change_pct=0,
                spot_cvd_delta_usdt=0,
                price_change_pct=-1,
                payload="test",
            )
            for minutes, price in ((15, 95), (30, 90), (60, 85), (240, 80)):
                history.record_pending_signal_prices(
                    signal_type="dump_binance",
                    prices={"AAAUSDT": price},
                    ts=signal_ts + minutes * 60,
                )
            reviewed = history.update_signal_reviews(now=signal_ts + 240 * 60)
            self.assertEqual(reviewed, 4)
            horizons = [row[1] for row in history.get_signal_stats()]
            self.assertEqual(horizons, [15, 30, 60, 240])

    def test_history_schema_migration_preserves_legacy_reviews(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "scanner.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE signals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        signal_type TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        ts INTEGER NOT NULL,
                        price REAL NOT NULL,
                        open_interest_change_pct REAL NOT NULL,
                        futures_cvd_change_pct REAL NOT NULL,
                        futures_cvd_delta_usdt REAL NOT NULL,
                        spot_cvd_change_pct REAL NOT NULL,
                        spot_cvd_delta_usdt REAL NOT NULL,
                        price_change_pct REAL NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE signal_reviews (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        signal_id INTEGER NOT NULL,
                        horizon_minutes INTEGER NOT NULL,
                        reviewed_ts INTEGER NOT NULL,
                        price_at_review REAL NOT NULL,
                        move_pct REAL NOT NULL,
                        max_favorable_pct REAL NOT NULL,
                        max_adverse_pct REAL NOT NULL,
                        UNIQUE(signal_id, horizon_minutes)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE signal_price_snapshots (
                        signal_id INTEGER NOT NULL,
                        ts INTEGER NOT NULL,
                        price REAL NOT NULL,
                        UNIQUE(signal_id, ts)
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO signals (
                        signal_type, symbol, ts, price,
                        open_interest_change_pct, futures_cvd_change_pct,
                        futures_cvd_delta_usdt, spot_cvd_change_pct,
                        spot_cvd_delta_usdt, price_change_pct, payload
                    )
                    VALUES ('dump_binance', 'BINANCE:AAAUSDT', 1000, 100, 0, 0, 0, 0, 0, -1, 'old')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO signal_reviews (
                        signal_id, horizon_minutes, reviewed_ts,
                        price_at_review, move_pct,
                        max_favorable_pct, max_adverse_pct
                    )
                    VALUES (1, 15, 2000, 95, 5, 5, 0)
                    """
                )
                conn.commit()

            history = HistoryStore(str(db_path))
            with closing(sqlite3.connect(history.path)) as conn:
                review = conn.execute(
                    "SELECT status, move_pct FROM signal_reviews WHERE signal_id = 1"
                ).fetchone()
                signal_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(signals)")
                }
            self.assertEqual(review, ("legacy", 5.0))
            self.assertIn("entry_quote_ts", signal_columns)
            self.assertIn("schema_version", signal_columns)

    def test_signal_review_uses_entry_as_metric_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            signal_ts = 10_000
            signal_id = history.record_signal(
                signal_type="dump_binance",
                symbol="BINANCE:AAAUSDT",
                ts=signal_ts,
                price=100,
                open_interest_change_pct=1,
                futures_cvd_change_pct=0,
                futures_cvd_delta_usdt=-10_000,
                spot_cvd_change_pct=0,
                spot_cvd_delta_usdt=0,
                price_change_pct=-1,
                payload="test",
            )
            history.record_pending_signal_prices(
                signal_type="dump_binance",
                prices={"AAAUSDT": 95},
                ts=signal_ts + 15 * 60,
            )
            history.update_signal_reviews(now=signal_ts + 15 * 60)
            with closing(sqlite3.connect(history.path)) as conn:
                favorable, adverse = conn.execute(
                    """
                    SELECT max_favorable_pct, max_adverse_pct
                    FROM signal_reviews
                    WHERE signal_id = ? AND horizon_minutes = 15
                    """,
                    (signal_id,),
                ).fetchone()
            self.assertAlmostEqual(favorable, 5)
            self.assertEqual(adverse, 0)

    def test_exact_review_uses_first_bybit_ask_after_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            signal_ts = 10_000
            signal_id = history.record_signal(
                signal_type="dump_binance",
                symbol="BINANCE:AAAUSDT",
                ts=signal_ts,
                price=101,
                open_interest_change_pct=1,
                futures_cvd_change_pct=0,
                futures_cvd_delta_usdt=-10_000,
                spot_cvd_change_pct=0,
                spot_cvd_delta_usdt=0,
                price_change_pct=-1,
                payload="test",
                model_version=DUMP_MODEL_VERSION,
                telegram_sent_ts=signal_ts,
                entry_quote_ts=signal_ts - 1,
                entry_bid=100,
                entry_ask=100.2,
                entry_price=100,
                entry_quote_status="ok",
                execution_venue="BYBIT",
            )
            history.record_pending_signal_quotes(
                quotes={"AAAUSDT": (89.8, 90)},
                ts=signal_ts + 14 * 60,
                model_version=DUMP_MODEL_VERSION,
            )
            self.assertEqual(
                history.update_signal_reviews(
                    now=signal_ts + 15 * 60 + 10,
                    horizons_minutes=(15,),
                    max_lag_seconds=300,
                ),
                0,
            )
            history.record_pending_signal_quotes(
                quotes={"AAAUSDT": (94.8, 95)},
                ts=signal_ts + 15 * 60 + 120,
                model_version=DUMP_MODEL_VERSION,
            )
            reviewed = history.update_signal_reviews(
                now=signal_ts + 15 * 60 + 120,
                horizons_minutes=(15,),
                max_lag_seconds=300,
            )
            self.assertEqual(reviewed, 1)
            with closing(sqlite3.connect(history.path)) as conn:
                row = conn.execute(
                    """
                    SELECT status, price_at_review, price_ts, lag_seconds, move_pct
                    FROM signal_reviews
                    WHERE signal_id = ? AND horizon_minutes = 15
                    """,
                    (signal_id,),
                ).fetchone()
            self.assertEqual(row[:4], ("ok", 95.0, signal_ts + 1_020, 120))
            self.assertAlmostEqual(row[4], 5.0)

    def test_exact_review_marks_missing_quote_and_excludes_it_from_stats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            signal_ts = 20_000
            history.record_signal(
                signal_type="dump_binance",
                symbol="BINANCE:AAAUSDT",
                ts=signal_ts,
                price=101,
                open_interest_change_pct=1,
                futures_cvd_change_pct=0,
                futures_cvd_delta_usdt=-10_000,
                spot_cvd_change_pct=0,
                spot_cvd_delta_usdt=0,
                price_change_pct=-1,
                payload="test",
                model_version=DUMP_MODEL_VERSION,
                telegram_sent_ts=signal_ts,
                entry_quote_ts=signal_ts - 1,
                entry_bid=100,
                entry_ask=100.2,
                entry_price=100,
                entry_quote_status="ok",
                execution_venue="BYBIT",
            )
            reviewed = history.update_signal_reviews(
                now=signal_ts + 15 * 60 + 301,
                horizons_minutes=(15,),
                max_lag_seconds=300,
            )
            self.assertEqual(reviewed, 1)
            self.assertEqual(history.get_signal_stats(DUMP_MODEL_VERSION), [])
            quality = history.get_review_quality(DUMP_MODEL_VERSION)
            self.assertEqual(quality[0][:2], ("missing", 1))

    def test_entry_delay_scenario_tracks_executable_bybit_bid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            signal_id = history.record_signal(
                signal_type="dump_binance",
                symbol="BINANCE:AAAUSDT",
                ts=30_000,
                price=101,
                open_interest_change_pct=1,
                futures_cvd_change_pct=0,
                futures_cvd_delta_usdt=-10_000,
                spot_cvd_change_pct=0,
                spot_cvd_delta_usdt=0,
                price_change_pct=-1,
                payload="test",
                model_version=DUMP_MODEL_VERSION,
                entry_quote_ts=30_000,
                entry_bid=100,
                entry_ask=100.2,
                entry_price=100,
                entry_quote_status="ok",
                execution_venue="BYBIT",
            )
            history.record_entry_quote_scenario(
                signal_id=signal_id,
                delay_seconds=5,
                target_ts=30_005,
                quote_ts=30_006,
                bid=99,
                ask=99.2,
                spread_bps=20.2,
                status="ok",
            )
            stats = history.get_entry_scenario_stats(DUMP_MODEL_VERSION)
            self.assertEqual(stats[0][:2], (5, 1))
            self.assertAlmostEqual(stats[0][2], 1.0)
            with closing(sqlite3.connect(history.path)) as conn:
                snapshot = conn.execute(
                    """
                    SELECT price, venue, bid, ask, quote_status
                    FROM signal_price_snapshots
                    WHERE signal_id = ? AND ts = 30006
                    """,
                    (signal_id,),
                ).fetchone()
            self.assertEqual(snapshot, (99.2, "BYBIT", 99.0, 99.2, "ok"))

    def test_watchlist_keeps_one_best_candidate_per_bucket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            common = {
                "scanner": "dump_binance",
                "symbol": "AAAUSDT",
                "price": 100,
                "passed_checks": ["volume"],
                "missing_checks": ["sell_cvd"],
                "payload": "candidate",
                "cooldown_seconds": 1_800,
            }
            self.assertTrue(history.record_watchlist_candidate(**common, score=4, ts=1_000))
            self.assertFalse(history.record_watchlist_candidate(**common, score=4, ts=1_100))
            self.assertTrue(history.record_watchlist_candidate(**common, score=6, ts=1_200))
            self.assertTrue(history.record_watchlist_candidate(**common, score=5, ts=1_900))
            rows = history.get_recent_watchlist_candidates(limit=10)
            self.assertEqual(len(rows), 2)
            self.assertEqual(sorted(row[3] for row in rows), [5, 6])

    def test_evaluation_history_keeps_hourly_best_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            common = {
                "scanner": "dump_binance",
                "source": "BINANCE",
                "symbol": "AAAUSDT",
                "source_rank": 80,
                "price": 100,
                "turnover_24h": 5_000_000,
                "model_version": "dump-test-v1",
                "snapshot_interval_seconds": 3_600,
            }
            history.record_scanner_evaluation(
                **common,
                ts=1_000,
                selected=False,
                status="outside_top_symbols",
                reason="outside_top_symbols",
                score=0,
            )
            history.record_scanner_evaluation(
                **common,
                ts=1_100,
                selected=True,
                status="watchlist",
                reason="sell_cvd",
                score=6,
            )
            history.record_scanner_evaluation(
                **common,
                ts=1_200,
                selected=True,
                status="rejected",
                reason="sell_cvd",
                score=5,
            )
            history.record_scanner_evaluation(
                **common,
                ts=3_700,
                selected=False,
                status="outside_top_symbols",
                reason="outside_top_symbols",
                score=0,
            )
            with closing(sqlite3.connect(history.path)) as conn:
                rows = conn.execute(
                    """
                    SELECT bucket_ts, status, score, model_version
                    FROM scanner_evaluation_history
                    ORDER BY bucket_ts
                    """
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], (0, "watchlist", 6, "dump-test-v1"))
            self.assertEqual(rows[1][0], 3_600)

    def test_telegram_cooldown_can_be_released_after_failed_send(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            claimed, _, _ = history.claim_telegram_symbol_alert(
                symbol="AAAUSDT",
                ts=1_000,
                signal_type="dump_binance",
                cooldown_minutes=240,
            )
            self.assertTrue(claimed)
            history.release_telegram_symbol_alert(symbol="AAAUSDT", ts=1_000)
            claimed_again, _, _ = history.claim_telegram_symbol_alert(
                symbol="AAAUSDT",
                ts=1_001,
                signal_type="dump_binance",
                cooldown_minutes=240,
            )
            self.assertTrue(claimed_again)

    def test_only_successfully_sent_signal_is_recorded(self):
        signal = SimpleNamespace(
            symbol="AAAUSDT",
            price=100,
            oi_change_pct=1,
            cvd_delta_usdt=-10_000,
            price_change_window_pct=-1,
            mode="SHORT_TREND",
            signal_score=8,
            model_version="dump-test-v1",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            sent = send_signal_with_symbol_cooldown(
                notifier=FakeNotifier(fail=True),
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda _: "signal",
            )
            self.assertFalse(sent)
            self.assertEqual(history.get_recent_signals(), [])

            sent = send_signal_with_symbol_cooldown(
                notifier=FakeNotifier(),
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda _: "signal",
            )
            self.assertTrue(sent)
            self.assertEqual(len(history.get_recent_signals()), 1)
            with closing(sqlite3.connect(history.path)) as conn:
                model_version, settings_snapshot = conn.execute(
                    "SELECT model_version, settings_snapshot FROM signals"
                ).fetchone()
            snapshot = json.loads(settings_snapshot)
            self.assertEqual(model_version, "dump-test-v1")
            self.assertEqual(snapshot["dump_max_symbols"], 100)
            self.assertNotIn("openai_api_key", snapshot)
            self.assertNotIn("telegram_bot_token", snapshot)

    def test_fresh_bybit_quote_is_used_as_executable_short_entry(self):
        signal = self._signal(price=101)
        quote_ts = int(time.time())
        quote = OrderbookQuote(
            symbol="AAAUSDT",
            bid_price=99.5,
            bid_size=10,
            ask_price=100,
            ask_size=8,
            ts=quote_ts,
        )
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            sent = send_signal_with_symbol_cooldown(
                notifier=notifier,
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda item: f"entry={item.entry_price}",
                execution_quote_provider=lambda _: quote,
            )
            self.assertTrue(sent)
            self.assertEqual(notifier.messages, ["entry=99.5"])
            with closing(sqlite3.connect(history.path)) as conn:
                row = conn.execute(
                    """
                    SELECT
                        price, market_price, entry_price, entry_bid, entry_ask,
                        entry_quote_ts, entry_quote_status, execution_venue,
                        schema_version, config_hash
                    FROM signals
                    """
                ).fetchone()
                snapshot = conn.execute(
                    """
                    SELECT price, venue, bid, ask, quote_status
                    FROM signal_price_snapshots
                    """
                ).fetchone()
            self.assertEqual(row[:8], (101.0, 101.0, 99.5, 99.5, 100.0, quote_ts, "ok", "BYBIT"))
            self.assertTrue(row[8])
            self.assertEqual(len(row[9]), 16)
            self.assertEqual(snapshot, (100.0, "BYBIT", 99.5, 100.0, "ok"))

    def test_stale_bybit_quote_blocks_telegram_and_releases_cooldown(self):
        signal = self._signal()
        stale_quote = OrderbookQuote(
            symbol="AAAUSDT",
            bid_price=99.5,
            bid_size=10,
            ask_price=100,
            ask_size=8,
            ts=int(time.time()) - self.settings.dump_execution_quote_max_age_seconds - 1,
        )
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            sent = send_signal_with_symbol_cooldown(
                notifier=notifier,
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda _: "signal",
                execution_quote_provider=lambda _: stale_quote,
            )
            self.assertFalse(sent)
            self.assertEqual(notifier.messages, [])
            self.assertEqual(history.get_recent_signals(), [])
            claimed, _, _ = history.claim_telegram_symbol_alert(
                symbol="AAAUSDT",
                ts=int(time.time()),
                signal_type="dump_binance",
                cooldown_minutes=240,
            )
            self.assertTrue(claimed)

    def test_chart_and_statistics_are_sent_as_one_message(self):
        signal = SimpleNamespace(
            symbol="AAAUSDT",
            price=100,
            oi_change_pct=1,
            cvd_delta_usdt=-10_000,
            price_change_window_pct=-1,
            mode="SHORT_TREND",
            signal_score=8,
        )
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            sent = send_signal_with_symbol_cooldown(
                notifier=notifier,
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda _: "signal",
                chart_renderer=lambda _: b"png-bytes",
            )
            self.assertTrue(sent)
            self.assertEqual(notifier.photos[0][0], b"png-bytes")
            self.assertEqual(notifier.photos[0][1], "signal")
            self.assertEqual(notifier.messages, [])

    def test_openai_analysis_edits_the_same_photo_message(self):
        signal = SimpleNamespace(
            symbol="AAAUSDT",
            price=100,
            oi_change_pct=1,
            cvd_delta_usdt=-10_000,
            price_change_window_pct=-1,
            mode="SHORT_TREND",
            signal_score=8,
        )
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            sent = send_signal_with_symbol_cooldown(
                notifier=notifier,
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda _: "signal",
                chart_renderer=lambda _: b"png-bytes",
                ai_analyzer=FakeAnalyzer(),
                ai_scheduler=lambda **kwargs: enrich_telegram_signal(**kwargs),
            )
        self.assertTrue(sent)
        self.assertEqual(len(notifier.photos), 1)
        self.assertEqual(len(notifier.caption_edits), 1)
        message_id, caption = notifier.caption_edits[0]
        self.assertEqual(message_id, 42)
        self.assertIn("OpenAI + интернет", caption)
        self.assertIn("вход 99-100", caption)

    def test_chart_failure_falls_back_to_one_text_message(self):
        signal = SimpleNamespace(
            symbol="AAAUSDT",
            price=100,
            oi_change_pct=1,
            cvd_delta_usdt=-10_000,
            price_change_window_pct=-1,
            mode="SHORT_TREND",
            signal_score=8,
        )
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryStore(str(Path(temp_dir) / "scanner.db"))
            sent = send_signal_with_symbol_cooldown(
                notifier=notifier,
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda _: "signal",
                chart_renderer=lambda _: (_ for _ in ()).throw(RuntimeError("chart failed")),
            )
            self.assertTrue(sent)
            self.assertEqual(notifier.photos, [])
            self.assertEqual(notifier.messages, ["signal"])

    def test_chart_renderer_creates_1200x900_png(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed in this Python runtime")

        signal = SimpleNamespace(
            symbol="AAAUSDT",
            source="BINANCE+BYBIT",
            mode="SHORT_TREND",
            signal_score=8,
            price_growth_lookback_pct=30,
            drawdown_from_high_pct=-12,
            oi_change_pct=2,
            cvd_delta_usdt=-50_000,
            price_change_window_pct=-2,
            price=108,
            high_price=112,
            confirmation_price_change_pct=-1.5,
            timeframes=(
                DumpTimeframeMetrics("1H", 60, -2, 2, -50_000),
                DumpTimeframeMetrics("4H", 240, -5, 3, -250_000),
                DumpTimeframeMetrics("1D", 1440, -12, 5, -1_500_000),
            ),
        )
        png = render_dump_chart(signal, FakeChartClient(), FakeChartHistory())
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.size, (1200, 900))

    def test_telegram_photo_uses_multipart_upload(self):
        notifier = TelegramNotifier("token", "chat-id")
        with patch("telegram.urlopen", return_value=FakeHttpResponse()) as mocked:
            notifier.send_photo(b"png-bytes", caption="chart")
        request = mocked.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/sendPhoto"))
        self.assertIn(b'name="photo"', request.data)
        self.assertIn(b"png-bytes", request.data)


if __name__ == "__main__":
    unittest.main()
