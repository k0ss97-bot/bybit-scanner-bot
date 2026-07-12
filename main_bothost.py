from __future__ import annotations

import os
from pathlib import Path
import signal
import threading
import time
import traceback

from bybit_client import BybitClient
from binance_client import BinanceClient
from chart_renderer import render_dump_chart
from config import get_settings
from dump_scanner import DumpScanner
from history import HistoryStore
from state import StateStore
from telegram import (
    TelegramNotifier,
    format_dump_signal,
)

STATUS_LOCK = threading.Lock()
SCANNER_STATUS: dict[str, dict[str, object]] = {}
SCANNERS = ("DUMP BYBIT", "DUMP BINANCE")
SCANNER_PAUSED = {scanner: False for scanner in SCANNERS}
WARNING_LOCK = threading.Lock()
LAST_WARNING_TS: dict[str, int] = {}
STOP_EVENT = threading.Event()


def menu_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "📊 Статус"}, {"text": "⚙️ Настройки"}],
            [{"text": "📈 Статистика"}, {"text": "❓ Почему нет сигналов"}],
            [{"text": "🎯 Ближайшие"}, {"text": "🕘 Последние сигналы"}],
            [{"text": "🔻 DUMP BYBIT"}, {"text": "🔻 DUMP BINANCE"}],
            [{"text": "⏸ Пауза"}, {"text": "▶️ Старт"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def safe_send_message(
    notifier: TelegramNotifier,
    text: str,
    reply_markup: dict | None = None,
) -> bool:
    try:
        notifier.send_message(text, reply_markup=reply_markup)
        return True
    except Exception as error:
        print(f"Telegram send failed: {error}", flush=True)
        return False


def safe_send_photo(notifier: TelegramNotifier, photo: bytes, caption: str) -> bool:
    try:
        notifier.send_photo(photo, caption=caption)
        return True
    except Exception as error:
        print(f"Telegram photo send failed: {error}", flush=True)
        return False


def request_stop(reason: str) -> None:
    if not STOP_EVENT.is_set():
        print(f"Shutdown requested: {reason}", flush=True)
    STOP_EVENT.set()


def install_signal_handlers() -> None:
    def handle_stop(signum, _frame) -> None:
        request_stop(f"signal {signum}")

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handle_stop)


def wait_or_stop(seconds: float) -> bool:
    return STOP_EVENT.wait(max(0.0, seconds))


def send_signal_with_symbol_cooldown(
    *,
    notifier: TelegramNotifier,
    history: HistoryStore,
    settings,
    signal,
    signal_type: str,
    formatter,
    chart_renderer=None,
) -> bool:
    symbol = str(getattr(signal, "symbol", ""))
    now = int(time.time())
    allowed, previous_type, previous_ts = history.claim_telegram_symbol_alert(
        symbol=symbol,
        ts=now,
        signal_type=signal_type,
        cooldown_minutes=settings.telegram_symbol_cooldown_minutes,
    )
    if not allowed:
        age_minutes = int((now - int(previous_ts or now)) / 60)
        print(
            f"Telegram skip {symbol}: cooldown after {previous_type}, age={age_minutes}m",
            flush=True,
        )
        history.release_dump_symbol_alert(
            symbol=symbol,
            source=signal_type.removeprefix("dump_").upper(),
        )
        return False

    if not safe_send_message(notifier, formatter(signal)):
        history.release_telegram_symbol_alert(symbol=symbol, ts=now)
        history.release_dump_symbol_alert(
            symbol=symbol,
            source=signal_type.removeprefix("dump_").upper(),
        )
        return False

    source_prefix = signal_type.removeprefix("dump_").upper()
    history.record_signal(
        signal_type=signal_type,
        symbol=f"{source_prefix}:{symbol}",
        ts=now,
        price=signal.price,
        open_interest_change_pct=signal.oi_change_pct,
        futures_cvd_change_pct=0,
        futures_cvd_delta_usdt=signal.cvd_delta_usdt,
        spot_cvd_change_pct=0,
        spot_cvd_delta_usdt=0,
        price_change_pct=signal.price_change_window_pct,
        payload=str(signal),
    )

    if chart_renderer is not None and settings.dump_chart_enabled:
        try:
            chart = chart_renderer(signal)
            mode_title = {
                "LIQUIDATION_FLUSH": "Liquidation flush",
                "SHORT_TREND": "Short trend",
            }.get(signal.mode, signal.mode)
            safe_send_photo(
                notifier,
                chart,
                f"{signal.symbol} | {mode_title} | score {signal.signal_score}/10",
            )
        except Exception as error:
            print(f"Chart render failed for {symbol}: {error}", flush=True)
    return True


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


class BybitSymbolCache:
    def __init__(self, client: BybitClient, ttl_minutes: int) -> None:
        self.client = client
        self.ttl_seconds = max(1, ttl_minutes) * 60
        self.symbols: set[str] = set()
        self.loaded_ts = 0

    def get_symbols(self) -> set[str]:
        now = int(time.time())
        if self.symbols and now - self.loaded_ts < self.ttl_seconds:
            return set(self.symbols)

        try:
            tickers = self.client.get_linear_tickers()
            symbols = {
                ticker.symbol
                for ticker in tickers
                if ticker.symbol.endswith("USDT") and ticker.price > 0
            }
            if symbols:
                self.symbols = symbols
                self.loaded_ts = now
                print(f"Bybit symbol cache updated: symbols={len(symbols)}", flush=True)
        except Exception as error:
            print(f"Bybit symbol cache update failed: {error}", flush=True)

        return set(self.symbols)


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
            "screened": result.screened_symbols,
            "signals": len(result.signals),
            "watchlist": len(result.watchlist_alerts),
            "failed": result.failed_symbols,
            "skipped": result.skipped_symbols,
            "reviews": reviewed,
            "rejections": format_rejections(result.rejection_reasons),
            "rejection_reasons": dict(result.rejection_reasons),
            "closest": format_closest_alerts(result.watchlist_alerts),
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


def update_paused_status(scanner: str) -> None:
    with STATUS_LOCK:
        previous = SCANNER_STATUS.get(scanner, {})
        SCANNER_STATUS[scanner] = {
            **previous,
            "stage": "paused",
            "updated_ts": int(time.time()),
        }


def is_paused(scanner: str) -> bool:
    return bool(SCANNER_PAUSED.get(scanner, False))


def set_paused(value: bool) -> None:
    for scanner in SCANNER_PAUSED:
        SCANNER_PAUSED[scanner] = value


def format_closest_alerts(alerts, limit: int = 5) -> list[str]:
    if not alerts:
        return []

    sorted_alerts = sorted(
        alerts,
        key=lambda alert: (getattr(alert, "signal_score", 0), len(getattr(alert, "passed_checks", []))),
        reverse=True,
    )
    lines = []
    for alert in sorted_alerts[:limit]:
        missing = ", ".join(getattr(alert, "missing_checks", [])[:4]) or "none"
        source = getattr(alert, "source", "")
        prefix = f"{source} " if source else ""
        lines.append(
            f"{prefix}{alert.symbol} score={alert.signal_score}/10 "
            f"price={alert.price:g} не хватает: {missing}"
        )
    return lines


def format_status_message() -> str:
    with STATUS_LOCK:
        snapshot = dict(SCANNER_STATUS)

    if not snapshot:
        return "Бот работает, но скан еще не завершался."

    lines = ["Статус сканера:"]
    now = int(time.time())
    for scanner in SCANNERS:
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
            f"Проверено: {data.get('screened', data['symbols'])}, глубоко: {data['symbols']}, "
            f"сигналов: {data['signals']}, "
            f"почти сигналов: {data['watchlist']}, "
            f"пропущено: {data.get('skipped', 0)}, ошибок: {data['failed']}\n"
            f"Причины отсечения: {data['rejections']}"
        )
    return "\n".join(lines)


