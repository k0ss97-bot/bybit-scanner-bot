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
    pump_min_new_trades: int
    pump_consecutive_checks: int
    pump_alert_cooldown_minutes: int


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
        scan_interval_seconds=_int("SCAN_INTERVAL_SECONDS", 60),
        window_minutes=_int("WINDOW_MINUTES", 15),
        oi_threshold_pct=_float("OI_THRESHOLD_PCT", 5),
        cvd_threshold_pct=_float("CVD_THRESHOLD_PCT", 5),
        min_cvd_delta_usdt=_float("MIN_CVD_DELTA_USDT", 10000),
        min_turnover_24h_usdt=_float("MIN_TURNOVER_24H_USDT", 5000000),
        max_symbols=_int("MAX_SYMBOLS", 100),
        alert_cooldown_minutes=_int("ALERT_COOLDOWN_MINUTES", 60),
        verify_ssl=_bool("VERIFY_SSL", True),
        debug_errors=_bool("DEBUG_ERRORS", False),
        telegram_enabled=_bool("TELEGRAM_ENABLED", True),
        price_min_change_pct=_float("PRICE_MIN_CHANGE_PCT", -1),
        require_price_hold=_bool("REQUIRE_PRICE_HOLD", True),
        min_new_trades=_int("MIN_NEW_TRADES", 50),
        consecutive_checks=_int("CONSECUTIVE_CHECKS", 2),
        pump_scan_interval_seconds=_int("PUMP_SCAN_INTERVAL_SECONDS", 60),
        pump_window_minutes=_int("PUMP_WINDOW_MINUTES", 15),
        pump_lookback_days=_int("PUMP_LOOKBACK_DAYS", 2),
        pump_min_price_growth_lookback_pct=_float("PUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT", 30),
        pump_min_drawdown_from_high_pct=_float("PUMP_MIN_DRAWDOWN_FROM_HIGH_PCT", 10),
        pump_max_oi_change_pct=_float("PUMP_MAX_OI_CHANGE_PCT", 0),
        pump_oi_drop_ratio_to_drawdown=_float("PUMP_OI_DROP_RATIO_TO_DRAWDOWN", 0.3),
        pump_min_negative_cvd_change_pct=_float("PUMP_MIN_NEGATIVE_CVD_CHANGE_PCT", 5),
        pump_min_negative_cvd_delta_usdt=_float("PUMP_MIN_NEGATIVE_CVD_DELTA_USDT", 10000),
        pump_max_price_change_window_pct=_float("PUMP_MAX_PRICE_CHANGE_WINDOW_PCT", 0),
        pump_min_turnover_24h_usdt=_float("PUMP_MIN_TURNOVER_24H_USDT", 5000000),
        pump_max_symbols=_int("PUMP_MAX_SYMBOLS", 100),
        pump_min_new_trades=_int("PUMP_MIN_NEW_TRADES", 50),
        pump_consecutive_checks=_int("PUMP_CONSECUTIVE_CHECKS", 2),
        pump_alert_cooldown_minutes=_int("PUMP_ALERT_COOLDOWN_MINUTES", 60),
    )
