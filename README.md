# Bybit Scanner

В проекте два сканера:

- `main.py` — основной вход для Bothost, запускает оба сканера;
- `main_long.py` — только LONG scanner;
- `main_pump.py` — pump exhaustion scanner.
- `main_bothost.py` — оба сканера в одном процессе для хостингов вроде Bothost; `main.py` просто вызывает его.

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
- `signals` — история найденных сигналов;
- `signal_reviews` — расчет результата сигналов через 1ч/4ч/24ч;
- `watchlist_candidates` — почти сигналы для кнопки "Ближайшие".

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

Запустить только LONG scanner:

```bash
python main_long.py
```

Запустить только scanner истощения пампа:

```bash
python main_pump.py
```

Запустить оба сканера одним процессом:

```bash
python main_bothost.py
```

Проверить только Telegram:

```bash
python main_long.py --test-telegram
python main_pump.py --test-telegram
```

Сделать один скан и остановиться:

```bash
python main_long.py --once
python main_pump.py --once
```

Если Telegram токен или chat id не указаны, сигналы будут печататься в консоль.

## Основные настройки

```text
WINDOW_MINUTES=15
OI_THRESHOLD_PCT=1
CVD_THRESHOLD_PCT=2
MIN_CVD_DELTA_USDT=3000
MIN_TURNOVER_24H_USDT=1000000
MAX_SYMBOLS=250
ALERT_COOLDOWN_MINUTES=240
PRICE_MIN_CHANGE_PCT=0.3
REQUIRE_PRICE_HOLD=true
MIN_NEW_TRADES=50
CONSECUTIVE_CHECKS=1
LONG_MOMENTUM_ENABLED=false
LONG_LOOKBACK_DAYS=14
LONG_MAX_PRICE_GROWTH_LOOKBACK_PCT=200
LONG_MAX_PRICE_CHANGE_WINDOW_PCT=25
LONG_MIN_TURNOVER_RATIO_TO_BASE=0.8
LONG_BASE_CACHE_MINUTES=15
LONG_MIN_SIGNAL_SCORE=4
LONG_WATCHLIST_MIN_SCORE=2
LONG_MAX_24H_PRICE_CHANGE_PCT=60
LONG_COMPRESSION_MAX_BASE_RANGE_PCT=25
LONG_MIN_SPOT_CVD_CHANGE_PCT=-5
LONG_MIN_SPOT_TRADES_FOR_FILTER=20
LONG_ACCUMULATION_ENABLED=true
LONG_ACCUMULATION_WINDOW_MINUTES=120
LONG_ACCUMULATION_WINDOWS_MINUTES=30,120,240
LONG_ACCUMULATION_MIN_PRICE_CHANGE_PCT=-2.5
LONG_ACCUMULATION_MAX_PRICE_CHANGE_PCT=6
LONG_ACCUMULATION_MIN_OI_CHANGE_PCT=1
LONG_ACCUMULATION_MIN_CVD_DELTA_USDT=5000
LONG_ACCUMULATION_MAX_CURRENT_FROM_BASE_PCT=35
LONG_ACCUMULATION_MIN_SIGNAL_SCORE=3
LONG_BREAKOUT_ENABLED=true
LONG_BREAKOUT_WINDOW_MINUTES=30
LONG_BREAKOUT_MIN_PRICE_CHANGE_PCT=0.5
LONG_BREAKOUT_MAX_PRICE_CHANGE_PCT=18
LONG_BREAKOUT_MIN_OI_CHANGE_PCT=0.5
LONG_BREAKOUT_MIN_CVD_DELTA_USDT=3000
LONG_BREAKOUT_MAX_CURRENT_FROM_BASE_PCT=60
LONG_BREAKOUT_MIN_SIGNAL_SCORE=4
LONG_SQUEEZE_ENABLED=true
LONG_SQUEEZE_LOOKBACK_DAYS=21
LONG_SQUEEZE_MAX_BASE_RANGE_PCT=25
LONG_SQUEEZE_MAX_DIST_FROM_BASE_HIGH_PCT=3
LONG_SQUEEZE_WINDOW_MINUTES=30
LONG_SQUEEZE_MIN_PRICE_CHANGE_PCT=0.2
LONG_SQUEEZE_MAX_PRICE_CHANGE_PCT=15
LONG_SQUEEZE_MIN_VOLUME_BURST_RATIO=3
LONG_SQUEEZE_MIN_SIGNAL_SCORE=4
LONG_SQUEEZE_STRONG_NEGATIVE_FUNDING_PCT=-0.05
LONG_SQUEEZE_MIN_OI_TREND_PCT=3
SLEEPER_SCAN_ENABLED=true
SLEEPER_MIN_TURNOVER_24H_USDT=250000
SLEEPER_MAX_SYMBOLS=150
SLEEPER_SCAN_INTERVAL_MINUTES=10
ORDERBOOK_ENABLED=true
ORDERBOOK_LIMIT=100
ORDERBOOK_DEPTH_PCT=1
ORDERBOOK_CACHE_SECONDS=300
VERIFY_SSL=true
DEBUG_ERRORS=false
TELEGRAM_ENABLED=true
STARTUP_NOTIFICATIONS=false
TELEGRAM_SYMBOL_COOLDOWN_MINUTES=240
WATCHLIST_ENABLED=false
CANDIDATE_TRACKING_ENABLED=true
HISTORY_SNAPSHOT_RETENTION_DAYS=7
WATCHLIST_RETENTION_DAYS=7
WATCHLIST_COOLDOWN_MINUTES=120
WATCHLIST_MAX_ALERTS_PER_SCAN=5
ALERT_SCORE_IMPROVEMENT=1
STATUS_COMMANDS_ENABLED=true
STATUS_POLL_INTERVAL_SECONDS=5
BYBIT_MIN_REQUEST_INTERVAL_SECONDS=0.35
BYBIT_RATE_LIMIT_BACKOFF_SECONDS=3
BYBIT_MAX_RETRIES=2
BINANCE_BASE_URL=https://fapi.binance.com
BINANCE_CONFIRM_ENABLED=false
BINANCE_CONFIRMATION_REQUIRED=false
BINANCE_MIN_QUOTE_VOLUME_24H_USDT=10000000
SPOT_CVD_UPDATE_INTERVAL_SECONDS=300
```

