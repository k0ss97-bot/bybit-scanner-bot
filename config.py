from dataclasses import dataclass
import os
from pathlib import Path


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    bybit_base_url: str
    bybit_min_request_interval_seconds: float
    bybit_rate_limit_backoff_seconds: float
    bybit_max_retries: int
    binance_base_url: str
    binance_confirm_enabled: bool
    binance_confirmation_required: bool
    binance_min_quote_volume_24h_usdt: float
    scan_interval_seconds: int
    window_minutes: int
    oi_threshold_pct: float
    cvd_threshold_pct: float
    min_cvd_delta_usdt: float
    min_turnover_24h_usdt: float
    max_symbols: int
    alert_cooldown_minutes: int
    verify_ssl: bool
    debug_errors: bool
    telegram_enabled: bool
    price_min_change_pct: float
    require_price_hold: bool
    min_new_trades: int
    consecutive_checks: int
    long_lookback_days: int
    long_max_price_growth_lookback_pct: float
    long_max_price_change_window_pct: float
    long_min_turnover_ratio_to_base: float
    long_base_cache_minutes: int
    long_min_signal_score: int
    long_watchlist_min_score: int
    long_min_spot_cvd_change_pct: float
    long_min_spot_trades_for_filter: int
    spot_cvd_update_interval_seconds: int
    watchlist_enabled: bool
    watchlist_cooldown_minutes: int
    watchlist_max_alerts_per_scan: int
    alert_score_improvement: int
    status_commands_enabled: bool
    status_poll_interval_seconds: int
    pump_scan_interval_seconds: int
    pump_window_minutes: int
    pump_lookback_days: int
    pump_min_price_growth_lookback_pct: float
    pump_min_drawdown_from_high_pct: float
    pump_max_oi_change_pct: float
    pump_oi_drop_ratio_to_drawdown: float
    pump_min_negative_cvd_change_pct: float
    pump_min_negative_cvd_delta_usdt: float
    pump_max_price_change_window_pct: float
    pump_min_turnover_24h_usdt: float
    pump_max_symbols: int
    pump_min_signal_score: int
    pump_watchlist_min_score: int
    pump_consecutive_checks: int
    pump_alert_cooldown_minutes: int
    pump_alert_score_improvement: int
    short_breakdown_enabled: bool
    short_breakdown_min_oi_growth_pct: float
    short_breakdown_max_price_change_window_pct: float
    short_breakdown_min_signal_score: int
    dump_enabled: bool
    dump_window_minutes: int
    dump_lookback_days: int
    dump_scan_interval_seconds: int
    dump_structure_cache_minutes: int
    dump_min_turnover_24h_usdt: float
    dump_max_symbols: int
    dump_min_price_growth_lookback_pct: float
    dump_min_drawdown_from_high_pct: float
    dump_min_price_drop_window_pct: float
    dump_min_negative_cvd_delta_usdt: float
    dump_max_oi_drop_window_pct: float
    dump_max_funding_rate: float
    dump_min_signal_score: int
    dump_watchlist_min_score: int
    dump_consecutive_checks: int
    dump_alert_cooldown_minutes: int
    dump_alert_score_improvement: int
    dump_symbol_cooldown_minutes: int
    startup_notifications: bool


