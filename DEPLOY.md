# Deploy 24/7 на VPS

Нужен обычный VPS с Ubuntu. Для текущего MVP достаточно минимального сервера: 1 CPU, 1 GB RAM.

## 1. Подключиться к серверу

```bash
ssh root@SERVER_IP
```

## 2. Установить Python

```bash
apt update
apt install -y python3 rsync
```

## 3. Создать пользователя и папку

```bash
adduser --disabled-password --gecos "" deploy
mkdir -p /opt/bybit-scanner
chown -R deploy:deploy /opt/bybit-scanner
```

## 4. Загрузить проект с Mac

На Mac, из папки проекта:

```bash
cd /Users/konstantingorskih/Documents/Codex/2026-06-28/fyf/outputs/bybit-long-scanner
rsync -av --exclude '.venv' --exclude '__pycache__' ./ root@SERVER_IP:/opt/bybit-scanner/
```

## 5. Проверить `.env` на сервере

```bash
ssh root@SERVER_IP
nano /opt/bybit-scanner/.env
```

На сервере лучше поставить:

```text
VERIFY_SSL=true
DEBUG_ERRORS=false
```

Если сервер тоже ругается на сертификаты, временно верни:

```text
VERIFY_SSL=false
```

## 6. Проверить запуск вручную

```bash
cd /opt/bybit-scanner
python3 main.py --test-telegram
python3 main_pump.py --test-telegram
```

Должны прийти 2 сообщения в Telegram.

## 7. Установить systemd-сервисы

```bash
cp /opt/bybit-scanner/deploy/bybit-long-scanner.service /etc/systemd/system/
cp /opt/bybit-scanner/deploy/bybit-pump-scanner.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable bybit-long-scanner
systemctl enable bybit-pump-scanner
systemctl start bybit-long-scanner
systemctl start bybit-pump-scanner
```

## 8. Проверить статус

```bash
systemctl status bybit-long-scanner
systemctl status bybit-pump-scanner
```

Логи:

```bash
journalctl -u bybit-long-scanner -f
journalctl -u bybit-pump-scanner -f
```

## 9. Обновление кода

На Mac:

```bash
rsync -av --exclude '.venv' --exclude '__pycache__' ./ root@SERVER_IP:/opt/bybit-scanner/
```

На сервере:

```bash
systemctl restart bybit-long-scanner
systemctl restart bybit-pump-scanner
```
