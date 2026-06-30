from __future__ import annotations

import os
from pathlib import Path
import threading
import time
import traceback

from bybit_client import BybitClient
from config import get_settings
from history import HistoryStore
from long_scanner import LongScanner
from pump_exhaustion_scanner import PumpExhaustionScanner
from state import StateStore
from telegram import TelegramNotifier, format_pump_signal


def safe_send_message(notifier: TelegramNotifier, text: str) -> None:
    try:
        notifier.send_message(text)
    except Exception as error:
        print(f"Telegram send failed: {error}", flush=True)


def safe_send_long_signal(notifier: TelegramNotifier, signal) -> None:
    try:
        notifier.send_signal(signal)
    except Exception as error:
        print(f"Telegram send failed: {error}", flush=True)


def build_notifier(settings) -> TelegramNotifier:
    return TelegramNotifier(
        settings.telegram_bot_token if settings.telegram_enabled else "",
        settings.telegram_chat_id if settings.telegram_enabled else "",
        timeout_seconds=5,
        verify_ssl=settings.verify_ssl,
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


def run_long_loop() -> None:
    settings = get_settings()
    client = build_bybit_client(settings)
    history = HistoryStore(data_path("scanner.db"))
    scanner = LongScanner(
        client,
        StateStore(data_path("state.json")),
        settings,
        history,
    )
    scanner.store.load()
    notifier = build_notifier(settings)

    if settings.startup_notifications:
        safe_send_message(notifier, "Bothost: LONG scanner запущен.")

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                safe_send_long_signal(notifier, signal)
            reviewed = history.update_signal_reviews()
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

        time.sleep(settings.scan_interval_seconds)


def run_pump_loop() -> None:
    settings = get_settings()
    client = build_bybit_client(settings)
    history = HistoryStore(data_path("scanner.db"))
    scanner = PumpExhaustionScanner(
        client,
        StateStore(data_path("pump_state.json")),
        settings,
        history,
    )
    scanner.store.load()
    notifier = build_notifier(settings)

    if settings.startup_notifications:
        safe_send_message(notifier, "Bothost: PUMP scanner запущен.")

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                safe_send_message(notifier, format_pump_signal(signal))
            reviewed = history.update_signal_reviews()
            print(
                "Pump scan done: "
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
                print("Pump scan error. Set DEBUG_ERRORS=true for details.", flush=True)

        time.sleep(settings.pump_scan_interval_seconds)


def main() -> None:
    settings = get_settings()
    print(
        "Config check: "
        f"telegram_enabled={settings.telegram_enabled}, "
        f"token_present={bool(settings.telegram_bot_token)}, "
        f"chat_id_present={bool(settings.telegram_chat_id)}",
        flush=True,
    )
    long_thread = threading.Thread(target=run_long_loop, name="long-scanner")
    pump_thread = threading.Thread(target=run_pump_loop, name="pump-scanner")
    long_thread.start()
    pump_thread.start()
    long_thread.join()
    pump_thread.join()


def build_bybit_client(settings) -> BybitClient:
    return BybitClient(
        settings.bybit_base_url,
        verify_ssl=settings.verify_ssl,
        min_request_interval_seconds=settings.bybit_min_request_interval_seconds,
        rate_limit_backoff_seconds=settings.bybit_rate_limit_backoff_seconds,
        max_retries=settings.bybit_max_retries,
    )


if __name__ == "__main__":
    main()