def get_settings() -> Settings:
    load_env_file()
    return Settings(
        telegram_bot_token=_first_env(
            "SCANNER_BOT_TOKEN",
            "TELEGRAM_BOT_TOKEN",
            "BOT_API_TOKEN",
            "BOT_TOKEN",
            "TOKEN",
            "TELEGRAM_TOKEN",
            "API_TOKEN",
        ),
        telegram_chat_id=_first_env("TELEGRAM_CHAT_ID", "CHAT_ID"),
        bybit_base_url=os.getenv("BYBIT_BASE_URL", "https://api.bybit.com"),
        bybit_min_request_interval_seconds=_float("BYBIT_MIN_REQUEST_INTERVAL_SECONDS", 0.35),
        bybit_rate_limit_backoff_seconds=_float("BYBIT_RATE_LIMIT_BACKOFF_SECONDS", 3),
        bybit_max_retries=_int("BYBIT_MAX_RETRIES", 2),
        binance_base_url=os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com"),
        binance_confirm_enabled=_bool("BINANCE_CONFIRM_ENABLED", False),
        binance_confirmation_required=_bool("BINANCE_CONFIRMATION_REQUIRED", False),
        binance_min_quote_volume_24h_usdt=_float("BINANCE_MIN_QUOTE_VOLUME_24H_USDT", 10000000),
        scan_interval_seconds=_int("SCAN_INTERVAL_SECONDS", 60),
        window_minutes=_int("WINDOW_MINUTES", 15),
        oi_threshold_pct=_float("OI_THRESHOLD_PCT", 1),
        cvd_threshold_pct=_float("CVD_THRESHOLD_PCT", 2),
        min_cvd_delta_usdt=_float("MIN_CVD_DELTA_USDT", 3000),
        min_turnover_24h_usdt=_float("MIN_TURNOVER_24H_USDT", 1000000),
        max_symbols=_int("MAX_SYMBOLS", 200),
        alert_cooldown_minutes=_int("ALERT_COOLDOWN_MINUTES", 60),
        verify_ssl=_bool("VERIFY_SSL", True),
        debug_errors=_bool("DEBUG_ERRORS", False),
        telegram_enabled=_bool("TELEGRAM_ENABLED", True),
        price_min_change_pct=_float("PRICE_MIN_CHANGE_PCT", 0.3),
        require_price_hold=_bool("REQUIRE_PRICE_HOLD", True),
        min_new_trades=_int("MIN_NEW_TRADES", 50),
        consecutive_checks=_int("CONSECUTIVE_CHECKS", 1),
        long_lookback_days=_int("LONG_LOOKBACK_DAYS", 7),
        long_max_price_growth_lookback_pct=_float("LONG_MAX_PRICE_GROWTH_LOOKBACK_PCT", 200),
        long_max_price_change_window_pct=_float("LONG_MAX_PRICE_CHANGE_WINDOW_PCT", 25),
        long_min_turnover_ratio_to_base=_float("LONG_MIN_TURNOVER_RATIO_TO_BASE", 0.8),
        long_base_cache_minutes=_int("LONG_BASE_CACHE_MINUTES", 15),
        long_min_signal_score=_int("LONG_MIN_SIGNAL_SCORE", 4),
        long_watchlist_min_score=_int("LONG_WATCHLIST_MIN_SCORE", 3),
        long_min_spot_cvd_change_pct=_float("LONG_MIN_SPOT_CVD_CHANGE_PCT", -5),
        long_min_spot_trades_for_filter=_int("LONG_MIN_SPOT_TRADES_FOR_FILTER", 20),
        spot_cvd_update_interval_seconds=_int("SPOT_CVD_UPDATE_INTERVAL_SECONDS", 300),
        watchlist_enabled=_bool("WATCHLIST_ENABLED", False),
        watchlist_cooldown_minutes=_int("WATCHLIST_COOLDOWN_MINUTES", 120),
        watchlist_max_alerts_per_scan=_int("WATCHLIST_MAX_ALERTS_PER_SCAN", 3),
        alert_score_improvement=_int("ALERT_SCORE_IMPROVEMENT", 2),
        status_commands_enabled=_bool("STATUS_COMMANDS_ENABLED", True),
        status_poll_interval_seconds=_int("STATUS_POLL_INTERVAL_SECONDS", 5),
        pump_scan_interval_seconds=_int("PUMP_SCAN_INTERVAL_SECONDS", 60),
        pump_window_minutes=_int("PUMP_WINDOW_MINUTES", 15),
        pump_lookback_days=_int("PUMP_LOOKBACK_DAYS", 2),
        pump_min_price_growth_lookback_pct=_float("PUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT", 20),
        pump_min_drawdown_from_high_pct=_float("PUMP_MIN_DRAWDOWN_FROM_HIGH_PCT", 5),
        pump_max_oi_change_pct=_float("PUMP_MAX_OI_CHANGE_PCT", 0),
        pump_oi_drop_ratio_to_drawdown=_float("PUMP_OI_DROP_RATIO_TO_DRAWDOWN", 0.3),
        pump_min_negative_cvd_change_pct=_float("PUMP_MIN_NEGATIVE_CVD_CHANGE_PCT", 3),
        pump_min_negative_cvd_delta_usdt=_float("PUMP_MIN_NEGATIVE_CVD_DELTA_USDT", 5000),
        pump_max_price_change_window_pct=_float("PUMP_MAX_PRICE_CHANGE_WINDOW_PCT", 0),
        pump_min_turnover_24h_usdt=_float("PUMP_MIN_TURNOVER_24H_USDT", 2000000),
        pump_max_symbols=_int("PUMP_MAX_SYMBOLS", 200),
        pump_min_signal_score=_int("PUMP_MIN_SIGNAL_SCORE", 5),
        pump_watchlist_min_score=_int("PUMP_WATCHLIST_MIN_SCORE", 5),
        pump_consecutive_checks=_int("PUMP_CONSECUTIVE_CHECKS", 1),
        pump_alert_cooldown_minutes=_int("PUMP_ALERT_COOLDOWN_MINUTES", 60),
        pump_alert_score_improvement=_int("PUMP_ALERT_SCORE_IMPROVEMENT", 2),
        short_breakdown_enabled=_bool("SHORT_BREAKDOWN_ENABLED", True),
        short_breakdown_min_oi_growth_pct=_float("SHORT_BREAKDOWN_MIN_OI_GROWTH_PCT", 0),
        short_breakdown_max_price_change_window_pct=_float("SHORT_BREAKDOWN_MAX_PRICE_CHANGE_WINDOW_PCT", -0.5),
        short_breakdown_min_signal_score=_int("SHORT_BREAKDOWN_MIN_SIGNAL_SCORE", 5),
        dump_enabled=_bool("DUMP_ENABLED", True),
        dump_window_minutes=_int("DUMP_WINDOW_MINUTES", 15),
        dump_lookback_days=_int("DUMP_LOOKBACK_DAYS", 2),
        dump_scan_interval_seconds=_int("DUMP_SCAN_INTERVAL_SECONDS", 120),
        dump_structure_cache_minutes=_int("DUMP_STRUCTURE_CACHE_MINUTES", 30),
        dump_min_turnover_24h_usdt=_float("DUMP_MIN_TURNOVER_24H_USDT", 2000000),
        dump_max_symbols=_int("DUMP_MAX_SYMBOLS", 40),
        dump_min_price_growth_lookback_pct=_float("DUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT", 15),
        dump_min_drawdown_from_high_pct=_float("DUMP_MIN_DRAWDOWN_FROM_HIGH_PCT", 4),
        dump_min_price_drop_window_pct=_float("DUMP_MIN_PRICE_DROP_WINDOW_PCT", 0.5),
        dump_min_negative_cvd_delta_usdt=_float("DUMP_MIN_NEGATIVE_CVD_DELTA_USDT", 5000),
        dump_max_oi_drop_window_pct=_float("DUMP_MAX_OI_DROP_WINDOW_PCT", 8),
        dump_max_funding_rate=_float("DUMP_MAX_FUNDING_RATE", 0.002),
        dump_min_signal_score=_int("DUMP_MIN_SIGNAL_SCORE", 5),
        dump_watchlist_min_score=_int("DUMP_WATCHLIST_MIN_SCORE", 4),
        dump_consecutive_checks=_int("DUMP_CONSECUTIVE_CHECKS", 1),
        dump_alert_cooldown_minutes=_int("DUMP_ALERT_COOLDOWN_MINUTES", 45),
        dump_alert_score_improvement=_int("DUMP_ALERT_SCORE_IMPROVEMENT", 2),
        dump_symbol_cooldown_minutes=_int("DUMP_SYMBOL_COOLDOWN_MINUTES", 60),
        startup_notifications=_bool("STARTUP_NOTIFICATIONS", False),
    )