Старый импульсный LONG выключен по умолчанию:

```text
LONG_MOMENTUM_ENABLED=false
```

Если включить `LONG_MOMENTUM_ENABLED=true`, бот снова будет искать практический импульс:

```text
жестко: цена за окно растет минимум на PRICE_MIN_CHANGE_PCT
жестко: futures CVD delta положительный минимум на MIN_CVD_DELTA_USDT
жестко: сила сигнала минимум LONG_MIN_SIGNAL_SCORE
мягко: OI, CVD %, spot CVD, оборот к базе и база роста только добавляют score
```

`MIN_NEW_TRADES`, `LONG_MIN_SPOT_CVD_CHANGE_PCT` и `LONG_MIN_TURNOVER_RATIO_TO_BASE` больше не душат сигнал сами по себе. Они помогают оценить силу, но не отсекают монету, если цена и futures CVD уже показывают импульс.

Если цена еще почти стоит на месте, но OI и futures CVD уже набираются, бот отправит отдельный ранний сигнал:

```text
🟢 LONG ACCUMULATION
```

Этот сигнал смотрит не 15 минут, а `LONG_ACCUMULATION_WINDOW_MINUTES`. Так он может увидеть медленный набор позиции, когда цена еще стоит или слегка проседает.
Дополнительно бот смотрит окна `LONG_ACCUMULATION_WINDOWS_MINUTES`: короткое окно ловит ранний старт, длинное окно ловит медленное накопление.

Если цена уже начала выходить из диапазона, бот может отправить:

```text
🟢 LONG BREAKOUT
```

Этот сигнал смотрит `LONG_BREAKOUT_WINDOW_MINUTES`: цена уже двинулась вверх, но еще не должна быть перегрета сильнее `LONG_BREAKOUT_MAX_PRICE_CHANGE_PCT`.

Если монета долго стояла в мертвой сжатой базе и начала просыпаться на всплеске объема, бот отправит:

```text
🟢 LONG SQUEEZE
```

Это сетап пробуждения базы / шорт-сквиза (кейсы вроде LAB, LIT, ES, TLM):

- база `LONG_SQUEEZE_LOOKBACK_DAYS` (по умолчанию 21 день) с диапазоном не шире `LONG_SQUEEZE_MAX_BASE_RANGE_PCT`;
- цена пробивает high базы или стоит не глубже `LONG_SQUEEZE_MAX_DIST_FROM_BASE_HIGH_PCT` под ним;
- объем за последний час минимум в `LONG_SQUEEZE_MIN_VOLUME_BURST_RATIO` раза выше среднего часового объема базы;
- цена за окно `LONG_SQUEEZE_WINDOW_MINUTES` растет хотя бы на `LONG_SQUEEZE_MIN_PRICE_CHANGE_PCT`.

Важно: в отличие от accumulation/breakout, положительный futures CVD здесь НЕ обязателен. Отрицательный funding и отрицательный futures CVD при держащейся цене (абсорбция шортов) не режут сигнал, а добавляют баллы:

