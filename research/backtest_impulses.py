from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import statistics
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class DailyKline:
    open_time: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    quote_volume: float
    taker_buy_quote_volume: float

    @property
    def taker_delta_quote(self) -> float:
        return self.taker_buy_quote_volume - (self.quote_volume - self.taker_buy_quote_volume)

    @property
    def taker_delta_pct(self) -> float:
        if self.quote_volume <= 0:
            return 0.0
        return (self.taker_delta_quote / self.quote_volume) * 100


def main() -> None:
    args = parse_args()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    symbols = parse_symbols(args.symbols)
    if not symbols:
        symbols = fetch_usdt_perp_symbols(args.base_url)
    symbols = symbols[: args.max_symbols]

    rows: list[dict[str, Any]] = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.days * DAY_MS

    for index, symbol in enumerate(symbols, start=1):
        try:
            klines = fetch_daily_klines(args.base_url, symbol, start_ms, end_ms)
            events = find_impulse_events(
                symbol=symbol,
                klines=klines,
                base_days=args.base_days,
                window_days=args.window_days,
                min_impulse_pct=args.min_impulse_pct,
                min_base_quote_volume=args.min_base_quote_volume,
            )
            rows.extend(events)
            print(
                f"{index}/{len(symbols)} {symbol}: days={len(klines)}, events={len(events)}",
                flush=True,
            )
        except Exception as error:
            print(f"{index}/{len(symbols)} {symbol}: failed: {error}", flush=True)
        time.sleep(args.request_delay)

    write_csv(out_path, rows)
    summary = build_summary(rows)
    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary)
    print(summary)
    print(f"Saved events: {out_path}")
    print(f"Saved summary: {summary_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find futures coins that moved more than N percent in 1-2 days and summarize pre-impulse metrics."
    )
    parser.add_argument("--base-url", default=BINANCE_FUTURES_BASE_URL)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--max-symbols", type=int, default=1000)
    parser.add_argument("--symbols", default="", help="Comma-separated symbols. Empty means all USDT perpetuals.")
    parser.add_argument("--base-days", type=int, default=7)
    parser.add_argument("--window-days", type=int, default=2)
    parser.add_argument("--min-impulse-pct", type=float, default=50)
    parser.add_argument("--min-base-quote-volume", type=float, default=0)
    parser.add_argument("--request-delay", type=float, default=0.12)
    parser.add_argument("--out", default="research/impulse_events.csv")
    parser.add_argument("--summary-out", default="research/impulse_summary.txt")
    return parser.parse_args()


