# Запуск на Bothost

## Start command

```bash
python main_bothost.py
```

`main_bothost.py` запускает оба сканера в одном процессе:

- LONG scanner;
- PUMP exhaustion scanner.

## Переменные окружения

В панели Bothost добавь:

```text
TELEGRAM_BOT_TOKEN=токен_бота
TELEGRAM_CHAT_ID=твой_chat_id
TELEGRAM_ENABLED=true
VERIFY_SSL=true
DEBUG_ERRORS=false

SCAN_INTERVAL_SECONDS=60
WINDOW_MINUTES=15
OI_THRESHOLD_PCT=5
CVD_THRESHOLD_PCT=5
MIN_CVD_DELTA_USDT=10000
MIN_TURNOVER_24H_USDT=5000000
MAX_SYMBOLS=100
ALERT_COOLDOWN_MINUTES=60
PRICE_MIN_CHANGE_PCT=-1
REQUIRE_PRICE_HOLD=true
MIN_NEW_TRADES=50
CONSECUTIVE_CHECKS=2

PUMP_SCAN_INTERVAL_SECONDS=60
PUMP_WINDOW_MINUTES=15
PUMP_LOOKBACK_DAYS=2
PUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT=30
PUMP_MIN_DRAWDOWN_FROM_HIGH_PCT=10
PUMP_MAX_OI_CHANGE_PCT=0
PUMP_OI_DROP_RATIO_TO_DRAWDOWN=0.3
PUMP_MIN_NEGATIVE_CVD_CHANGE_PCT=5
PUMP_MIN_NEGATIVE_CVD_DELTA_USDT=10000
PUMP_MAX_PRICE_CHANGE_WINDOW_PCT=0
PUMP_MIN_TURNOVER_24H_USDT=5000000
PUMP_MAX_SYMBOLS=100
PUMP_MIN_NEW_TRADES=50
PUMP_CONSECUTIVE_CHECKS=2
PUMP_ALERT_COOLDOWN_MINUTES=60

BYBIT_BASE_URL=https://api.bybit.com
```

## Важно

Не загружай `.env`, `state.json`, `pump_state.json`, `__pycache__`.

Архив `bybit-scanner-bothost.zip` уже собран без этих файлов.

## База динамики

Бот сохраняет динамику в SQLite:

```text
$DATA_DIR/scanner.db
```

Там хранятся снимки рынка и история сигналов.
