# Bybit Scanner

Рабочая версия сейчас оставляет только один тип сигнала:

```text
🔻 DUMP TREND
```

Бот запускает два dump-сканера:

- `DUMP BYBIT`
- `DUMP BINANCE`

Старые LONG, PUMP, SHORT и SPRING ветки убраны из рабочего запуска. Их будем собирать заново позже.

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
🔻 DUMP TREND | BYBIT
🔻 DUMP TREND | BINANCE
```

## Антиспам

```text
TELEGRAM_SYMBOL_COOLDOWN_MINUTES=240
DUMP_SYMBOL_COOLDOWN_MINUTES=60
DUMP_ALERT_COOLDOWN_MINUTES=45
```

`TELEGRAM_SYMBOL_COOLDOWN_MINUTES=240` не дает одной монете спамить в Telegram чаще 1 раза за 4 часа.

`DUMP_SYMBOL_COOLDOWN_MINUTES=60` защищает от дубля между Binance и Bybit по одной монете.

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
