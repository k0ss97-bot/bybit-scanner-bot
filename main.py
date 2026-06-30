from __future__ import annotations

import argparse
import os
from pathlib import Path
import time
import traceback

from bybit_client import BybitClient
from binance_client import BinanceClient
from config import get_settings
from history import HistoryStore
from long_scanner import LongScanner
from state import StateStore
from telegram import TelegramNotifier, format_long_watchlist


def safe_send(notifier: TelegramNotifier, text: str) -> None:
    try:
        notifier.send_message(text)
    except Exception as error:
        print(f"Telegram send failed: {error}", flush=True)


def build_start_message(settings) -> str:
    return (
        "LONG scanner запущен.\n\n"
        f"Окно: {settings.window_minutes}m\n"
        f"OI от: +{settings.oi_threshold_pct:g}%\n"
        f"Futures CVD от: +{settings.cvd_threshold_pct:g}%\n"
        f"Мин. CVD delta: {settings.min_cvd_delta_usdt:,.0f} USDT\n"
        f"Мин. оборот 24h: {settings.min_turnover_24h_usdt:,.0f} USDT\n"
        f"Монет в скане: top {settings.max_symbols}\n"
        f"Пауза между сканами: {settings.scan_interval_seconds}s\n"
        f"Подтверждений подряд: {settings.consecutive_checks}"
    )


def data_path(filename: str) -> str:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / filename)


def format_rejections(rejection_reasons: dict[str, int], limit: int = 5) -> str:
    if not rejection_reasons:
        return "none"

    items = sorted(rejection_reasons.items(), key=lambda item: item[1], reverse=True)
    return ", ".join(f"{reason}={count}" for reason, count in items[:limit])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one scan and stop.")
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Send one Telegram test message and stop.",
    )
    return parser.parse_args()


def build_bybit_client(settings) -> BybitClient:
    return BybitClient(
        settings.bybit_base_url,
        verify_ssl=settings.verify_ssl,
        min_request_interval_seconds=settings.bybit_min_request_interval_seconds,
        rate_limit_backoff_seconds=settings.bybit_rate_limit_backoff_seconds,
        max_retries=settings.bybit_max_retries,
    )


def build_binance_client(settings) -> BinanceClient | None:
    if not settings.binance_confirm_enabled:
        return None
    return BinanceClient(
        settings.binance_base_url,
        verify_ssl=settings.verify_ssl,
        rate_limit_backoff_seconds=settings.bybit_rate_limit_backoff_seconds,
        max_retries=settings.bybit_max_retries,
    )


def main() -> None:
    args = parse_args()
    settings = get_settings()
    client = build_bybit_client(settings)
    binance_client = build_binance_client(settings)
    store = StateStore(data_path("state.json"))
    store.load()

    scanner = LongScanner(
        client,
        store,
        settings,
        HistoryStore(data_path("scanner.db")),
        binance_client,
    )
    notifier = TelegramNotifier(
        settings.telegram_bot_token if settings.telegram_enabled else "",
        settings.telegram_chat_id if settings.telegram_enabled else "",
        timeout_seconds=5,
        verify_ssl=settings.verify_ssl,
    )

    if args.test_telegram or settings.startup_notifications:
        safe_send(notifier, build_start_message(settings))
    if args.test_telegram:
        return

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                notifier.send_signal(signal)
            for alert in result.watchlist_alerts:
                safe_send(notifier, format_long_watchlist(alert))
            reviewed = scanner.history.update_signal_reviews() if scanner.history is not None else 0
            print(
                "Long scan done: "
                f"symbols={result.scanned_symbols}, "
                f"signals={len(result.signals)}, "
                f"failed={result.failed_symbols}, "
                f"reviews={reviewed}, "
                f"rejections={format_rejections(result.rejection_reasons)}",
                flush=True,
            )
        except Exception:
            if settings.debug_errors:
                traceback.print_exc()
            else:
                print("Long scan error. Set DEBUG_ERRORS=true for details.", flush=True)

        if args.once:
            return

        time.sleep(settings.scan_interval_seconds)


if __name__ == "__main__":
    main()
