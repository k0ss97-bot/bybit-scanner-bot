from __future__ import annotations

from dataclasses import dataclass
import json
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from bybit_client import Kline, Trade


@dataclass(frozen=True)
class BinanceTicker:
    symbol: str
    price_change_24h_pct: float
    quote_volume_24h: float
    price: float = 0.0
    open_interest: float = 0.0
    funding_rate: float = 0.0
    turnover_24h: float = 0.0
    volume_24h: float = 0.0
    high_price_24h: float = 0.0
    low_price_24h: float = 0.0


class BinanceClient:
    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        timeout_seconds: int = 15,
        verify_ssl: bool = True,
        rate_limit_backoff_seconds: float = 3,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.ssl_context = None if verify_ssl else ssl._create_unverified_context()
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self.max_retries = max_retries

    def get_usdt_perp_tickers(self) -> dict[str, BinanceTicker]:
        data = self._get("/fapi/v1/ticker/24hr")
        tickers = {}
        for item in data:
            symbol = str(item.get("symbol", ""))
            if not symbol.endswith("USDT"):
                continue
            tickers[symbol] = BinanceTicker(
                symbol=symbol,
                price_change_24h_pct=_to_float(item.get("priceChangePercent")),
                quote_volume_24h=_to_float(item.get("quoteVolume")),
                price=_to_float(item.get("lastPrice")),
                turnover_24h=_to_float(item.get("quoteVolume")),
                volume_24h=_to_float(item.get("volume")),
                high_price_24h=_to_float(item.get("highPrice")),
                low_price_24h=_to_float(item.get("lowPrice")),
            )
        return tickers

    def get_open_interest(self, symbol: str) -> float:
        data = self._get("/fapi/v1/openInterest", {"symbol": symbol})
        return _to_float(data.get("openInterest"))

    def get_recent_trades(self, symbol: str, limit: int = 1000) -> list[Trade]:
        data = self._get("/fapi/v1/aggTrades", {"symbol": symbol, "limit": limit})
        trades = []
        for item in data:
            is_buyer_maker = bool(item.get("m"))
            trades.append(
                Trade(
                    exec_id=str(item.get("a")),
                    symbol=symbol,
                    price=_to_float(item.get("p")),
                    size=_to_float(item.get("q")),
                    side="Sell" if is_buyer_maker else "Buy",
                    time_ms=int(item.get("T", 0)),
                )
            )
        return trades

    def get_daily_klines(self, symbol: str, limit: int = 5) -> list[Kline]:
        data = self._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1d", "limit": limit},
        )
        klines = []
        for item in data:
            klines.append(
                Kline(
                    start_ms=int(item[0]),
                    open_price=_to_float(item[1]),
                    high_price=_to_float(item[2]),
                    low_price=_to_float(item[3]),
                    close_price=_to_float(item[4]),
                    volume=_to_float(item[5]),
                    turnover=_to_float(item[7]),
                )
            )
        return sorted(klines, key=lambda item: item.start_ms)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{query}"
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(url, timeout=self.timeout_seconds, context=self.ssl_context) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError:
                raise
            except URLError:
                if attempt < self.max_retries:
                    time.sleep(self.rate_limit_backoff_seconds * (attempt + 1))
                    continue
                raise
        raise RuntimeError("Binance request failed")


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)
