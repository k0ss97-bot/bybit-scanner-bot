# Запуск на Bothost

Боевая версия сейчас оставляет только один рабочий тип сигнала:

```text
🔻 DUMP TREND
```

`main_bothost.py` запускает два dump-сканера:

- `DUMP BYBIT`
- `DUMP BINANCE`

LONG, PUMP, SHORT и SPRING в рабочем запуске отключены.

## Start command

```bash
python main_bothost.py
```

Если Bothost использует Dockerfile, команда уже задана внутри Dockerfile.

## Переменные окружения

В панели Bothost оставь только эти переменные:

```text
SCANNER_BOT_TOKEN=токен_бота
TELEGRAM_CHAT_ID=твой_chat_id
TELEGRAM_ENABLED=true
STARTUP_NOTIFICATIONS=false
TELEGRAM_SYMBOL_COOLDOWN_MINUTES=240

VERIFY_SSL=true
DEBUG_ERRORS=false
STATUS_COMMANDS_ENABLED=true
STATUS_POLL_INTERVAL_SECONDS=5
CANDIDATE_TRACKING_ENABLED=true
HISTORY_SNAPSHOT_RETENTION_DAYS=7
WATCHLIST_RETENTION_DAYS=7
WATCHLIST_COOLDOWN_MINUTES=120
WATCHLIST_MAX_ALERTS_PER_SCAN=5

BYBIT_BASE_URL=https://api.bybit.com
BYBIT_MIN_REQUEST_INTERVAL_SECONDS=0.35
BYBIT_RATE_LIMIT_BACKOFF_SECONDS=3
BYBIT_MAX_RETRIES=2
BINANCE_BASE_URL=https://fapi.binance.com

DUMP_ENABLED=true
DUMP_SCAN_INTERVAL_SECONDS=120
DUMP_WINDOW_MINUTES=60
DUMP_LOOKBACK_DAYS=2
DUMP_STRUCTURE_CACHE_MINUTES=30
DUMP_MIN_TURNOVER_24H_USDT=2000000
DUMP_MAX_SYMBOLS=100
DUMP_DEEP_MAX_SYMBOLS=30
DUMP_REQUIRE_BYBIT_LISTING=true
DUMP_BYBIT_SYMBOL_CACHE_MINUTES=15
DUMP_EVALUATION_ENABLED=true
DUMP_MAX_EVALUATION_SYMBOLS=120
DUMP_WATCHLIST_SNAPSHOT_MINUTES=30
DUMP_EVALUATION_SNAPSHOT_MINUTES=60
DUMP_TRADE_MAX_PAGES=5
DUMP_CROSS_EXCHANGE_REQUIRED=true
DUMP_CROSS_EXCHANGE_MAX_AGE_SECONDS=300
DUMP_EXECUTION_QUOTE_MAX_AGE_SECONDS=15
DUMP_ENTRY_QUOTE_DELAYS_SECONDS=5,15,30
DUMP_REVIEW_MAX_LAG_SECONDS=300

PAPER_TRADING_ENABLED=true
PAPER_POLL_INTERVAL_SECONDS=15
PAPER_STARTING_EQUITY_USDT=10000
PAPER_RISK_PER_TRADE_PCT=0.5
PAPER_MAX_NOTIONAL_PCT=25
PAPER_MAX_OPEN_POSITIONS=3
PAPER_EPISODE_COOLDOWN_MINUTES=30
PAPER_STOP_LOSS_PCT=2
PAPER_MAX_HOLDING_MINUTES=240
PAPER_TRAILING_ACTIVATION_PCT=2
PAPER_TRAILING_DISTANCE_PCT=1.5
PAPER_ENTRY_FEE_BPS=5.5
PAPER_EXIT_FEE_BPS=5.5
PAPER_SLIPPAGE_BPS=5
PAPER_FUNDING_BUFFER_BPS=1

DUMP_LIQUIDATION_MIN_OI_DROP_PCT=1.5
DUMP_TREND_MIN_OI_CHANGE_PCT=-0.5
DUMP_CHART_ENABLED=true
DUMP_CHART_LOOKBACK_HOURS=168
DUMP_CHART_INTERVAL=1h
OPENAI_ANALYSIS_ENABLED=true
OPENAI_API_KEY=ваш_секретный_ключ_OpenAI
OPENAI_MODEL=gpt-5.6
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_TIMEOUT_SECONDS=45
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
```

## Telegram-кнопки

```text
📊 Статус - текущий прогресс DUMP-сканеров
⚙️ Настройки - активные dump-only настройки
📈 Статистика - статистика сигналов
❓ Почему нет сигналов - причины отсечения
🎯 Ближайшие - кандидаты из последнего скана
🕘 Последние сигналы - последние сигналы из базы
🧪 Paper - виртуальный баланс, сделки и просадка
🔻 DUMP BYBIT - статус Bybit dump-сканера
🔻 DUMP BINANCE - статус Binance dump-сканера
⏸ Пауза / ▶️ Старт - временно остановить или запустить dump-сканеры
```

