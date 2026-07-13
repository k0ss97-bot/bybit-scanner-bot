from __future__ import annotations

import json
import ssl
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dump_scanner import DumpSignal


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        timeout_seconds: int = 15,
        verify_ssl: bool = True,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self.ssl_context = None if verify_ssl else ssl._create_unverified_context()

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_message(self, text: str, reply_markup: dict | None = None) -> dict:
        if not self.enabled:
            print(text)
            return {}

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        return self._post("sendMessage", payload)

    def send_photo(self, photo: bytes, caption: str = "") -> dict:
        if not self.enabled:
            print(caption or f"Chart image: {len(photo)} bytes")
            return {}
        return self._post_multipart(
            "sendPhoto",
            fields={"chat_id": self.chat_id, "caption": caption},
            file_field="photo",
            filename="dump-signal.png",
            content_type="image/png",
            content=photo,
        )

    def edit_message_caption(self, message_id: int, caption: str) -> dict:
        if not self.enabled:
            print(caption)
            return {}
        return self._post(
            "editMessageCaption",
            {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "caption": caption,
            },
        )

    def edit_message_text(self, message_id: int, text: str) -> dict:
        if not self.enabled:
            print(text)
            return {}
        return self._post(
            "editMessageText",
            {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

    def get_updates(self, offset: int | None = None, timeout_seconds: int = 20) -> list[dict]:
        if not self.enabled:
            return []

        payload = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = self._post("getUpdates", payload, timeout_seconds=timeout_seconds + 5)
        return list(response.get("result", []))

    def _post(self, method: str, payload: dict, timeout_seconds: int | None = None) -> dict:
        payload = json.dumps(payload).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
            with urlopen(request, timeout=timeout, context=self.ssl_context) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram error {error.code}: {body}") from error

    def _post_multipart(
        self,
        method: str,
        *,
        fields: dict[str, str],
        file_field: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> dict:
        boundary = f"----scanner-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{file_field}"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        request = Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=b"".join(chunks),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds, context=self.ssl_context) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram error {error.code}: {body}") from error


def format_dump_signal(signal: DumpSignal) -> str:
    bybit_chart_url = f"https://www.bybit.com/trade/usdt/{signal.symbol}"
    data_chart_url = f"https://www.binance.com/en/futures/{signal.symbol}"
    data_chart_line = (
        f"График данных Binance: {data_chart_url}\n"
        if "BINANCE" in signal.source.upper()
        else ""
    )
    mode_title = {
        "LIQUIDATION_FLUSH": "Ликвидационный слив",
        "SHORT_TREND": "Продолжение шорт-тренда",
    }.get(signal.mode, signal.mode)
    reason = (
        "Цена и OI падают одновременно: закрываются или ликвидируются лонги, "
        "а поток futures-сделок подтверждает продажи."
        if signal.mode == "LIQUIDATION_FLUSH"
        else "Цена падает при стабильном или растущем OI: продавцы продолжают "
        "набирать шорты, а futures CVD подтверждает направление."
    )
    confirmation_block = ""
    if signal.confirmation_source:
        confirmation_block = (
            f"Подтверждение {signal.confirmation_source}:\n"
            f"Price: {signal.confirmation_price_change_pct:+.2f}%\n"
            f"OI: {signal.confirmation_oi_change_pct:+.2f}%\n"
            f"CVD delta: {signal.confirmation_cvd_delta_usdt:,.0f} USDT\n\n"
        )
    return (
        f"🔻 DUMP TREND | {signal.source.replace('+', ' + ')}\n"
        f"Модель: {mode_title}\n\n"
        f"Монета: {signal.symbol}\n"
        f"График Bybit: {bybit_chart_url}\n"
        f"{data_chart_line}"
        f"Окно: {signal.window_minutes}m\n\n"
        f"Сила сигнала: {signal.signal_score}/10\n"
        f"Рост за {signal.lookback_days}d: {signal.price_growth_lookback_pct:+.2f}%\n"
        f"Откат от high: {signal.drawdown_from_high_pct:+.2f}%\n"
        f"Price за окно: {signal.price_change_window_pct:+.2f}%\n"
        f"Futures CVD delta: {signal.cvd_delta_usdt:,.0f} USDT\n"
        f"OI за окно: {signal.oi_change_pct:+.2f}%\n"
        f"Funding: {signal.funding_rate * 100:.4f}%\n"
        f"Last price: {signal.price:g}\n"
        f"High разгона: {signal.high_price:g}\n"
        f"Turnover 24h: {signal.turnover_24h:,.0f} USDT\n"
        f"New futures trades: {signal.new_trades}\n"
        f"Confirmations: {signal.consecutive_matches}\n\n"
        f"{confirmation_block}"
        f"Причина: {reason}"
    )
