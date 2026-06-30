from __future__ import annotations

from dataclasses import dataclass
import json
import ssl
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


@dataclass(frozen=True)
class BinanceTicker:
    symbol: str
    price_change_24h_pct: float
    quote_volume_24h: float


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
            )
        return tickers

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(url, timeout=self.timeout_seconds, context=self.ssl_context) as response:
                    return json.loads(response.read().decode("utf-8"))
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