def parse_symbols(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def fetch_usdt_perp_symbols(base_url: str) -> list[str]:
    data = get_json(base_url, "/fapi/v1/exchangeInfo")
    symbols = []
    for item in data.get("symbols", []):
        if (
            item.get("contractType") == "PERPETUAL"
            and item.get("quoteAsset") == "USDT"
            and item.get("status") == "TRADING"
        ):
            symbols.append(str(item["symbol"]))
    return sorted(symbols)


def fetch_daily_klines(
    base_url: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[DailyKline]:
    raw = get_json(
        base_url,
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "1d",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1500,
        },
    )
    klines = []
    for item in raw:
        klines.append(
            DailyKline(
                open_time=int(item[0]),
                open_price=to_float(item[1]),
                high_price=to_float(item[2]),
                low_price=to_float(item[3]),
                close_price=to_float(item[4]),
                quote_volume=to_float(item[7]),
                taker_buy_quote_volume=to_float(item[10]),
            )
        )
    return sorted(klines, key=lambda item: item.open_time)


def get_json(base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    with urlopen(f"{base_url.rstrip('/')}{path}{query}", timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def find_impulse_events(
    *,
    symbol: str,
    klines: list[DailyKline],
    base_days: int,
    window_days: int,
    min_impulse_pct: float,
    min_base_quote_volume: float,
) -> list[dict[str, Any]]:
    events = []
    index = base_days
    last_start = len(klines) - window_days
    while index <= last_start:
        base = klines[index - base_days : index]
        impulse = klines[index : index + window_days]
        if not base or not impulse:
            index += 1
            continue

        base_avg_quote_volume = mean(kline.quote_volume for kline in base)
        if base_avg_quote_volume < min_base_quote_volume:
            index += 1
            continue

        start_price = impulse[0].open_price
        impulse_high = max(kline.high_price for kline in impulse)
        impulse_pct = pct_change(start_price, impulse_high)
        if impulse_pct < min_impulse_pct:
            index += 1
            continue

        event = build_event_row(
            symbol=symbol,
            start_kline=impulse[0],
            base=base,
            impulse=impulse,
            impulse_pct=impulse_pct,
        )
        events.append(event)
        index += window_days
    return events


def build_event_row(
    *,
    symbol: str,
    start_kline: DailyKline,
    base: list[DailyKline],
    impulse: list[DailyKline],
    impulse_pct: float,
) -> dict[str, Any]:
    base_open = base[0].open_price
    base_close = base[-1].close_price
    base_high = max(kline.high_price for kline in base)
    base_low = min(kline.low_price for kline in base)
    base_avg_quote_volume = mean(kline.quote_volume for kline in base)
    base_last_quote_volume = base[-1].quote_volume
    impulse_quote_volume = sum(kline.quote_volume for kline in impulse)
    impulse_taker_delta = sum(kline.taker_delta_quote for kline in impulse)
    impulse_taker_quote = sum(kline.quote_volume for kline in impulse)
    pre_taker_delta = sum(kline.taker_delta_quote for kline in base[-3:])
    pre_taker_quote = sum(kline.quote_volume for kline in base[-3:])

    return {
        "symbol": symbol,
        "start_date": time.strftime("%Y-%m-%d", time.gmtime(start_kline.open_time / 1000)),
        "impulse_pct": round(impulse_pct, 4),
        "base_return_pct": round(pct_change(base_open, base_close), 4),
        "base_range_pct": round(pct_change(base_low, base_high), 4),
        "base_avg_quote_volume": round(base_avg_quote_volume, 2),
        "pre_day_volume_ratio": round(safe_ratio(base_last_quote_volume, base_avg_quote_volume), 4),
        "impulse_volume_ratio": round(safe_ratio(impulse_quote_volume / len(impulse), base_avg_quote_volume), 4),
        "pre_3d_taker_delta_pct": round(safe_pct(pre_taker_delta, pre_taker_quote), 4),
        "impulse_taker_delta_pct": round(safe_pct(impulse_taker_delta, impulse_taker_quote), 4),
        "quiet_days_7d": quiet_days(base),
        "price_to_base_high_pct": round(pct_change(base_high, start_kline.open_price), 4),
        "breakout_over_base_high_pct": round(pct_change(base_high, max(kline.high_price for kline in impulse)), 4),
    }


def quiet_days(klines: list[DailyKline], max_abs_return_pct: float = 5) -> int:
    count = 0
    for kline in klines:
        if abs(pct_change(kline.open_price, kline.close_price)) <= max_abs_return_pct:
            count += 1
    return count


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "symbol",
        "start_date",
        "impulse_pct",
        "base_return_pct",
        "base_range_pct",
        "base_avg_quote_volume",
        "pre_day_volume_ratio",
        "impulse_volume_ratio",
        "pre_3d_taker_delta_pct",
        "impulse_taker_delta_pct",
        "quiet_days_7d",
        "price_to_base_high_pct",
        "breakout_over_base_high_pct",
    ]
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: list[dict[str, Any]]) -> str:
    lines = [
        "Impulse research summary",
        f"events={len(rows)}",
        "",
        "Notes:",
        "- taker_delta_pct is a kline proxy: taker buy quote minus taker sell quote divided by total quote volume.",
        "- public Binance OI statistics are not available for a full year through the REST endpoint; use this table for price/volume/taker-flow thresholds and combine it with live OI data.",
        "",
    ]
    if not rows:
        lines.append("No events found.")
        return "\n".join(lines)

    for field in (
        "impulse_pct",
        "base_return_pct",
        "base_range_pct",
        "pre_day_volume_ratio",
        "impulse_volume_ratio",
        "pre_3d_taker_delta_pct",
        "impulse_taker_delta_pct",
        "quiet_days_7d",
        "price_to_base_high_pct",
    ):
        values = [float(row[field]) for row in rows]
        lines.append(
            f"{field}: "
            f"p25={percentile(values, 25):.2f}, "
            f"median={percentile(values, 50):.2f}, "
            f"p75={percentile(values, 75):.2f}, "
            f"avg={statistics.fmean(values):.2f}"
        )
    return "\n".join(lines)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * (pct / 100)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


def pct_change(old: float, new: float) -> float:
    if old == 0:
        return 100.0 if new > 0 else 0.0
    return ((new - old) / abs(old)) * 100


def safe_ratio(value: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return value / base


def safe_pct(value: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return (value / base) * 100


def to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


if __name__ == "__main__":
    main()