Если кнопки не появились, напиши боту `/start` или `меню`.

## Безопасный этап автоматической торговли

`PAPER_TRADING_ENABLED=true` не требует Bybit API key и не может отправить реальный ордер. После каждого настоящего Telegram-сигнала бот открывает две локальные виртуальные SHORT-сделки и проверяет их по свежему ask Bybit каждые 15 секунд: фиксированный выход через четыре часа и trailing-вариант. Комиссии и проскальзывание уже вычитаются.

Команда `/paper` показывает накопленный результат. Непрерывность paper-наблюдения сбрасывается после остановки дольше часа. Реальный режим будет добавляться отдельно только после прохождения `Automation gate`; в текущей версии он жёстко заблокирован.

## Что ищет DUMP TREND

Сигнал ищет начало или продолжение слива:

- до этого был разгон;
- цена откатилась от high;
- за рабочее окно цена падает;
- futures CVD показывает продажи;
- OI и funding добавляют силу, но не являются единственным решением.

Сообщение в Telegram начинается так:

```text
🔻 DUMP TREND | BINANCE + BYBIT
```

`DUMP_SYMBOL_COOLDOWN_MINUTES` защищает от дублей между биржами: если монета уже пришла с Binance, Bybit не отправит такой же dump-сигнал по этой монете до конца cooldown, и наоборот.

`DUMP_REQUIRE_BYBIT_LISTING=true` оставляет Binance-данные только для монет, которые есть на Bybit linear USDT. Если монеты нет на Bybit, бот не покажет по ней сигнал.

`DUMP_EVALUATION_ENABLED=true` записывает последнюю причину по монетам: прошла ли монета в рабочий top, была ли вне `DUMP_MAX_SYMBOLS`, ушла ли на cooldown или не прошла условия сигнала. `DUMP_EVALUATION_SNAPSHOT_MINUTES=60` дополнительно сохраняет компактную часовую историю по каждой монете, а `DUMP_WATCHLIST_SNAPSHOT_MINUTES=30` ограничивает watchlist одним лучшим состоянием монеты за полчаса. Это уменьшает нагрузку SQLite и сохраняет данные для последующего разбора пропущенных движений. Легкий этап проверяет top-100, а дорогие запросы сделок и OI выполняются только для `DUMP_DEEP_MAX_SYMBOLS` кандидатов с нужным разгоном и откатом.

Funding загружается отдельным массовым запросом Binance. Если биржа временно не вернула funding, бот помечает метрику как недоступную и не начисляет за нее балл вместо подстановки ложного нуля. Нулевой или отсутствующий OI также больше не считается стабильным OI: такой кандидат блокируется до появления двух валидных часовых точек.

Финальный сигнал формируется по часовому окну Binance и требует свежего часового подтверждения направления на Bybit. Deep-кандидаты Binance проверяются на Bybit напрямую, даже если они находятся вне собственного top/deep-рейтинга Bybit. Модель `LIQUIDATION_FLUSH` означает падение цены вместе с OI, модель `SHORT_TREND` — падение цены при стабильном или растущем OI. В готовом сигнале отдельно показываются цена, OI и futures CVD за `1H`, `4H` и `1D`.

Перед отправкой бот получает свежие best bid/ask Bybit: для открытия шорта сохраняется bid, для оценки закрытия — ask. Дополнительные котировки через 5, 15 и 30 секунд измеряют задержку ручного входа. Результаты через 15, 30, 60 и 240 минут используют первую котировку Bybit после целевого времени, но не позднее чем через `DUMP_REVIEW_MAX_LAG_SECONDS`. Пропущенный замер не записывается как нулевая сделка и не попадает в winrate.

`DUMP_CHART_ENABLED=true` отправляет одно сообщение: PNG-график с часовыми свечами Binance за 7 дней, объемом, локальными OI/CVD, контекстом `1H/4H/1D` и уровнями signal, invalidation, target 1R/2R. Для четырехчасового графика задайте `DUMP_CHART_INTERVAL=4h`. Картинка строится только для готового сигнала; при ошибке бот отправляет одно обычное текстовое сообщение.

`OPENAI_ANALYSIS_ENABLED=true` включает дополнительную проверку готового сигнала. Добавьте секрет `OPENAI_API_KEY` в переменные Bothost, не в GitHub и не в код. Сигнал приходит без задержки, а после анализа OpenAI редактирует то же Telegram-сообщение: учитывает PNG-график, метрики, свежий интернет-фон и добавляет сценарные уровни входа, отмены и целей. При ошибке OpenAI исходный сигнал остается без изменений.
