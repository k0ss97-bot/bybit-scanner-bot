# Deploy 24/7

Текущая рабочая версия запускает только `DUMP TREND`.

## Bothost

Используй Dockerfile или start command:

```bash
python main_bothost.py
```

## VPS

```bash
cd /opt/bybit-scanner
python3 main_bothost.py
```

Для постоянного запуска можно сделать один systemd-сервис на `main_bothost.py`.

Старые отдельные сервисы LONG/PUMP удалены.