def format_single_status_message(scanner: str) -> str:
    scanner = scanner.upper()
    with STATUS_LOCK:
        data = dict(SCANNER_STATUS.get(scanner, {}))

    if not data:
        return f"{scanner}: еще нет данных."

    now = int(time.time())
    if data.get("stage") == "paused":
        return f"{scanner}: на паузе."
    if data.get("stage") == "scanning":
        started_ago = now - int(data.get("started_ts", now))
        current = int(data.get("current", 0))
        total = int(data.get("total", 0))
        progress = f"{current}/{total}" if total else "подготовка"
        return (
            f"{scanner}: скан идет {started_ago}s, прогресс {progress}\n"
            f"Последние причины отсечения: {data.get('rejections', 'еще нет')}"
        )

    ago = now - int(data.get("updated_ts", now))
    return (
        f"{scanner}: обновлено {ago}s назад\n"
        f"Проверено: {data.get('screened', data.get('symbols', 0))}, "
        f"глубоко: {data.get('symbols', 0)}, сигналов: {data.get('signals', 0)}, "
        f"почти сигналов: {data.get('watchlist', 0)}, "
        f"пропущено: {data.get('skipped', 0)}, ошибок: {data.get('failed', 0)}\n"
        f"Причины отсечения: {data.get('rejections', 'нет данных')}"
    )


