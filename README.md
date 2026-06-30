# Bybit LONG Scanner MVP

В проекте два отдельных сканера:

- `main.py` — Bothost entrypoint, запускает оба сканера;
- `main_pump.py` — pump exhaustion scanner.
- `main_bothost.py` — оба сканера в одном процессе для хостингов вроде Bothost.

Инструкция для запуска на сервере 24/7: [DEPLOY.md](DEPLOY.md).

Long-бот отслеживает USDT perpetual монеты на Bybit и отправляет сигнал, когда за выбранное окно одновременно растут:

- `OI` на заданный процент;
- `CVD` на заданный процент;
- `CVD delta` в USDT выше минимального порога.

Funding не фильтрует сигнал. Он только показывается в сообщении.

## История и динамика

Бот пишет динамику в SQLite:

```text
data/scanner.db
```

Если хостинг задает `DATA_DIR`, база и state-файлы будут храниться там:

```text
$DATA_DIR/scanner.db
$DATA_DIR/state.json
$DATA_DIR/pump_state.json
```

В базе есть:

- `market_snapshots` — снимки цены, OI, futures CVD, spot CVD, funding;
- `signals` — история найденных сигналов.

## Как запустить

1. Создай окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Внешние библиотеки для MVP не нужны.

2. Создай настройки:

```bash
cp .env.example .env
```

3. Заполни в `.env`:

```text
TELEGRAM_BOT_TOKEN=токен_от_BotFather
TELEGRAM_CHAT_ID=твой_chat_id
```

4. Запусти:

```bash
python main.py
```

Запустить второго бота, который ищет истощение пампа:

```bash
python main_pump.py
```

Запустить оба сканера одним процессом:

```bash
python main_bothost.py
```

Проверить только Telegram:

```bash
python main.py --test-telegram
```

Сделать один скан и остановиться:

```bash
python main.py --once
python main_pump.py --once
```

Если Telegram токен или chat id не указаны, сигналы будут печататься в консоль.

## Основные настройки

```text
WINDOW_MINUTES=15
OI_THRESHOLD_PCT=10
CVD_THRESHOLD_PCT=10
MIN_CVD_DELTA_USDT=10000
MIN_TURNOVER_24H_USDT=1000000
MAX_SYMBOLS=200
ALERT_COOLDOWN_MINUTES=60
PRICE_MIN_CHANGE_PCT=-1
REQUIRE_PRICE_HOLD=true
MIN_NEW_TRADES=50
CONSECUTIVE_CHECKS=2
LONG_LOOKBACK_DAYS=7
LONG_MAX_PRICE_GROWTH_LOOKBACK_PCT=20
LONG_MAX_PRICE_CHANGE_WINDOW_PCT=25
LONG_MIN_TURNOVER_RATIO_TO_BASE=2
LONG_BASE_CACHE_MINUTES=15
VERIFY_SSL=true
DEBUG_ERRORS=false
TELEGRAM_ENABLED=true
```

Настройки pump exhaustion scanner:

```text
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
```

Pump-бот ищет структуру:

```text
монета выросла на 30%+ за последние 2 дневные свечи
цена откатилась от high разгона минимум на 10%
OI за окно остановился или падает
падение OI пропорционально откату цены: откат x 0.3
CVD за окно ушел в минус минимум на 5%
цена за окно не растет
```

Если локальный Python на macOS ругается на сертификаты (`CERTIFICATE_VERIFY_FAILED`), можно временно поставить:

```text
VERIFY_SSL=false
```

Для боевого сервера лучше оставить `VERIFY_SSL=true`.

## Как считается CVD

Бот берет последние публичные сделки Bybit. Если taker side = `Buy`, объем сделки идет в плюс. Если `Sell`, объем идет в минус. Для расчета используется notional в USDT:

```text
price * size
```

Чтобы не считать одну сделку дважды, бот хранит `execId` последних сделок в `state.json`.

## Важное ограничение MVP

Это первая версия на REST API. Она простая и понятная, но для очень частого сканирования всего рынка лучше перейти на WebSocket, чтобы CVD был точнее и нагрузка на API была ниже.
