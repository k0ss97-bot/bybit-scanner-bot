from __future__ import annotations

import json
import ssl
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dump_scanner import DumpSignal
from long_scanner import LongSignal, LongWatchlistAlert
from pump_exhaustion_scanner import PumpExhaustionSignal, PumpWatchlistAlert, ShortBreakdownSignal


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

    def send_message(self, text: str, reply_markup: dict | None = None) -> None:
        if not self.enabled:
            print(text)
            return

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        self._post("sendMessage", payload)

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
        payload = json.dumps(
            payload
        ).encode("utf-8")
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


def format_signal(signal: LongSignal) -> str:
    setup_type = getattr(signal, "setup_type", "momentum")
    if setup_type == "accumulation":
        title = "🟢 LONG ACCUMULATION"
        reason = "Причина: цена еще почти не ушла вверх, но OI и futures CVD уже набираются. Возможная фаза накопления перед импульсом."
    elif setup_type == "breakout":
        title = "🟢 LONG BREAKOUT"
        reason = "Причина: цена выходит из накопления, OI и futures CVD поддерживают движение. Возможное начало импульса."
    else:
        title = "🟢 LONG WATCH"
        reason = "Причина: цена уже пошла вверх, futures CVD поддерживает движение. Возможное развитие импульса."
    return (
        f"{title}\n\n"
        f"Монета: {signal.symbol}\n"
        f"График: https://www.bybit.com/trade/usdt/{signal.symbol}\n"
        f"Окно: {signal.window_minutes}m\n\n"
        f"Сила сигнала: {signal.signal_score}/10\n"
        f"База {signal.lookback_days}d: {signal.base_growth_pct:+.2f}%\n"
        f"Диапазон базы: {signal.base_range_pct:.2f}%\n"
        f"Цена от начала базы: {signal.current_from_base_pct:+.2f}%\n"
        f"Цена от high базы: {signal.price_from_base_high_pct:+.2f}%\n"
        f"Рост 24h: {signal.price_change_24h_pct:+.2f}%\n"
        f"High базы: {signal.base_high_price:g}\n"
        f"Low базы: {signal.base_low_price:g}\n"
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
        f"{reason}"
    )