- funding < 0 дает +1, funding ниже `LONG_SQUEEZE_STRONG_NEGATIVE_FUNDING_PCT` дает +2;
- рост OI за 24-48 часов при плоской цене (из SQLite-истории `market_snapshots`) от `LONG_SQUEEZE_MIN_OI_TREND_PCT` дает +1..2;
- futures CVD в минусе при растущей цене дает +1 (шорты давят, цена не падает);
- рост spot CVD дает +1.

Чтобы сканер видел спящие монеты с маленьким оборотом (которые не проходят `MIN_TURNOVER_24H_USDT` и не попадают в top `MAX_SYMBOLS`), есть отдельный «спящий» проход: раз в `SLEEPER_SCAN_INTERVAL_MINUTES` минут бот дополнительно сканирует до `SLEEPER_MAX_SYMBOLS` монет с оборотом от `SLEEPER_MIN_TURNOVER_24H_USDT`. Это увеличивает нагрузку на API, поэтому проход включается не каждый скан.

`STARTUP_NOTIFICATIONS=false` означает, что бот не отправляет сообщение "scanner запущен" при обычном старте и пишет в Telegram только сигналы. Для проверки Telegram используй `--test-telegram`.
`CANDIDATE_TRACKING_ENABLED=true` означает, что бот копит почти сигналы в базе для кнопки "Ближайшие". `WATCHLIST_ENABLED` оставлен под отдельные Telegram-alerts по watchlist и сейчас не влияет на реальные сигналы.
`ALERT_COOLDOWN_MINUTES=240` и `ALERT_SCORE_IMPROVEMENT=1` означают, что повторный сигнал по той же монете не придет раньше 4 часов, а после 4 часов придет только если score стал выше минимум на 1.

`TELEGRAM_SYMBOL_COOLDOWN_MINUTES=240` защищает от спама: по одной монете бот отправляет в Telegram не больше одного сигнала за 4 часа, даже если второй сигнал пришел с другой биржи или другого сканера.

`LONG_MAX_24H_PRICE_CHANGE_PCT=60` не дает long-сигнал, если монета уже сильно улетела за сутки. Это защита от поздних входов на хаях.
`LONG_COMPRESSION_MAX_BASE_RANGE_PCT=25` добавляет баллы монетам, которые до импульса стояли в более сжатой базе.
`ORDERBOOK_ENABLED=true` добавляет проверку стакана. В сигнале появятся спред, глубина 1% и оценка ликвидности: `fragile`, `stressed` или `healthy`.

Telegram-кнопки:

```text
📊 Статус - текущий прогресс сканеров и причины отсечения
⚙️ Настройки - реальные настройки, которые применил бот
📈 Статистика - статистика сигналов и последние сигналы
❓ Почему нет сигналов - подробные причины отсечения
🎯 Ближайшие - топ монет, которые ближе всего к сигналу
🕘 Последние сигналы - последние 10 сигналов из базы
🟢 LONG статус - отдельный статус long-сканера
🔴 PUMP статус - отдельный статус pump-сканера
⏸ Пауза / ▶️ Старт - временно остановить или запустить оба сканера
```

Если кнопки не появились, напиши боту `/start` или `меню`.
Старые команды `/status`, `/status long`, `/status pump`, `/settings`, `/stats`, `/why`, `/last`, `/closest`, `/pause`, `/resume` тоже работают.

Настройки pump exhaustion scanner:

```text
PUMP_SCAN_INTERVAL_SECONDS=60
PUMP_WINDOW_MINUTES=15
PUMP_LOOKBACK_DAYS=2
PUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT=20
PUMP_MIN_DRAWDOWN_FROM_HIGH_PCT=5
PUMP_MAX_OI_CHANGE_PCT=0
PUMP_OI_DROP_RATIO_TO_DRAWDOWN=0.3
PUMP_MIN_NEGATIVE_CVD_CHANGE_PCT=3
PUMP_MIN_NEGATIVE_CVD_DELTA_USDT=5000
PUMP_MAX_PRICE_CHANGE_WINDOW_PCT=0
PUMP_MIN_TURNOVER_24H_USDT=5000000
PUMP_MAX_SYMBOLS=200
PUMP_MIN_SIGNAL_SCORE=5
PUMP_WATCHLIST_MIN_SCORE=5
PUMP_CONSECUTIVE_CHECKS=1
PUMP_ALERT_COOLDOWN_MINUTES=240
PUMP_ALERT_SCORE_IMPROVEMENT=1
SHORT_BREAKDOWN_ENABLED=true
SHORT_BREAKDOWN_MIN_OI_GROWTH_PCT=0
SHORT_BREAKDOWN_MAX_PRICE_CHANGE_WINDOW_PCT=-0.5
SHORT_BREAKDOWN_MIN_SIGNAL_SCORE=5
SHORT_LONG_TRAP_ENABLED=true
SHORT_LONG_TRAP_MIN_DRAWDOWN_FROM_HIGH_PCT=2
SHORT_LONG_TRAP_MIN_OI_GROWTH_PCT=2
SHORT_LONG_TRAP_MAX_PRICE_CHANGE_WINDOW_PCT=1
SHORT_LONG_TRAP_MIN_SIGNAL_SCORE=5
```

