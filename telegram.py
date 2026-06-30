from __future__ import annotations

import json
import ssl
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from long_scanner import LongSignal
from pump_exhaustion_scanner import PumpExhaustionSignal


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

    def send_signal(self, signal: LongSignal) -> None:
        self.send_message(format_signal(signal))

    def send_message(self, text: str) -> None:
        if not self.enabled:
            print(text)
            return

        payload = json.dumps(
            {
                "chat_id": self.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds, context=self.ssl_context):
                return
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram error {error.code}: {body}") from error


def format_signal(signal: LongSignal) -> str:
    return (
        "🟢 LONG WATCH\n\n"
        f"Монета: {signal.symbol}\n"
        f"График: https://www.bybit.com/trade/usdt/{signal.symbol}\n"
        f"Окно: {signal.window_minutes}m\n\n"
        f"Сила сигнала: {signal.signal_score}/10\n"
        f"База {signal.lookback_days}d: {signal.base_growth_pct:+.2f}%\n"
        f"Цена от начала базы: {signal.current_from_base_pct:+.2f}%\n"
        f"High базы: {signal.base_high_price:g}\n"
        f"Оборот к базе: x{signal.turnover_ratio_to_base:.2f}\n"
        f"Средний оборот базы: {signal.base_avg_turnover:,.0f} USDT\n"
        f"OI: +{signal.oi_change_pct:.2f}%\n"
        f"Futures CVD: +{signal.cvd_change_pct:.2f}%\n"
        f"Futures CVD delta: {signal.cvd_delta_usdt:,.0f} USDT\n"
        f"Spot CVD: {signal.spot_cvd_change_pct:+.2f}%\n"
        f"Spot CVD delta: {signal.spot_cvd_delta_usdt:,.0f} USDT\n"
        f"Funding: {signal.funding_rate * 100:.4f}%\n"
        f"Price: {signal.price_change_pct:+.2f}%\n"
        f"Last price: {signal.price:g}\n"
        f"Turnover 24h: {signal.turnover_24h:,.0f} USDT\n\n"
        f"New trades: {signal.new_trades}\n"
        f"New spot trades: {signal.new_spot_trades}\n"
        f"Confirmations: {signal.consecutive_matches}\n\n"
        "Причина: монета не была сильно разогнана в базе, но сейчас резко растут OI и futures CVD. Возможное зарождение импульса."
    )


def format_pump_signal(signal: PumpExhaustionSignal) -> str:
    return (
        "🔴 PUMP EXHAUSTION WATCH\n\n"
        f"Монета: {signal.symbol}\n"
        f"График: https://www.bybit.com/trade/usdt/{signal.symbol}\n"
        f"Окно слабости: {signal.window_minutes}m\n\n"
        f"Рост за {signal.lookback_days}d: {signal.price_growth_lookback_pct:+.2f}%\n"
        f"Откат от high разгона: {signal.drawdown_from_high_pct:.2f}%\n"
        f"OI за окно: {signal.oi_change_pct:+.2f}%\n"
        f"Требуемое падение OI: -{signal.required_oi_drop_pct:.2f}%\n"
        f"Futures CVD за окно: {signal.cvd_change_pct:+.2f}%\n"
        f"Futures CVD delta: {signal.cvd_delta_usdt:,.0f} USDT\n"
        f"Spot CVD за окно: {signal.spot_cvd_change_pct:+.2f}%\n"
        f"Spot CVD delta: {signal.spot_cvd_delta_usdt:,.0f} USDT\n"
        f"Price за окно: {signal.price_change_window_pct:+.2f}%\n"
        f"Funding: {signal.funding_rate * 100:.4f}%\n"
        f"Last price: {signal.price:g}\n"
        f"High разгона: {signal.high_price_24h:g}\n"
        f"Turnover 24h: {signal.turnover_24h:,.0f} USDT\n"
        f"New trades: {signal.new_trades}\n"
        f"New spot trades: {signal.new_spot_trades}\n"
        f"Confirmations: {signal.consecutive_matches}\n\n"
        "Причина: монета сильно росла 1-2 дня, откатилась от хая, OI стоит/падает, futures CVD уходит в минус. Возможное распределение / long trap."
    )
