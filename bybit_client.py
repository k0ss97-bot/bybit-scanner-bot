from __future__ import annotations

from dataclasses import dataclass
import json
import ssl
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from liquidity import OrderbookLevel, OrderbookLiquidity, calculate_orderbook_liquidity


@dataclass(frozen=True)
class Ticker:
    symbol: str
    price: float
    open_interest: float
    funding_rate: float
    turnover_24h: float
    volume_24h: float
    high_price_24h: float
    low_price_24h: float
    price_change_24h_pct: float


@dataclass(frozen=True)
class Trade:
    exec_id: str
    symbol: str
    price: float
    size: float
    side: str
    time_ms: int

    @property
    def signed_notional(self) -> float:
        sign = 1 if self.side == "Buy" else -1
        return sign * self.price * self.size


@dataclass(frozen=True)
class Kline:
    start_ms: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    turnover: float


class BybitClient:
    _lock = threading.Lock()
    _last_request_ts = 0.0

    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 15,
        verify_ssl: bool = True,
        min_request_interval_seconds: float = 0.35,
        rate_limit_backoff_seconds: float = 3,
        max_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.ssl_context = None if verify_ssl else ssl._create_unverified_context()
        self.min_request_interval_seconds = min_request_interval_seconds
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self.max_retries = max_retries

    def get_linear_tickers(self) -> list[Ticker]:
        data = self._get("/v5/market/tickers", {"category": "linear"})
        tickers = []
        for item in data["result"]["list"]:
            symbol = item.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            tickers.append(
                Ticker(
                    symbol=symbol,
                    price=_to_float(item.get("lastPrice")),
                    open_interest=_to_float(item.get("openInterest")),
                    funding_rate=_to_float(item.get("fundingRate")),
                    turnover_24h=_to_float(item.get("turnover24h")),
                    volume_24h=_to_float(item.get("volume24h")),
                    high_price_24h=_to_float(item.get("highPrice24h")),
                    low_price_24h=_to_float(item.get("lowPrice24h")),
                    price_change_24h_pct=_to_float(item.get("price24hPcnt")) * 100,
                )
            )
        return tickers

    def get_recent_trades(
        self,
        symbol: str,
        limit: int = 1000,
        category: str = "linear",
    ) -> list[Trade]:
        data = self._get(
            "/v5/market/recent-trade",
            {"category": category, "symbol": symbol, "limit": limit},
        )
        trades = []
        for item in data["result"]["list"]:
            trades.append(
                Trade(
                    exec_id=str(item["execId"]),
                    symbol=str(item["symbol"]),
                    price=_to_float(item["price"]),
                    size=_to_float(item["size"]),
                    side=str(item["side"]),
                    time_ms=int(item["time"]),
                )
            )
        return trades

    def get_daily_klines(self, symbol: str, limit: int = 5) -> list[Kline]:
        data = self._get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "D",
                "limit": limit,
            },
        )
        klines = []
        for item in data["result"]["list"]:
            klines.append(
                Kline(
                    start_ms=int(item[0]),
                    open_price=_to_float(item[1]),
                    high_price=_to_float(item[2]),
                    low_price=_to_float(item[3]),
                    close_price=_to_float(item[4]),
                    volume=_to_float(item[5]),
                    turnover=_to_float(item[6]),
                )
            )
        return sorted(klines, key=lambda item: item.start_ms)

    def get_orderbook_liquidity(
        self,
        symbol: str,
        turnover_24h: float,
        limit: int = 100,
        depth_pct: float = 1.0,
        category: str = "linear",
    ) -> OrderbookLiquidity:
        data = self._get(
            "/v5/market/orderbook",
            {
                "category": category,
                "symbol": symbol,
                "limit": limit,
            },
        )
        result = data["result"]
        bids = [
            OrderbookLevel(price=_to_float(item[0]), size=_to_float(item[1]))
            for item in result.get("b", [])
        ]
        asks = [
            OrderbookLevel(price=_to_float(item[0]), size=_to_float(item[1]))
            for item in result.get("a", [])
        ]
        return calculate_orderbook_liquidity(
            symbol=symbol,
            bids=bids,
            asks=asks,
            turnover_24h=turnover_24h,
            depth_pct=depth_pct,
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(params)}"
        for attempt in range(self.max_retries + 1):
            self._wait_for_turn()
            try:
                with urlopen(url, timeout=self.timeout_seconds, context=self.ssl_context) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except URLError:
                if attempt < self.max_retries:
                    time.sleep(self.rate_limit_backoff_seconds * (attempt + 1))
                    continue
                raise
            if data.get("retCode") == 0:
                return data

            if data.get("retCode") == 10006 and attempt < self.max_retries:
                time.sleep(self.rate_limit_backoff_seconds * (attempt + 1))
                continue

            raise RuntimeError(f"Bybit error {data.get('retCode')}: {data.get('retMsg')}")

        raise RuntimeError("Bybit request failed")

    def _wait_for_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait_seconds = self.min_request_interval_seconds - (now - self._last_request_ts)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self.__class__._last_request_ts = time.monotonic()


def _to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)
