from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from bybit_client import OrderbookQuote
from config import get_settings
from history import HistoryStore
from main_bothost import send_signal_with_symbol_cooldown
from paper_trading import PaperBroker, format_paper_summary
from research.backtest_dump import (
    SignalEvent,
    automation_gate,
    deduplicate_events,
)


def event(signal_id: int, ts: int) -> SignalEvent:
    return SignalEvent(
        signal_id=signal_id,
        signal_type="dump_binance",
        symbol="AAAUSDT",
        ts=ts,
        entry_price=100,
        gross_return_pct=1,
        max_favorable_pct=2,
        max_adverse_pct=1,
        model_version="dump-v5.2-confirmation",
        mode="SHORT_TREND",
        score=8,
        window_minutes=60,
        turnover_24h=10_000_000,
        price_growth_pct=20,
        drawdown_pct=-5,
    )


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(
            get_settings(),
            paper_trading_enabled=True,
            paper_starting_equity_usdt=10_000,
            paper_risk_per_trade_pct=0.5,
            paper_max_notional_pct=25,
            paper_max_open_positions=3,
            paper_episode_cooldown_minutes=30,
            paper_stop_loss_pct=2,
            paper_max_holding_minutes=240,
            paper_trailing_activation_pct=2,
            paper_trailing_distance_pct=1.5,
            paper_entry_fee_bps=5.5,
            paper_exit_fee_bps=5.5,
            paper_slippage_bps=5,
            paper_funding_buffer_bps=1,
            dump_execution_quote_max_age_seconds=15,
        )

    @staticmethod
    def signal(symbol="AAAUSDT"):
        return SimpleNamespace(
            symbol=symbol,
            entry_bid=100,
            entry_quote_ts=1_000,
            model_version="dump-v5.2-confirmation",
            mode="SHORT_TREND",
            signal_score=8,
        )

    def test_stop_closes_both_shadow_strategies_after_costs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            broker = PaperBroker(str(Path(tmpdir) / "scanner.db"), self.settings)
            self.assertEqual(
                broker.open_signal(signal_id=1, signal=self.signal(), opened_ts=1_000),
                ["open", "open"],
            )
            quote = OrderbookQuote("AAAUSDT", 102.9, 10, 103, 10, 1_010)
            updated, closed = broker.update_open_positions(
                lambda _: quote,
                now=1_010,
            )
            self.assertEqual(updated, 0)
            self.assertEqual(closed, 2)
            for row in broker.summary():
                self.assertEqual(row["closed_count"], 1)
                self.assertLess(row["equity"], row["starting_equity"])
                self.assertLess(row["net_pnl"], 0)
            self.assertEqual(broker.runtime_summary()["heartbeat_count"], 1)

    def test_correlated_episode_is_skipped_for_every_strategy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            broker = PaperBroker(str(Path(tmpdir) / "scanner.db"), self.settings)
            broker.open_signal(signal_id=1, signal=self.signal(), opened_ts=1_000)
            result = broker.open_signal(
                signal_id=2,
                signal=self.signal("BBBUSDT"),
                opened_ts=1_100,
            )
            self.assertEqual(result, ["skipped_correlated_episode"] * 2)
            for row in broker.summary():
                self.assertEqual(row["open_count"], 1)
                self.assertEqual(row["skipped_count"], 1)

    def test_trailing_variant_closes_after_favorable_move_and_rebound(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            broker = PaperBroker(str(Path(tmpdir) / "scanner.db"), self.settings)
            broker.open_signal(signal_id=1, signal=self.signal(), opened_ts=1_000)
            favorable = OrderbookQuote("AAAUSDT", 96.9, 10, 97, 10, 1_010)
            broker.update_open_positions(lambda _: favorable, now=1_010)
            rebound = OrderbookQuote("AAAUSDT", 98.6, 10, 98.7, 10, 1_020)
            _, closed = broker.update_open_positions(lambda _: rebound, now=1_020)
            self.assertEqual(closed, 1)
            rows = {row["label"]: row for row in broker.summary()}
            trailing = next(row for label, row in rows.items() if "trailing" in label)
            timed = next(row for label, row in rows.items() if "выход" in label)
            self.assertEqual(trailing["closed_count"], 1)
            self.assertEqual(timed["open_count"], 1)
            self.assertIn("LIVE trading: заблокирован", format_paper_summary(broker))

    def test_successful_telegram_signal_opens_paper_positions_once(self):
        class Notifier:
            def send_message(self, text, reply_markup=None):
                return {"result": {"message_id": 1}}

        now = int(time.time())
        signal = SimpleNamespace(
            symbol="AAAUSDT",
            price=100,
            oi_change_pct=1,
            cvd_delta_usdt=-10_000,
            price_change_window_pct=-1,
            entry_bid=100,
            entry_ask=100.1,
            entry_price=100,
            entry_quote_ts=now,
            entry_spread_bps=10,
            entry_quote_status="ok",
            execution_venue="BYBIT",
            model_version="dump-v5.2-confirmation",
            mode="SHORT_TREND",
            signal_score=8,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "scanner.db")
            history = HistoryStore(db_path)
            broker = PaperBroker(db_path, self.settings)
            sent = send_signal_with_symbol_cooldown(
                notifier=Notifier(),
                history=history,
                settings=self.settings,
                signal=signal,
                signal_type="dump_binance",
                formatter=lambda _: "signal",
                paper_broker=broker,
            )
            self.assertTrue(sent)
            self.assertEqual(len(history.get_recent_signals()), 1)
            for row in broker.summary():
                self.assertEqual(row["open_count"], 1)


class BacktestSafetyTests(unittest.TestCase):
    def test_deduplication_requires_a_full_quiet_cooldown(self):
        events = [event(1, 0), event(2, 3 * 3_600), event(3, 6 * 3_600)]
        kept = deduplicate_events(events, cooldown_minutes=240)
        self.assertEqual([item.signal_id for item in kept], [1])

    def test_gate_cannot_pass_without_current_events_and_paper_observation(self):
        coverage = {
            "signals": 0,
            "executable_quotes": 0,
            "reviewed": 0,
            "execution_coverage_pct": 0.0,
            "review_coverage_pct": 0.0,
        }
        paper = {
            "observation_days": 0.0,
            "heartbeat_count": 0,
            "quote_error_count": 0,
            "loop_error_count": 0,
        }
        gate = automation_gate(current_trades=[], coverage=coverage, paper=paper)
        self.assertFalse(gate["passed"])
        failed = {check["name"] for check in gate["checks"] if not check["passed"]}
        self.assertIn("current_events", failed)
        self.assertIn("paper_observation", failed)


if __name__ == "__main__":
    unittest.main()