def format_settings_message(settings) -> str:
    return (
        "Текущие настройки:\n\n"
        "Общее:\n"
        f"BYBIT_MIN_REQUEST_INTERVAL_SECONDS={settings.bybit_min_request_interval_seconds:g}\n"
        f"HISTORY_SNAPSHOT_RETENTION_DAYS={settings.history_snapshot_retention_days}\n"
        f"WATCHLIST_RETENTION_DAYS={settings.watchlist_retention_days}\n"
        f"TELEGRAM_SYMBOL_COOLDOWN_MINUTES={settings.telegram_symbol_cooldown_minutes}\n\n"
        "DUMP:\n"
        f"DUMP_ENABLED={str(settings.dump_enabled).lower()}\n"
        f"DUMP_SCAN_INTERVAL_SECONDS={settings.dump_scan_interval_seconds}\n"
        f"DUMP_WINDOW_MINUTES={settings.dump_window_minutes}\n"
        f"DUMP_LOOKBACK_DAYS={settings.dump_lookback_days}\n"
        f"DUMP_MIN_TURNOVER_24H_USDT={settings.dump_min_turnover_24h_usdt:g}\n"
        f"DUMP_MAX_SYMBOLS={settings.dump_max_symbols}\n"
        f"DUMP_DEEP_MAX_SYMBOLS={settings.dump_deep_max_symbols}\n"
        f"DUMP_REQUIRE_BYBIT_LISTING={str(settings.dump_require_bybit_listing).lower()}\n"
        f"DUMP_BYBIT_SYMBOL_CACHE_MINUTES={settings.dump_bybit_symbol_cache_minutes}\n"
        f"DUMP_EVALUATION_ENABLED={str(settings.dump_evaluation_enabled).lower()}\n"
        f"DUMP_MAX_EVALUATION_SYMBOLS={settings.dump_max_evaluation_symbols}\n"
        f"DUMP_TRADE_MAX_PAGES={settings.dump_trade_max_pages}\n"
        f"DUMP_CROSS_EXCHANGE_REQUIRED={str(settings.dump_cross_exchange_required).lower()}\n"
        f"DUMP_CROSS_EXCHANGE_MAX_AGE_SECONDS={settings.dump_cross_exchange_max_age_seconds}\n"
        f"DUMP_LIQUIDATION_MIN_OI_DROP_PCT={settings.dump_liquidation_min_oi_drop_pct:g}\n"
        f"DUMP_TREND_MIN_OI_CHANGE_PCT={settings.dump_trend_min_oi_change_pct:g}\n"
        f"DUMP_CHART_ENABLED={str(settings.dump_chart_enabled).lower()}\n"
        f"DUMP_CHART_LOOKBACK_HOURS={settings.dump_chart_lookback_hours}\n"
        f"DUMP_CHART_INTERVAL={settings.dump_chart_interval}\n"
        f"DUMP_STRUCTURE_CACHE_MINUTES={settings.dump_structure_cache_minutes}\n"
        f"DUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT={settings.dump_min_price_growth_lookback_pct:g}\n"
        f"DUMP_MIN_DRAWDOWN_FROM_HIGH_PCT={settings.dump_min_drawdown_from_high_pct:g}\n"
        f"DUMP_MIN_PRICE_DROP_WINDOW_PCT={settings.dump_min_price_drop_window_pct:g}\n"
        f"DUMP_MIN_NEGATIVE_CVD_DELTA_USDT={settings.dump_min_negative_cvd_delta_usdt:g}\n"
        f"DUMP_MAX_OI_DROP_WINDOW_PCT={settings.dump_max_oi_drop_window_pct:g}\n"
        f"DUMP_MAX_FUNDING_RATE={settings.dump_max_funding_rate:g}\n"
        f"DUMP_SYMBOL_COOLDOWN_MINUTES={settings.dump_symbol_cooldown_minutes}\n"
        f"DUMP_MIN_SIGNAL_SCORE={settings.dump_min_signal_score}\n"
        f"DUMP_WATCHLIST_MIN_SCORE={settings.dump_watchlist_min_score}\n\n"
        "Фильтры:\n"
        f"CANDIDATE_TRACKING_ENABLED={str(settings.candidate_tracking_enabled).lower()}\n"
        f"WATCHLIST_MAX_ALERTS_PER_SCAN={settings.watchlist_max_alerts_per_scan}\n"
        f"WATCHLIST_COOLDOWN_MINUTES={settings.watchlist_cooldown_minutes}\n"
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
        lines.append("Пока нет рассчитанных результатов. Нужно дождаться 15/30/60/240 минут после сигналов.")

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


def format_rejection_details_message(
    scanner_filter: str | None = None,
    history: HistoryStore | None = None,
) -> str:
    with STATUS_LOCK:
        snapshot = dict(SCANNER_STATUS)

    if not snapshot:
        return "Пока нет данных. Дождись завершения первого скана."

    lines = ["Почему нет сигналов:"]
    scanners = (scanner_filter,) if scanner_filter else SCANNERS
    for scanner in scanners:
        data = snapshot.get(scanner)
        if not data:
            lines.append(f"\n{scanner}: еще нет данных")
            continue

        reasons = data.get("rejection_reasons")
        if isinstance(reasons, dict) and reasons:
            items = sorted(reasons.items(), key=lambda item: item[1], reverse=True)
            lines.append(f"\n{scanner}:")
            for reason, count in items[:10]:
                lines.append(f"{reason}: {count}")
        else:
            text_reasons = str(data.get("rejections", "none"))
            if text_reasons == "none":
                lines.append(
                    f"\n{scanner}: причин пока нет. Дождись завершения полного скана."
                )
            else:
                lines.append(f"\n{scanner}: {text_reasons}")

        closest = data.get("closest", [])
        if isinstance(closest, list) and closest:
            lines.append("Ближайшие:")
            lines.extend(str(item) for item in closest[:5])

        if history is not None:
            hidden = history.get_recent_scanner_evaluations(
                scanner=scanner_key(scanner),
                status="outside_top_symbols",
                limit=5,
            )
            if hidden:
                lines.append("Вне top лимита, но уже видны в диагностике:")
                now = int(time.time())
                for _, source, symbol, ts, rank, status, reason, score, price, turnover, missing in hidden:
                    age_minutes = int((now - ts) / 60)
                    lines.append(
                        f"{source} {symbol}: rank={rank}, цена={price:g}, "
                        f"оборот={turnover:,.0f}, возраст={age_minutes}m, причина={reason}"
                    )

    return "\n".join(lines)


def scanner_key(scanner: str) -> str:
    return scanner.lower().replace(" ", "_")


def format_closest_message(history: HistoryStore) -> str:
    with STATUS_LOCK:
        snapshot = dict(SCANNER_STATUS)

    lines = ["Ближайшие к сигналу:"]
    has_live = False
    for scanner in SCANNERS:
        data = snapshot.get(scanner, {})
        closest = data.get("closest", [])
        lines.append(f"\n{scanner}:")
        if isinstance(closest, list) and closest:
            has_live = True
            lines.extend(str(item) for item in closest[:5])
        else:
            lines.append("пока нет кандидатов из последнего скана")

    if has_live:
        return "\n".join(lines)

    recent = history.get_recent_watchlist_candidates(limit=5)
    if not recent:
        lines.append("\nВ базе тоже пока нет почти сигналов.")
        return "\n".join(lines)

    lines.append("\nПоследние почти сигналы из базы:")
    now = int(time.time())
    for scanner, symbol, ts, score, price, passed_checks, missing_checks in recent:
        age_minutes = int((now - ts) / 60)
        missing = missing_checks or "none"
        lines.append(
            f"{scanner} {symbol}: score={score}/10, цена={price:g}, "
            f"возраст={age_minutes}m, не хватает: {missing}"
        )
    return "\n".join(lines)


def format_recent_signals_message(history: HistoryStore, limit: int = 10) -> str:
    recent = history.get_recent_signals(limit=limit)
    if not recent:
        return "Пока нет сигналов."

    now = int(time.time())
    lines = [f"Последние {len(recent)} сигналов:"]
    for signal_id, signal_type, symbol, ts, price, price_change_pct in recent:
        age_minutes = int((now - ts) / 60)
        lines.append(
            f"#{signal_id} {signal_type} {symbol}: "
            f"цена={price:g}, окно={price_change_pct:+.2f}%, возраст={age_minutes}m"
        )
    return "\n".join(lines)


def is_status_request(text: str) -> bool:
    return text.startswith("/status") or text in {"статус", "📊 статус"}


def status_target(text: str) -> str | None:
    if text.startswith("/status dump bybit") or text in {"dump bybit", "🔻 dump bybit"}:
        return "DUMP BYBIT"
    if text.startswith("/status dump binance") or text in {"dump binance", "🔻 dump binance"}:
        return "DUMP BINANCE"
    if text.startswith("/status dump"):
        return "DUMP BINANCE"
    return None


def is_settings_request(text: str) -> bool:
    return text.startswith("/settings") or text in {"настройки", "⚙️ настройки"}


def is_stats_request(text: str) -> bool:
    return text.startswith("/stats") or text in {"статистика", "📈 статистика"}


def is_rejections_request(text: str) -> bool:
    return (
        text.startswith("/why")
        or text in {
            "почему нет сигналов",
            "❓ почему нет сигналов",
            "нет сигналов",
            "почему",
        }
    )


def rejection_target(text: str) -> str | None:
    if text.startswith("/why dump bybit"):
        return "DUMP BYBIT"
    if text.startswith("/why dump"):
        return "DUMP BINANCE"
    return None


def is_recent_signals_request(text: str) -> bool:
    return (
        text.startswith("/last")
        or text in {
            "последние сигналы",
            "🕘 последние сигналы",
            "последние",
        }
    )


def is_closest_request(text: str) -> bool:
    return (
        text.startswith("/closest")
        or text in {
            "ближайшие",
            "🎯 ближайшие",
            "почти сигналы",
            "кандидаты",
        }
    )


def is_pause_request(text: str) -> bool:
    return text.startswith("/pause") or text in {"пауза", "⏸ пауза"}


def is_start_request(text: str) -> bool:
    return text.startswith("/resume") or text in {"старт", "▶️ старт", "продолжить"}


def is_menu_request(text: str) -> bool:
    return text.startswith("/start") or text in {"меню", "/menu", "кнопки"}


def maybe_send_rate_warning(
    scanner: str,
    failed_symbols: int,
    notifier: TelegramNotifier,
    threshold: int = 5,
    cooldown_seconds: int = 1800,
) -> None:
    if failed_symbols < threshold:
        return
    now = int(time.time())
    with WARNING_LOCK:
        last_ts = LAST_WARNING_TS.get(scanner, 0)
        if now - last_ts < cooldown_seconds:
            return
        LAST_WARNING_TS[scanner] = now
    safe_send_message(
        notifier,
        f"{scanner}: много ошибок за скан ({failed_symbols}). "
        "Если это Bybit rate limit, увеличь интервалы скана или паузу между запросами.",
        menu_keyboard(),
    )


def run_status_loop() -> None:
    settings = get_settings()
    if not settings.status_commands_enabled:
        return

    notifier = build_notifier(settings)
    history = HistoryStore(data_path("scanner.db"))
    offset = None
    while not STOP_EVENT.is_set():
        try:
            for update in notifier.get_updates(offset=offset, timeout_seconds=20):
                offset = int(update["update_id"]) + 1
                message = update.get("message") or {}
                text = str(message.get("text") or "").strip().lower()
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                if chat_id != str(settings.telegram_chat_id):
                    continue
                target = status_target(text)
                why_target = rejection_target(text)
                if target is not None:
                    safe_send_message(notifier, format_single_status_message(target), menu_keyboard())
                elif is_status_request(text):
                    safe_send_message(notifier, format_status_message(), menu_keyboard())
                elif is_settings_request(text):
                    safe_send_message(notifier, format_settings_message(settings), menu_keyboard())
                elif is_stats_request(text):
                    safe_send_message(notifier, format_stats_message(history), menu_keyboard())
                elif why_target is not None:
                    safe_send_message(
                        notifier,
                        format_rejection_details_message(why_target, history),
                        menu_keyboard(),
                    )
                elif is_rejections_request(text):
                    safe_send_message(
                        notifier,
                        format_rejection_details_message(history=history),
                        menu_keyboard(),
                    )
                elif is_recent_signals_request(text):
                    safe_send_message(
                        notifier,
                        format_recent_signals_message(history),
                        menu_keyboard(),
                    )
                elif is_closest_request(text):
                    safe_send_message(notifier, format_closest_message(history), menu_keyboard())
                elif is_pause_request(text):
                    set_paused(True)
                    safe_send_message(notifier, "Сканеры поставлены на паузу.", menu_keyboard())
                elif is_start_request(text):
                    set_paused(False)
                    safe_send_message(notifier, "Сканеры снова работают.", menu_keyboard())
                elif is_menu_request(text):
                    safe_send_message(
                        notifier,
                        "Кнопки включены. Выбери действие ниже.",
                        menu_keyboard(),
                    )
                else:
                    safe_send_message(
                        notifier,
                        "Не понял команду. Выбери действие кнопкой ниже.",
                        menu_keyboard(),
                    )
        except Exception as error:
            if "timed out" not in str(error).lower():
                print(f"Status command loop error: {error}", flush=True)

        wait_or_stop(settings.status_poll_interval_seconds)


def run_dump_loop(source: str) -> None:
    settings = get_settings()
    scanner_name = f"DUMP {source.upper()}"
    if not settings.dump_enabled:
        update_paused_status(scanner_name)
        return

    if source.upper() == "BINANCE":
        client = build_binance_market_client(settings)
        allowed_symbols_provider = None
        if settings.dump_require_bybit_listing:
            bybit_symbol_cache = BybitSymbolCache(
                build_bybit_client(settings),
                settings.dump_bybit_symbol_cache_minutes,
            )
            allowed_symbols_provider = bybit_symbol_cache.get_symbols
    else:
        client = build_bybit_client(settings)
        allowed_symbols_provider = None

    history = HistoryStore(data_path("scanner.db"))
    scanner = DumpScanner(
        source,
        client,
        StateStore(data_path(f"dump_{source.lower()}_state.json")),
        settings,
        history,
        allowed_symbols_provider=allowed_symbols_provider,
    )
    scanner.store.load()
    notifier = build_notifier(settings)

    while not STOP_EVENT.is_set():
        if is_paused(scanner_name):
            update_paused_status(scanner_name)
            wait_or_stop(settings.dump_scan_interval_seconds)
            continue
        try:
            update_scanning_status(scanner_name)
            result = scanner.scan_once(
                progress_callback=lambda current, total: update_scanning_status(
                    scanner_name,
                    current,
                    total,
                )
            )
            for signal in result.signals:
                chart_renderer = None
                if source.upper() == "BINANCE":
                    chart_renderer = lambda current_signal: render_dump_chart(
                        current_signal,
                        client,
                        history,
                        lookback_hours=settings.dump_chart_lookback_hours,
                        interval=settings.dump_chart_interval,
                    )
                send_signal_with_symbol_cooldown(
                    notifier=notifier,
                    history=history,
                    settings=settings,
                    signal=signal,
                    signal_type=f"dump_{source.lower()}",
                    formatter=format_dump_signal,
                    chart_renderer=chart_renderer,
                )
            reviewed = history.update_signal_reviews()
            history.cleanup_old_data(
                snapshot_retention_days=settings.history_snapshot_retention_days,
                watchlist_retention_days=settings.watchlist_retention_days,
            )
            update_status(scanner_name, result, reviewed)
            maybe_send_rate_warning(scanner_name, result.failed_symbols, notifier)
            print(
                f"{scanner_name} scan done: "
                f"screened={result.screened_symbols}, "
                f"deep={result.scanned_symbols}, "
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
                print(f"{scanner_name} scan error. Set DEBUG_ERRORS=true for details.", flush=True)

        wait_or_stop(settings.dump_scan_interval_seconds)


def main() -> None:
    install_signal_handlers()
    settings = get_settings()
    print(
        "Config check: "
        f"telegram_enabled={settings.telegram_enabled}, "
        f"token_present={bool(settings.telegram_bot_token)}, "
        f"chat_id_present={bool(settings.telegram_chat_id)}",
        flush=True,
    )
    dump_bybit_thread = threading.Thread(
        target=run_dump_loop,
        args=("BYBIT",),
        name="dump-bybit-scanner",
        daemon=True,
    )
    dump_binance_thread = threading.Thread(
        target=run_dump_loop,
        args=("BINANCE",),
        name="dump-binance-scanner",
        daemon=True,
    )
    status_thread = threading.Thread(target=run_status_loop, name="status-commands", daemon=True)
    worker_threads = [
        dump_bybit_thread,
        dump_binance_thread,
    ]
    for thread in worker_threads:
        thread.start()
    status_thread.start()

    while not STOP_EVENT.is_set():
        if not any(thread.is_alive() for thread in worker_threads):
            print("All scanner threads stopped.", flush=True)
            break
        wait_or_stop(1)

    for thread in worker_threads:
        thread.join(timeout=5)
    print("Bot stopped.", flush=True)


def build_bybit_client(settings) -> BybitClient:
    return BybitClient(
        settings.bybit_base_url,
        verify_ssl=settings.verify_ssl,
        min_request_interval_seconds=settings.bybit_min_request_interval_seconds,
        rate_limit_backoff_seconds=settings.bybit_rate_limit_backoff_seconds,
        max_retries=settings.bybit_max_retries,
    )


def build_binance_market_client(settings) -> BinanceClient:
    return BinanceClient(
        settings.binance_base_url,
        verify_ssl=settings.verify_ssl,
        rate_limit_backoff_seconds=settings.bybit_rate_limit_backoff_seconds,
        max_retries=settings.bybit_max_retries,
    )


if __name__ == "__main__":
    main()
