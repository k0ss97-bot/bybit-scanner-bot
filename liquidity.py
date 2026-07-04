from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderbookLevel:
    price: float
    size: float

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass(frozen=True)
class OrderbookLiquidity:
    symbol: str
    spread_bps: float
    bid_depth_1pct_usdt: float
    ask_depth_1pct_usdt: float
    depth_1pct_usdt: float
    depth_coverage_1h: float
    quality: str


EMPTY_LIQUIDITY = OrderbookLiquidity(
    symbol="",
    spread_bps=0.0,
    bid_depth_1pct_usdt=0.0,
    ask_depth_1pct_usdt=0.0,
    depth_1pct_usdt=0.0,
    depth_coverage_1h=0.0,
    quality="unknown",
)


def unknown_liquidity(symbol: str) -> OrderbookLiquidity:
    return OrderbookLiquidity(
        symbol=symbol,
        spread_bps=0.0,
        bid_depth_1pct_usdt=0.0,
        ask_depth_1pct_usdt=0.0,
        depth_1pct_usdt=0.0,
        depth_coverage_1h=0.0,
        quality="unknown",
    )


def calculate_orderbook_liquidity(
    *,
    symbol: str,
    bids: list[OrderbookLevel],
    asks: list[OrderbookLevel],
    turnover_24h: float,
    depth_pct: float = 1.0,
) -> OrderbookLiquidity:
    if not bids or not asks:
        return unknown_liquidity(symbol)

    best_bid = bids[0].price
    best_ask = asks[0].price
    mid_price = (best_bid + best_ask) / 2
    if best_bid <= 0 or best_ask <= 0 or mid_price <= 0:
        return unknown_liquidity(symbol)

    spread_bps = ((best_ask - best_bid) / mid_price) * 10_000
    bid_floor = mid_price * (1 - depth_pct / 100)
    ask_ceiling = mid_price * (1 + depth_pct / 100)
    bid_depth = sum(level.notional for level in bids if level.price >= bid_floor)
    ask_depth = sum(level.notional for level in asks if level.price <= ask_ceiling)
    depth_1pct = min(bid_depth, ask_depth)
    estimated_1h_turnover = max(0.0, turnover_24h) / 24
    depth_coverage = (
        depth_1pct / estimated_1h_turnover
        if estimated_1h_turnover > 0
        else 0.0
    )
    return OrderbookLiquidity(
        symbol=symbol,
        spread_bps=spread_bps,
        bid_depth_1pct_usdt=bid_depth,
        ask_depth_1pct_usdt=ask_depth,
        depth_1pct_usdt=depth_1pct,
        depth_coverage_1h=depth_coverage,
        quality=classify_liquidity(spread_bps, depth_coverage),
    )


def classify_liquidity(spread_bps: float, depth_coverage_1h: float) -> str:
    if spread_bps <= 0 and depth_coverage_1h <= 0:
        return "unknown"
    if spread_bps > 30 or depth_coverage_1h < 0.25:
        return "fragile"
    if spread_bps > 15 or depth_coverage_1h < 1:
        return "stressed"
    return "healthy"


def liquidity_score(liquidity: OrderbookLiquidity) -> int:
    if liquidity.quality == "healthy":
        return 2
    if liquidity.quality == "stressed":
        return 1
    return 0


def fragile_liquidity_score(liquidity: OrderbookLiquidity) -> int:
    if liquidity.quality == "fragile":
        return 2
    if liquidity.quality == "stressed":
        return 1
    return 0
