from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bybit_client import BybitClient, Trade
from bybit_client import Kline
from binance_client import BinanceClient, BinanceTicker
from chart_renderer import render_dump_chart
from config import get_settings
from dump_scanner import DumpScanner, MARKET_EVIDENCE, SYMBOL_ALERTS, MarketEvidence
from history import HistoryStore
from main_bothost import enrich_telegram_signal, send_signal_with_symbol_cooldown
from state import Snapshot, StateStore, SymbolState
from telegram import TelegramNotifier


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


class FakeChartClient:
    def get_klines(self, symbol, interval="15m", limit=192):
        base_ts = 1_700_000_000_000
        klines = []
        for index in range(limit):
            base = 100 + index * 0.05
            close = base + (0.3 if index % 3 else -0.2)
            klines.append(
                Kline(
                    start_ms=base_ts + index * 15 * 60_000,
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
            dump_max_symbols=100,
            dump_deep_max_symbols=30,
            dump_cross_exchange_required=True,
            dump_cross_exchange_max_age_seconds=300,
        )

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

    def test_binance_trades_are_paginated_and_saturation_is_marked(self):
        batch = FakeBinanceClient().get_trades_since(
            "AAAUSDT",
            last_trade_id="100",
            limit=3,
            max_pages=2,
        )
        self.assertEqual([trade.exec_id for trade in batch.trades], ["101", "102", "103", "104", "105", "106"])
        self.assertFalse(batch.complete)

    def test_dump_modes_are_separate(self):
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        self.assertEqual(scanner._signal_mode(-2.0), "LIQUIDATION_FLUSH")
        self.assertEqual(scanner._signal_mode(0.5), "SHORT_TREND")
        self.assertEqual(scanner._signal_mode(-1.0), "UNCONFIRMED_OI")

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
            MarketEvidence("BYBIT", "AAAUSDT", 1_000, "SHORT_TREND", -1.0, 0.2, -2_000, True)
        )
        scanner = DumpScanner("BINANCE", object(), StateStore("unused.json"), self.settings)
        scanner._get_dump_structure = lambda symbol, price: (20.0, -5.0, 10.5)
        ticker = BinanceTicker(
            symbol="AAAUSDT",
            price_change_24h_pct=-5,
            quote_volume_24h=5_000_000,
            price=9.9,
            funding_rate=0,
            high_price_24h=10.5,
        )
        state = SymbolState(
            cumulative_cvd=-10_000,
            snapshots=[
                Snapshot(100, 100, 0, 10, 0, 5_000_000, cvd_generation=0),
                Snapshot(1_000, 101, -10_000, 9.9, 0, 5_000_000, cvd_generation=0),
            ],
        )
        signal, _ = scanner._build_signal(1_000, ticker, state, {}, 1, True)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.source, "BINANCE+BYBIT")
        self.assertEqual(signal.mode, "SHORT_TREND")

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
