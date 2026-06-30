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


def menu_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "📊 Статус"}, {"text": "⚙️ Настройки"}],
            [{"text": "📈 Статистика"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def safe_send_message(
    notifier: TelegramNotifier,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    try:
        notifier.send_message(text, reply_markup=reply_markup)
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
            "stage": "done",
            "updated_ts": int(time.time()),
            "symbols": result.scanned_symbols,
            "signals": len(result.signals),
            "watchlist": len(result.watchlist_alerts),
            "failed": result.failed_symbols,
            "reviews": reviewed,
            "rejections": format_rejections(result.rejection_reasons),
        }


def update_scanning_status(scanner: str, current: int | None = None, total: int | None = None) -> None:
    with STATUS_LOCK:
        previous = SCANNER_STATUS.get(scanner, {})
        SCANNER_STATUS[scanner] = {
            **previous,
            "stage": "scanning",
            "started_ts": previous.get("started_ts", int(time.time())),
            "current": current if current is not None else previous.get("current", 0),
            "total": total if total is not None else previous.get("total", 0),
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
        if data.get("stage") == "scanning":
            started_ago = now - int(data.get("started_ts", now))
            previous_rejections = data.get("rejections", "еще нет")
            current = int(data.get("current", 0))
            total = int(data.get("total", 0))
            progress = f"{current}/{total}" if total else "подготовка"
            lines.append(
                "\n"
                f"{scanner}: скан идет {started_ago}s, прогресс {progress}\n"
                f"Последние причины отсечения: {previous_rejections}"
            )
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


def format_settings_message(settings) -> str:
    return (
        "Текущие настройки:\n\n"
        "Общее:\n"
        f"MAX_SYMBOLS={settings.max_symbols}\n"
        f"PUMP_MAX_SYMBOLS={settings.pump_max_symbols}\n"
        f"SCAN_INTERVAL_SECONDS={settings.scan_interval_seconds}\n"
        f"PUMP_SCAN_INTERVAL_SECONDS={settings.pump_scan_interval_seconds}\n"
        f"BYBIT_MIN_REQUEST_INTERVAL_SECONDS={settings.bybit_min_request_interval_seconds:g}\n"
        f"SPOT_CVD_UPDATE_INTERVAL_SECONDS={settings.spot_cvd_update_interval_seconds}\n\n"
        "LONG:\n"
        f"OI_THRESHOLD_PCT={settings.oi_threshold_pct:g}\n"
        f"CVD_THRESHOLD_PCT={settings.cvd_threshold_pct:g}\n"
        f"MIN_CVD_DELTA_USDT={settings.min_cvd_delta_usdt:g}\n"
        f"MIN_TURNOVER_24H_USDT={settings.min_turnover_24h_usdt:g}\n"
        f"LONG_LOOKBACK_DAYS={settings.long_lookback_days}\n"
        f"LONG_MAX_PRICE_GROWTH_LOOKBACK_PCT={settings.long_max_price_growth_lookback_pct:g}\n"
        f"LONG_MIN_TURNOVER_RATIO_TO_BASE={settings.long_min_turnover_ratio_to_base:g}\n"
        f"LONG_MIN_SIGNAL_SCORE={settings.long_min_signal_score}\n\n"
        "PUMP:\n"
        f"PUMP_MIN_TURNOVER_24H_USDT={settings.pump_min_turnover_24h_usdt:g}\n"
        f"PUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT={settings.pump_min_price_growth_lookback_pct:g}\n"
        f"PUMP_MIN_DRAWDOWN_FROM_HIGH_PCT={settings.pump_min_drawdown_from_high_pct:g}\n"
        f"PUMP_MIN_SIGNAL_SCORE={settings.pump_min_signal_score}\n\n"
        "Фильтры:\n"
        f"BINANCE_CONFIRM_ENABLED={str(settings.binance_confirm_enabled).lower()}\n"
        f"BINANCE_CONFIRMATION_REQUIRED={str(settings.binance_confirmation_required).lower()}\n"
        f"WATCHLIST_ENABLED={str(settings.watchlist_enabled).lower()}\n"
        f"STATUS_COMMANDS_ENABLED={str(settings.status_commands_enabled).lower()}"
    )


def format_stats_message(history: HistoryStore) -> str:
    reviewed = history.update_signal_reviews()
    rows = history.get_signal_stats()
    recent = history.get_recent_signals(limit=5)

    lines = ["Статистика сигналов:"]
    if reviewed:
        lines.append(f"Новых расчетов результата: {reviewed}")

    if rows:
        for (
            signal_type,
            horizon_minutes,
            total,
            avg_move_pct,
            avg_max_favorable_pct,
            avg_max_adverse_pct,
            positive_count,
        ) in rows:
            win_rate = (positive_count / total) * 100 if total else 0
            lines.append(
                f"{signal_type} {horizon_minutes}m: "
                f"сигналов={total}, "
                f"winrate={win_rate:.1f}%, "
                f"средн={avg_move_pct:+.2f}%, "
                f"лучшее={avg_max_favorable_pct:+.2f}%, "
                f"просадка={avg_max_adverse_pct:+.2f}%"
            )
    else:
        lines.append("Пока нет рассчитанных результатов. Нужно дождаться 1ч/4ч/24ч после сигналов.")

    lines.append("\nПоследние сигналы:")
    if not recent:
        lines.append("Пока нет сигналов.")
        return "\n".join(lines)

    now = int(time.time())
    for signal_id, signal_type, symbol, ts, price, price_change_pct in recent:
        age_minutes = int((now - ts) / 60)
        lines.append(
            f"#{signal_id} {signal_type} {symbol}: "
            f"цена={price:g}, окно={price_change_pct:+.2f}%, возраст={age_minutes}m"
        )
    return "\n".join(lines)


def is_status_request(text: str) -> bool:
    return text.startswith("/status") or text in {"статус", "📊 статус"}


def is_settings_request(text: str) -> bool:
    return text.startswith("/settings") or text in {"настройки", "⚙️ настройки"}


def is_stats_request(text: str) -> bool:
    return text.startswith("/stats") or text in {"статистика", "📈 статистика"}


def is_menu_request(text: str) -> bool:
    return text.startswith("/start") or text in {"меню", "/menu", "кнопки"}


def run_status_loop() -> None:
    settings = get_settings()
    if not settings.status_commands_enabled:
        return

    notifier = build_notifier(settings)
    history = HistoryStore(data_path("scanner.db"))
    offset = None
    while True:
        try:
            for update in notifier.get_updates(offset=offset, timeout_seconds=20):
                offset = int(update["update_id"]) + 1
                message = update.get("message") or {}
                text = str(message.get("text") or "").strip().lower()
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                if chat_id != str(settings.telegram_chat_id):
                    continue
                if is_status_request(text):
                    safe_send_message(notifier, format_status_message(), menu_keyboard())
                elif is_settings_request(text):
                    safe_send_message(notifier, format_settings_message(settings), menu_keyboard())
                elif is_stats_request(text):
                    safe_send_message(notifier, format_stats_message(history), menu_keyboard())
                elif is_menu_request(text):
                    safe_send_message(
                        notifier,
                        "Кнопки включены. Выбери действие ниже.",
                        menu_keyboard(),
                    )
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
            update_scanning_status("LONG")
            result = scanner.scan_once(
                progress_callback=lambda current, total: update_scanning_status(
                    "LONG",
                    current,
                    total,
                )
            )
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
            update_scanning_status("PUMP")
            result = scanner.scan_once(
                progress_callback=lambda current, total: update_scanning_status(
                    "PUMP",
                    current,
                    total,
                )
            )
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
