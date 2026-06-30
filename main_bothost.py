from __future__ import annotations

import os
from pathlib import Path
import threading
import time
import traceback

from bybit_client import BybitClient
from binance_client import BinanceClient
from config import get_settings
from history import HistoryStore
from long_scanner import LongScanner
from pump_exhaustion_scanner import PumpExhaustionScanner
from state import StateStore
from telegram import (
    TelegramNotifier,
    format_pump_signal,
)

STATUS_LOCK = threading.Lock()
SCANNER_STATUS: dict[str, dict[str, object]] = {}


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


def update_status(scanner: str, result, reviewed: int) -> None:
    with STATUS_LOCK:
        SCANNER_STATUS[scanner] = {
            "updated_ts": int(time.time()),
            "symbols": result.scanned_symbols,
            "signals": len(result.signals),
            "watchlist": len(result.watchlist_alerts),
            "failed": result.failed_symbols,
            "reviews": reviewed,
            "rejections": format_rejections(result.rejection_reasons),
        }


def format_status_message() -> str:
    with STATUS_LOCK:
        snapshot = dict(SCANNER_STATUS)

    if not snapshot:
        return "Бот работает, но скан еще не завершался."

    lines = ["Статус сканера:"]
    now = int(time.time())
    for scanner in ("LONG", "PUMP"):
        data = snapshot.get(scanner)
        if not data:
            lines.append(f"\n{scanner}: еще нет данных")
            continue
        ago = now - int(data["updated_ts"])
        lines.append(
            "\n"
            f"{scanner}: обновлено {ago}s назад\n"
            f"Монет: {data['symbols']}, сигналов: {data['signals']}, "
            f"watchlist: {data['watchlist']}, ошибок: {data['failed']}\n"
            f"Причины отсечения: {data['rejections']}"
        )
    return "\n".join(lines)


def run_status_loop() -> None:
    settings = get_settings()
    if not settings.status_commands_enabled:
        return

    notifier = build_notifier(settings)
    offset = None
    while True:
        try:
            for update in notifier.get_updates(offset=offset, timeout_seconds=20):
                offset = int(update["update_id"]) + 1
                message = update.get("message") or {}
                text = str(message.get("text") or "").strip().lower()
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                if text.startswith("/status") and chat_id == str(settings.telegram_chat_id):
                    safe_send_message(notifier, format_status_message())
        except Exception as error:
            print(f"Status command loop error: {error}", flush=True)

        time.sleep(settings.status_poll_interval_seconds)


def run_long_loop() -> None:
    settings = get_settings()
    client = build_bybit_client(settings)
    binance_client = build_binance_client(settings)
    history = HistoryStore(data_path("scanner.db"))
    scanner = LongScanner(
        client,
        StateStore(data_path("state.json")),
        settings,
        history,
        binance_client,
    )
    scanner.store.load()
    notifier = build_notifier(settings)

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                safe_send_long_signal(notifier, signal)
            reviewed = history.update_signal_reviews()
            update_status("LONG", result, reviewed)
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
    binance_client = build_binance_client(settings)
    history = HistoryStore(data_path("scanner.db"))
    scanner = PumpExhaustionScanner(
        client,
        StateStore(data_path("pump_state.json")),
        settings,
        history,
        binance_client,
    )
    scanner.store.load()
    notifier = build_notifier(settings)

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                safe_send_message(notifier, format_pump_signal(signal))
            reviewed = history.update_signal_reviews()
            update_status("PUMP", result, reviewed)
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
    status_thread = threading.Thread(target=run_status_loop, name="status-commands", daemon=True)
    long_thread.start()
    pump_thread.start()
    status_thread.start()
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


def build_binance_client(settings) -> BinanceClient | None:
    if not settings.binance_confirm_enabled:
        return None
    return BinanceClient(
        settings.binance_base_url,
        verify_ssl=settings.verify_ssl,
        rate_limit_backoff_seconds=settings.bybit_rate_limit_backoff_seconds,
        max_retries=settings.bybit_max_retries,
    )


if __name__ == "__main__":
    main()