def format_pump_signal(signal: PumpExhaustionSignal) -> str:
    return (
        "🔴 PUMP EXHAUSTION WATCH\n\n"
        f"Монета: {signal.symbol}\n"
        f"График: https://www.bybit.com/trade/usdt/{signal.symbol}\n"
        f"Окно слабости: {signal.window_minutes}m\n\n"
        f"Сила сигнала: {signal.signal_score}/10\n"
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


def format_short_breakdown_signal(signal: ShortBreakdownSignal) -> str:
    is_long_trap = getattr(signal, "setup_type", "breakdown") == "long_trap"
    title = "🔻 SHORT LONG TRAP" if is_long_trap else "🔻 SHORT BREAKDOWN"
    reason = (
        "Причина: после пампа цена уже не продолжает рост, OI растет, а futures CVD отрицательный. Возможна ловушка для поздних лонгов / набор шорта."
        if is_long_trap
        else "Причина: после пампа цена падает, OI растет или не падает, futures CVD отрицательный. Возможен вход новых шортов / breakdown."
    )
    return (
        f"{title}\n\n"
        f"Монета: {signal.symbol}\n"
        f"График: https://www.bybit.com/trade/usdt/{signal.symbol}\n"
        f"Окно: {signal.window_minutes}m\n\n"
        f"Сила сигнала: {signal.signal_score}/10\n"
        f"Рост за {signal.lookback_days}d: {signal.price_growth_lookback_pct:+.2f}%\n"
        f"Откат от high: {signal.drawdown_from_high_pct:+.2f}%\n"
        f"OI за окно: {signal.oi_change_pct:+.2f}%\n"
        f"Futures CVD за окно: {signal.cvd_change_pct:+.2f}%\n"
        f"Futures CVD delta: {signal.cvd_delta_usdt:,.0f} USDT\n"
        f"Spot CVD за окно: {signal.spot_cvd_change_pct:+.2f}%\n"
        f"Spot CVD delta: {signal.spot_cvd_delta_usdt:,.0f} USDT\n"
        f"Price за окно: {signal.price_change_window_pct:+.2f}%\n"
        f"Funding: {signal.funding_rate * 100:.4f}%\n"
        f"Last price: {signal.price:g}\n"
        f"High пампа: {signal.high_price_24h:g}\n"
        f"Turnover 24h: {signal.turnover_24h:,.0f} USDT\n"
        f"New trades: {signal.new_trades}\n"
        f"New spot trades: {signal.new_spot_trades}\n"
        f"Confirmations: {signal.consecutive_matches}\n\n"
        f"{reason}"
    )


def format_dump_signal(signal: DumpSignal) -> str:
    source = signal.source.upper()
    chart_url = (
        f"https://www.binance.com/en/futures/{signal.symbol}"
        if source == "BINANCE"
        else f"https://www.bybit.com/trade/usdt/{signal.symbol}"
    )
    return (
        f"🔻 DUMP TREND | {source}\n\n"
        f"Монета: {signal.symbol}\n"
        f"График: {chart_url}\n"
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
        "Причина: после разгона монета начала откатываться от high, цена падает в коротком окне, а поток futures-сделок идет в продажу. Это модель входа в тренд слива."
    )


def format_long_watchlist(alert: LongWatchlistAlert) -> str:
    return (
        "🟡 LONG WATCHLIST\n\n"
        f"Монета: {alert.symbol}\n"
        f"График: https://www.bybit.com/trade/usdt/{alert.symbol}\n"
        f"Окно: {alert.window_minutes}m\n"
        f"Сила: {alert.signal_score}/10\n\n"
        f"OI: {alert.oi_change_pct:+.2f}%\n"
        f"Futures CVD: {alert.cvd_change_pct:+.2f}%\n"
        f"Futures CVD delta: {alert.cvd_delta_usdt:,.0f} USDT\n"
        f"Spot CVD: {alert.spot_cvd_change_pct:+.2f}%\n"
        f"Price: {alert.price_change_pct:+.2f}%\n"
        f"Оборот к базе: x{alert.turnover_ratio_to_base:.2f}\n"
        f"Last price: {alert.price:g}\n"
        f"Turnover 24h: {alert.turnover_24h:,.0f} USDT\n\n"
        f"Прошло: {', '.join(alert.passed_checks)}\n"
        f"Не хватает: {', '.join(alert.missing_checks)}"
    )


def format_pump_watchlist(alert: PumpWatchlistAlert) -> str:
    return (
        "🟠 PUMP WATCHLIST\n\n"
        f"Монета: {alert.symbol}\n"
        f"График: https://www.bybit.com/trade/usdt/{alert.symbol}\n"
        f"Окно слабости: {alert.window_minutes}m\n"
        f"Сила: {alert.signal_score}/10\n\n"
        f"Рост за {alert.lookback_days}d: {alert.price_growth_lookback_pct:+.2f}%\n"
        f"Откат от high: {alert.drawdown_from_high_pct:+.2f}%\n"
        f"OI за окно: {alert.oi_change_pct:+.2f}%\n"
        f"Требуемое падение OI: -{alert.required_oi_drop_pct:.2f}%\n"
        f"Futures CVD: {alert.cvd_change_pct:+.2f}%\n"
        f"Futures CVD delta: {alert.cvd_delta_usdt:,.0f} USDT\n"
        f"Price за окно: {alert.price_change_window_pct:+.2f}%\n"
        f"Last price: {alert.price:g}\n"
        f"Turnover 24h: {alert.turnover_24h:,.0f} USDT\n\n"
        f"Прошло: {', '.join(alert.passed_checks)}\n"
        f"Не хватает: {', '.join(alert.missing_checks)}"
    )
