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


def _int_list(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    value = os.getenv(name)
    if not value:
        return default
    items = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if item:
            items.append(int(item))
    return tuple(items) or default


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
    orderbook_enabled: bool
    orderbook_limit: int
    orderbook_depth_pct: float
    orderbook_cache_seconds: int
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
    telegram_symbol_cooldown_minutes: int
    price_min_change_pct: float
    require_price_hold: bool
    min_new_trades: int
    consecutive_checks: int
    long_momentum_enabled: bool
    long_lookback_days: int
    long_max_price_growth_lookback_pct: float
    long_max_price_change_window_pct: float
    long_min_turnover_ratio_to_base: float
    long_base_cache_minutes: int
    long_min_signal_score: int
    long_watchlist_min_score: int
    long_max_24h_price_change_pct: float
    long_compression_max_base_range_pct: float
    long_min_spot_cvd_change_pct: float
    long_min_spot_trades_for_filter: int
    long_accumulation_enabled: bool
    long_accumulation_window_minutes: int
    long_accumulation_windows_minutes: tuple[int, ...]
    long_accumulation_min_price_change_pct: float
    long_accumulation_max_price_change_pct: float
    long_accumulation_min_oi_change_pct: float
    long_accumulation_min_cvd_delta_usdt: float
    long_accumulation_max_current_from_base_pct: float
    long_accumulation_min_signal_score: int
    long_breakout_enabled: bool
    long_breakout_window_minutes: int
    long_breakout_min_price_change_pct: float
    long_breakout_max_price_change_pct: float
    long_breakout_min_oi_change_pct: float
    long_breakout_min_cvd_delta_usdt: float
    long_breakout_max_current_from_base_pct: float
    long_breakout_min_signal_score: int
    long_squeeze_enabled: bool
    long_squeeze_lookback_days: int
    long_squeeze_max_base_range_pct: float
    long_squeeze_max_dist_from_base_high_pct: float
    long_squeeze_window_minutes: int
    long_squeeze_min_price_change_pct: float
    long_squeeze_max_price_change_pct: float
    long_squeeze_min_volume_burst_ratio: float
    long_squeeze_min_signal_score: int
    long_squeeze_strong_negative_funding_pct: float
    long_squeeze_min_oi_trend_pct: float
    spring_min_score: int
    spring_max_per_scan: int
    sleeper_scan_enabled: bool
    sleeper_min_turnover_24h_usdt: float
    sleeper_max_symbols: int
    sleeper_scan_interval_minutes: int
    spot_cvd_update_interval_seconds: int
    candidate_tracking_enabled: bool
    history_snapshot_retention_days: int
    watchlist_retention_days: int
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
    short_long_trap_enabled: bool
    short_long_trap_min_drawdown_from_high_pct: float
    short_long_trap_min_oi_growth_pct: float
    short_long_trap_max_price_change_window_pct: float
    short_long_trap_min_signal_score: int
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
        orderbook_enabled=_bool("ORDERBOOK_ENABLED", True),
        orderbook_limit=_int("ORDERBOOK_LIMIT", 100),
        orderbook_depth_pct=_float("ORDERBOOK_DEPTH_PCT", 1.0),
        orderbook_cache_seconds=_int("ORDERBOOK_CACHE_SECONDS", 300),
        scan_interval_seconds=_int("SCAN_INTERVAL_SECONDS", 60),
        window_minutes=_int("WINDOW_MINUTES", 15),
        oi_threshold_pct=_float("OI_THRESHOLD_PCT", 1),
        cvd_threshold_pct=_float("CVD_THRESHOLD_PCT", 2),
        min_cvd_delta_usdt=_float("MIN_CVD_DELTA_USDT", 3000),
        min_turnover_24h_usdt=_float("MIN_TURNOVER_24H_USDT", 1000000),
        max_symbols=_int("MAX_SYMBOLS", 250),
        alert_cooldown_minutes=_int("ALERT_COOLDOWN_MINUTES", 240),
        verify_ssl=_bool("VERIFY_SSL", True),
        debug_errors=_bool("DEBUG_ERRORS", False),
        telegram_enabled=_bool("TELEGRAM_ENABLED", True),
        telegram_symbol_cooldown_minutes=_int("TELEGRAM_SYMBOL_COOLDOWN_MINUTES", 240),
        price_min_change_pct=_float("PRICE_MIN_CHANGE_PCT", 0.3),
        require_price_hold=_bool("REQUIRE_PRICE_HOLD", True),
        min_new_trades=_int("MIN_NEW_TRADES", 50),
        consecutive_checks=_int("CONSECUTIVE_CHECKS", 1),
        long_momentum_enabled=_bool("LONG_MOMENTUM_ENABLED", False),
        long_lookback_days=_int("LONG_LOOKBACK_DAYS", 7),
        long_max_price_growth_lookback_pct=_float("LONG_MAX_PRICE_GROWTH_LOOKBACK_PCT", 200),
        long_max_price_change_window_pct=_float("LONG_MAX_PRICE_CHANGE_WINDOW_PCT", 25),
        long_min_turnover_ratio_to_base=_float("LONG_MIN_TURNOVER_RATIO_TO_BASE", 0.8),
        long_base_cache_minutes=_int("LONG_BASE_CACHE_MINUTES", 15),
        long_min_signal_score=_int("LONG_MIN_SIGNAL_SCORE", 4),
        long_watchlist_min_score=_int("LONG_WATCHLIST_MIN_SCORE", 2),
        long_max_24h_price_change_pct=_float("LONG_MAX_24H_PRICE_CHANGE_PCT", 60),
        long_compression_max_base_range_pct=_float("LONG_COMPRESSION_MAX_BASE_RANGE_PCT", 35),
        long_min_spot_cvd_change_pct=_float("LONG_MIN_SPOT_CVD_CHANGE_PCT", -5),
        long_min_spot_trades_for_filter=_int("LONG_MIN_SPOT_TRADES_FOR_FILTER", 20),
        long_accumulation_enabled=_bool("LONG_ACCUMULATION_ENABLED", True),
        long_accumulation_window_minutes=_int("LONG_ACCUMULATION_WINDOW_MINUTES", 120),
        long_accumulation_windows_minutes=_int_list(
            "LONG_ACCUMULATION_WINDOWS_MINUTES",
            (30, 120, 240),
        ),
        long_accumulation_min_price_change_pct=_float("LONG_ACCUMULATION_MIN_PRICE_CHANGE_PCT", -2.5),
        long_accumulation_max_price_change_pct=_float("LONG_ACCUMULATION_MAX_PRICE_CHANGE_PCT", 6),
        long_accumulation_min_oi_change_pct=_float("LONG_ACCUMULATION_MIN_OI_CHANGE_PCT", 1),
        long_accumulation_min_cvd_delta_usdt=_float("LONG_ACCUMULATION_MIN_CVD_DELTA_USDT", 5000),
        long_accumulation_max_current_from_base_pct=_float("LONG_ACCUMULATION_MAX_CURRENT_FROM_BASE_PCT", 35),
        long_accumulation_min_signal_score=_int("LONG_ACCUMULATION_MIN_SIGNAL_SCORE", 3),
        long_breakout_enabled=_bool("LONG_BREAKOUT_ENABLED", True),
        long_breakout_window_minutes=_int("LONG_BREAKOUT_WINDOW_MINUTES", 30),
        long_breakout_min_price_change_pct=_float("LONG_BREAKOUT_MIN_PRICE_CHANGE_PCT", 0.5),
        long_breakout_max_price_change_pct=_float("LONG_BREAKOUT_MAX_PRICE_CHANGE_PCT", 18),
        long_breakout_min_oi_change_pct=_float("LONG_BREAKOUT_MIN_OI_CHANGE_PCT", 0.5),
        long_breakout_min_cvd_delta_usdt=_float("LONG_BREAKOUT_MIN_CVD_DELTA_USDT", 3000),
        long_breakout_max_current_from_base_pct=_float("LONG_BREAKOUT_MAX_CURRENT_FROM_BASE_PCT", 60),
        long_breakout_min_signal_score=_int("LONG_BREAKOUT_MIN_SIGNAL_SCORE", 4),
        long_squeeze_enabled=_bool("LONG_SQUEEZE_ENABLED", True),
        long_squeeze_lookback_days=_int("LONG_SQUEEZE_LOOKBACK_DAYS", 21),
        long_squeeze_max_base_range_pct=_float("LONG_SQUEEZE_MAX_BASE_RANGE_PCT", 25),
        long_squeeze_max_dist_from_base_high_pct=_float("LONG_SQUEEZE_MAX_DIST_FROM_BASE_HIGH_PCT", 3),
        long_squeeze_window_minutes=_int("LONG_SQUEEZE_WINDOW_MINUTES", 30),
        long_squeeze_min_price_change_pct=_float("LONG_SQUEEZE_MIN_PRICE_CHANGE_PCT", 0.2),
        long_squeeze_max_price_change_pct=_float("LONG_SQUEEZE_MAX_PRICE_CHANGE_PCT", 15),
        long_squeeze_min_volume_burst_ratio=_float("LONG_SQUEEZE_MIN_VOLUME_BURST_RATIO", 3),
        long_squeeze_min_signal_score=_int("LONG_SQUEEZE_MIN_SIGNAL_SCORE", 4),
        long_squeeze_strong_negative_funding_pct=_float("LONG_SQUEEZE_STRONG_NEGATIVE_FUNDING_PCT", -0.05),
        long_squeeze_min_oi_trend_pct=_float("LONG_SQUEEZE_MIN_OI_TREND_PCT", 3),
        spring_min_score=_int("SPRING_MIN_SCORE", 3),
        spring_max_per_scan=_int("SPRING_MAX_PER_SCAN", 10),
        sleeper_scan_enabled=_bool("SLEEPER_SCAN_ENABLED", True),
        sleeper_min_turnover_24h_usdt=_float("SLEEPER_MIN_TURNOVER_24H_USDT", 250000),
        sleeper_max_symbols=_int("SLEEPER_MAX_SYMBOLS", 150),
        sleeper_scan_interval_minutes=_int("SLEEPER_SCAN_INTERVAL_MINUTES", 10),
        spot_cvd_update_interval_seconds=_int("SPOT_CVD_UPDATE_INTERVAL_SECONDS", 300),
        candidate_tracking_enabled=_bool("CANDIDATE_TRACKING_ENABLED", True),
        history_snapshot_retention_days=_int("HISTORY_SNAPSHOT_RETENTION_DAYS", 7),
        watchlist_retention_days=_int("WATCHLIST_RETENTION_DAYS", 7),
        watchlist_enabled=_bool("WATCHLIST_ENABLED", False),
        watchlist_cooldown_minutes=_int("WATCHLIST_COOLDOWN_MINUTES", 120),
        watchlist_max_alerts_per_scan=_int("WATCHLIST_MAX_ALERTS_PER_SCAN", 5),
        alert_score_improvement=_int("ALERT_SCORE_IMPROVEMENT", 1),
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
        pump_min_turnover_24h_usdt=_float("PUMP_MIN_TURNOVER_24H_USDT", 5000000),
        pump_max_symbols=_int("PUMP_MAX_SYMBOLS", 200),
        pump_min_signal_score=_int("PUMP_MIN_SIGNAL_SCORE", 5),
        pump_watchlist_min_score=_int("PUMP_WATCHLIST_MIN_SCORE", 5),
        pump_consecutive_checks=_int("PUMP_CONSECUTIVE_CHECKS", 1),
        pump_alert_cooldown_minutes=_int("PUMP_ALERT_COOLDOWN_MINUTES", 240),
        pump_alert_score_improvement=_int("PUMP_ALERT_SCORE_IMPROVEMENT", 1),
        short_breakdown_enabled=_bool("SHORT_BREAKDOWN_ENABLED", True),
        short_breakdown_min_oi_growth_pct=_float("SHORT_BREAKDOWN_MIN_OI_GROWTH_PCT", 0),
        short_breakdown_max_price_change_window_pct=_float("SHORT_BREAKDOWN_MAX_PRICE_CHANGE_WINDOW_PCT", -0.5),
        short_breakdown_min_signal_score=_int("SHORT_BREAKDOWN_MIN_SIGNAL_SCORE", 5),
        short_long_trap_enabled=_bool("SHORT_LONG_TRAP_ENABLED", True),
        short_long_trap_min_drawdown_from_high_pct=_float("SHORT_LONG_TRAP_MIN_DRAWDOWN_FROM_HIGH_PCT", 2),
        short_long_trap_min_oi_growth_pct=_float("SHORT_LONG_TRAP_MIN_OI_GROWTH_PCT", 2),
        short_long_trap_max_price_change_window_pct=_float("SHORT_LONG_TRAP_MAX_PRICE_CHANGE_WINDOW_PCT", 1),
        short_long_trap_min_signal_score=_int("SHORT_LONG_TRAP_MIN_SIGNAL_SCORE", 5),
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
