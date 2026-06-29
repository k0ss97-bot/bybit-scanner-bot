from __future__ import annotations

import argparse
import time
import traceback

from bybit_client import BybitClient
from config import get_settings
from long_scanner import LongScanner
from state import StateStore
from telegram import TelegramNotifier


def safe_send(notifier: TelegramNotifier, text: str) -> None:
    try:
        notifier.send_message(text)
    except Exception as error:
        print(f"Telegram send failed: {error}")


def safe_send_signal(notifier: TelegramNotifier, signal) -> None:
    try:
        notifier.send_signal(signal)
    except Exception as error:
        print(f"Telegram send failed: {error}")


def build_start_message(settings) -> str:
    return (
        "Bybit LONG scanner запущен.\n\n"
        f"Окно: {settings.window_minutes}m\n"
        f"Порог OI: +{settings.oi_threshold_pct:g}%\n"
        f"Порог CVD: +{settings.cvd_threshold_pct:g}%\n"
        f"Мин. CVD delta: {settings.min_cvd_delta_usdt:,.0f} USDT\n"
        f"Мин. оборот 24h: {settings.min_turnover_24h_usdt:,.0f} USDT\n"
        f"Монет в скане: top {settings.max_symbols}\n"
        f"Пауза между сканами: {settings.scan_interval_seconds}s\n"
        f"Cooldown на монету: {settings.alert_cooldown_minutes}m\n\n"
        f"Мин. новых сделок: {settings.min_new_trades}\n"
        f"Подтверждений подряд: {settings.consecutive_checks}\n"
        f"Мин. изменение цены: {settings.price_min_change_pct:+g}%\n\n"
        "Фильтр: OI растет + CVD растет + цена удерживается. Funding только справочно."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one scan and stop.")
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Send one Telegram test message and stop.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    client = BybitClient(settings.bybit_base_url, verify_ssl=settings.verify_ssl)
    store = StateStore()
    store.load()

    scanner = LongScanner(client, store, settings)
    notifier = TelegramNotifier(
        settings.telegram_bot_token if settings.telegram_enabled else "",
        settings.telegram_chat_id if settings.telegram_enabled else "",
        timeout_seconds=5,
        verify_ssl=settings.verify_ssl,
    )

    safe_send(notifier, build_start_message(settings))
    if args.test_telegram:
        return

    while True:
        try:
            result = scanner.scan_once()
            for signal in result.signals:
                safe_send_signal(notifier, signal)
            print(
                "Scan done: "
                f"symbols={result.scanned_symbols}, "
                f"signals={len(result.signals)}, "
                f"failed={result.failed_symbols}"
            )
        except Exception:
            if settings.debug_errors:
                traceback.print_exc()
            else:
                print("Scan error. Set DEBUG_ERRORS=true in .env for details.")

        if args.once:
            return

        time.sleep(settings.scan_interval_seconds)


if __name__ == "__main__":
    main()
