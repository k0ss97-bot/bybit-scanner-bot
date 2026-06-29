from __future__ import annotations

import threading
import time
import traceback

from bybit_client import BybitClient
from config import get_settings
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


def run_long_loop() -> None:
    settings = get_settings()
    client = BybitClient(settings.bybit_base_url, verify_ssl=settings.verify_ssl)
    scanner = LongScanner(client, StateStore("state.json"), settings)
    scanner.store.load()
    notifier = build_notifier(settings)

    safe_send_message(notifier, "Bothost: LONG scanner запущен.")

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                safe_send_long_signal(notifier, signal)
            print(
                "Long scan done: "
                f"symbols={result.scanned_symbols}, "
                f"signals={len(result.signals)}, "
                f"failed={result.failed_symbols}",
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
    client = BybitClient(settings.bybit_base_url, verify_ssl=settings.verify_ssl)
    scanner = PumpExhaustionScanner(client, StateStore("pump_state.json"), settings)
    scanner.store.load()
    notifier = build_notifier(settings)

    safe_send_message(notifier, "Bothost: PUMP scanner запущен.")

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                safe_send_message(notifier, format_pump_signal(signal))
            print(
                "Pump scan done: "
                f"symbols={result.scanned_symbols}, "
                f"signals={len(result.signals)}, "
                f"failed={result.failed_symbols}",
                flush=True,
            )
        except Exception:
            if settings.debug_errors:
                traceback.print_exc()
            else:
                print("Pump scan error. Set DEBUG_ERRORS=true for details.", flush=True)

        time.sleep(settings.pump_scan_interval_seconds)


def main() -> None:
    long_thread = threading.Thread(target=run_long_loop, name="long-scanner")
    pump_thread = threading.Thread(target=run_pump_loop, name="pump-scanner")
    long_thread.start()
    pump_thread.start()
    long_thread.join()
    pump_thread.join()


if __name__ == "__main__":
    main()
