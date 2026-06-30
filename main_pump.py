from __future__ import annotations

import argparse
import os
from pathlib import Path
import time
import traceback

from bybit_client import BybitClient
from config import get_settings
from history import HistoryStore
from pump_exhaustion_scanner import PumpExhaustionScanner
from state import StateStore
from telegram import TelegramNotifier, format_pump_signal


def safe_send(notifier: TelegramNotifier, text: str) -> None:
    try:
        notifier.send_message(text)
    except Exception as error:
        print(f"Telegram send failed: {error}")


def build_start_message(settings) -> str:
    return (
        "Pump Exhaustion scanner запущен.\n\n"
        f"Окно слабости: {settings.pump_window_minutes}m\n"
        f"Рост за {settings.pump_lookback_days}d от: +{settings.pump_min_price_growth_lookback_pct:g}%\n"
        f"Откат от high разгона от: -{settings.pump_min_drawdown_from_high_pct:g}%\n"
        f"OI должен быть не выше: {settings.pump_max_oi_change_pct:+g}%\n"
        f"Мин. падение OI: откат цены x {settings.pump_oi_drop_ratio_to_drawdown:g}\n"
        f"Порог CVD: -{settings.pump_min_negative_cvd_change_pct:g}%\n"
        f"Мин. negative CVD delta: -{settings.pump_min_negative_cvd_delta_usdt:,.0f} USDT\n"
        f"Цена за окно не выше: {settings.pump_max_price_change_window_pct:+g}%\n"
        f"Мин. оборот 24h: {settings.pump_min_turnover_24h_usdt:,.0f} USDT\n"
        f"Монет в скане: top {settings.pump_max_symbols}\n"
        f"Пауза между сканами: {settings.pump_scan_interval_seconds}s\n"
        f"Подтверждений подряд: {settings.pump_consecutive_checks}\n\n"
        "Фильтр: сильный рост 1-2 дня + откат от хая + OI стоит/падает + futures CVD уходит в минус."
    )


def data_path(filename: str) -> str:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir / filename)


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
    client = BybitClient(
        settings.bybit_base_url,
        verify_ssl=settings.verify_ssl,
        min_request_interval_seconds=settings.bybit_min_request_interval_seconds,
        rate_limit_backoff_seconds=settings.bybit_rate_limit_backoff_seconds,
        max_retries=settings.bybit_max_retries,
    )
    store = StateStore(data_path("pump_state.json"))
    store.load()

    scanner = PumpExhaustionScanner(
        client,
        store,
        settings,
        HistoryStore(data_path("scanner.db")),
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
                safe_send(notifier, format_pump_signal(signal))
            print(
                "Pump scan done: "
                f"symbols={result.scanned_symbols}, "
                f"signals={len(result.signals)}, "
                f"failed={result.failed_symbols}"
            )
        except Exception:
            if settings.debug_errors:
                traceback.print_exc()
            else:
                print("Pump scan error. Set DEBUG_ERRORS=true in .env for details.")

        if args.once:
            return

        time.sleep(settings.pump_scan_interval_seconds)


if __name__ == "__main__":
    main()