`SHORT LONG TRAP` - отдельный шорт-сигнал: после пампа цена уже не продолжает рост, OI растет, а futures CVD уходит в минус. Это сценарий ловушки поздних лонгов или набора шорта до явного breakdown.

Настройки отдельного dump trend scanner:

```text
DUMP_ENABLED=true
DUMP_SCAN_INTERVAL_SECONDS=120
DUMP_WINDOW_MINUTES=15
DUMP_LOOKBACK_DAYS=2
DUMP_STRUCTURE_CACHE_MINUTES=30
DUMP_MIN_TURNOVER_24H_USDT=2000000
DUMP_MAX_SYMBOLS=40
DUMP_MIN_PRICE_GROWTH_LOOKBACK_PCT=15
DUMP_MIN_DRAWDOWN_FROM_HIGH_PCT=4
DUMP_MIN_PRICE_DROP_WINDOW_PCT=0.5
DUMP_MIN_NEGATIVE_CVD_DELTA_USDT=5000
DUMP_MAX_OI_DROP_WINDOW_PCT=8
DUMP_MAX_FUNDING_RATE=0.002
DUMP_MIN_SIGNAL_SCORE=5
DUMP_WATCHLIST_MIN_SCORE=4
DUMP_CONSECUTIVE_CHECKS=1
DUMP_SYMBOL_COOLDOWN_MINUTES=60
DUMP_ALERT_COOLDOWN_MINUTES=45
DUMP_ALERT_SCORE_IMPROVEMENT=2
```

Dump-бот запускается отдельно по Bybit и Binance. В начале сообщения будет источник:

```text
🔻 DUMP TREND | BYBIT
🔻 DUMP TREND | BINANCE
```

Он ищет не идеальный финал пампа, а начало или продолжение слива: до этого был разгон, цена уже откатывается от high, за короткое окно цена падает, а futures CVD показывает продажи. OI используется как усилитель сигнала, но не как жесткая причина отсеять монету.

`DUMP_SYMBOL_COOLDOWN_MINUTES` защищает от дублей: если сигнал по монете уже пришел с Binance, Bybit не отправит такой же dump-сигнал по этой монете до конца cooldown, и наоборот.

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

## Исследование импульсов

Для подбора порогов есть отдельный offline-скрипт:

```bash
python research/backtest_impulses.py --days 365 --max-symbols 1000 --min-impulse-pct 50 --window-days 2
```

Если локальный Python на macOS ругается на сертификаты, запусти так:

```bash
python research/backtest_impulses.py --days 365 --max-symbols 1000 --min-impulse-pct 50 --window-days 2 --verify-ssl false
```

Он скачивает дневные свечи Binance USDT perpetual, находит монеты, которые дали больше `50%` за `1-2` дня, и сохраняет:

```text
research/impulse_events.csv
research/impulse_summary.txt
```

В таблице считаются:

- диапазон базы перед импульсом;
- рост/падение базы;
- всплеск объема относительно базы;
- proxy taker delta по дневным свечам;
- сколько тихих дней было перед импульсом;
- насколько цена была близко к high базы перед выносом.

Важно: публичный Binance REST не дает полную годовую историю OI/taker-flow по всем монетам через статистические endpoints. Поэтому этот скрипт дает price/volume/taker-delta основу, а OI лучше донакапливать live в базе бота или брать из отдельного исторического источника.

## Как считается CVD

Бот берет последние публичные сделки Bybit. Если taker side = `Buy`, объем сделки идет в плюс. Если `Sell`, объем идет в минус. Для расчета используется notional в USDT:

```text
price * size
```

Чтобы не считать одну сделку дважды, бот хранит `execId` последних сделок в `state.json`.

## Важное ограничение MVP

Это первая версия на REST API. Она простая и понятная, но для очень частого сканирования всего рынка лучше перейти на WebSocket, чтобы CVD был точнее и нагрузка на API была ниже.
