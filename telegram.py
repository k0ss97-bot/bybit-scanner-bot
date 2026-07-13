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
    mode_title = {
        "LIQUIDATION_FLUSH": "Ликвидационный слив",
        "SHORT_TREND": "Продолжение шорт-тренда",
    }.get(signal.mode, signal.mode)
    timeframe_lines = _dump_timeframe_lines(signal)
    funding_line = (
        f"Funding: {signal.funding_rate * 100:.4f}%\n"
        if getattr(signal, "funding_available", True)
        else "Funding: нет данных\n"
    )
    confirmation_block = ""
    if signal.confirmation_source:
        confirmation_block = (
            f"Bybit 1H: цена {signal.confirmation_price_change_pct:+.2f}% | "
            f"OI {signal.confirmation_oi_change_pct:+.2f}% | "
            f"CVD {_compact_usdt(signal.confirmation_cvd_delta_usdt)}\n"
        )
    return (
        f"🔻 DUMP TREND | {signal.source.replace('+', ' + ')}\n"
        f"{signal.symbol} | {mode_title} | {signal.signal_score}/10\n"
        f"Bybit: {bybit_chart_url}\n\n"
        f"Период | Цена | OI | Futures CVD\n"
        f"{timeframe_lines}\n\n"
        f"Рост за {signal.lookback_days}d: {signal.price_growth_lookback_pct:+.2f}%\n"
        f"Откат от high: {signal.drawdown_from_high_pct:+.2f}%\n"
        f"{funding_line}"
        f"Цена: {signal.price:g} | high: {signal.high_price:g}\n"
        f"Оборот 24h: {_compact_usdt(signal.turnover_24h)}\n"
        f"{confirmation_block}"
    )


def _dump_timeframe_lines(signal: DumpSignal) -> str:
    timeframes = getattr(signal, "timeframes", ()) or ()
    if not timeframes:
        return (
            f"1H | {_optional_pct(getattr(signal, 'price_change_window_pct', None))} | "
            f"{_optional_pct(getattr(signal, 'oi_change_pct', None))} | "
            f"{_compact_usdt(getattr(signal, 'cvd_delta_usdt', None))}"
        )
    return "\n".join(
        f"{timeframe.label} | {_optional_pct(timeframe.price_change_pct)} | "
        f"{_optional_pct(timeframe.oi_change_pct)} | "
        f"{_compact_usdt(timeframe.cvd_delta_usdt)}"
        for timeframe in timeframes
    )


def _optional_pct(value: float | None) -> str:
    return "нет данных" if value is None else f"{value:+.2f}%"


def _compact_usdt(value: float | None) -> str:
    if value is None:
        return "нет данных"
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:+.1f}M USDT"
    if absolute >= 1_000:
        return f"{value / 1_000:+.0f}K USDT"
    return f"{value:+.0f} USDT"
