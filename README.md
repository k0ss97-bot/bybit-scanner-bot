# Bybit Scanner

Рабочая версия сейчас оставляет только один тип сигнала:

```text
🔻 DUMP TREND
```

Бот запускает два dump-сканера:

- `DUMP BYBIT`
- `DUMP BINANCE`

Старые LONG, PUMP, SHORT и SPRING ветки убраны из рабочего запуска. Их будем собирать заново позже.

Binance-сканер использует Binance-данные только по тем USDT-перпетам, которые есть на Bybit. Если монеты нет на Bybit, бот не отправляет по ней сигнал.

## Запуск

```bash
python main_bothost.py
```

Dockerfile тоже запускает:

```text
python main_bothost.py
```

## Настройки

Смотри [BOTHOST.md](BOTHOST.md) и [.env.example](.env.example).

Минимально нужны:

```text
TELEGRAM_BOT_TOKEN=токен_от_BotFather
TELEGRAM_CHAT_ID=твой_chat_id
TELEGRAM_ENABLED=true
DUMP_ENABLED=true
```

## Что ищет DUMP TREND

Сигнал ищет начало или продолжение слива:

- монета уже разгонялась;
- цена откатилась от high;
- в коротком окне цена падает;
- futures CVD показывает продажи;
- OI/funding усиливают оценку.

Telegram-сообщения:

```text
🔻 DUMP TREND | BINANCE + BYBIT
```

## Антиспам

```text
TELEGRAM_SYMBOL_COOLDOWN_MINUTES=240
DUMP_SYMBOL_COOLDOWN_MINUTES=60
```

`TELEGRAM_SYMBOL_COOLDOWN_MINUTES=240` не дает одной монете спамить в Telegram чаще 1 раза за 4 часа.

`DUMP_SYMBOL_COOLDOWN_MINUTES=60` защищает внутренний цикл от повторной подготовки одного сигнала, а Telegram-лимит остаётся главным антиспамом на 4 часа.

`DUMP_EVALUATION_ENABLED=true` сохраняет последнюю причину по монете: была ли она вне `DUMP_MAX_SYMBOLS`, не торгуется ли на Bybit, ушла ли на cooldown или не прошла условия.

Активный DUMP-сканер использует двухэтапный отбор: top-100 проходит легкую проверку структуры, после чего до 30 кандидатов получают глубокий анализ сделок, CVD и OI. Сигнал отправляется только при совпадении направления Binance и Bybit и помечается как `LIQUIDATION_FLUSH` или `SHORT_TREND`. Статистика результата считается через 15, 30, 60 и 240 минут только для реально отправленных Telegram-сигналов.

При `DUMP_CHART_ENABLED=true` сигнал приходит одним сообщением: PNG-график с полной статистикой в подписи. На графике находятся 15-минутные свечи Binance за 48 часов, объем, OI, CVD, high разгона, точка сигнала, отмена сценария и цели 1R/2R.

При `OPENAI_ANALYSIS_ENABLED=true` и заполненном `OPENAI_API_KEY` бот сразу отправляет обычный сигнал, затем OpenAI анализирует график и метрики, ищет свежую информацию о монете в интернете и дописывает краткое заключение с диапазоном входа, отменой сценария и целями в это же сообщение. Ошибка или таймаут OpenAI не блокируют сигнал скринера.

## Telegram-команды

```text
/status
/status dump bybit
/status dump binance
/settings
/stats
/why
/closest
/last
/pause
/resume
```

## Данные

Бот хранит историю в SQLite:

```text
$DATA_DIR/scanner.db
```

Если `DATA_DIR` не задан, используется папка `data/`.
