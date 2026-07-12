from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import math
import time
from typing import Iterable

from binance_client import BinanceClient
from dump_scanner import DumpSignal
from history import HistoryStore


BACKGROUND = "#0B1117"
PANEL = "#101922"
GRID = "#23303B"
TEXT = "#E7EEF5"
MUTED = "#8797A5"
GREEN = "#22C993"
RED = "#F05261"
YELLOW = "#F7C948"
BLUE = "#4EA1FF"
PURPLE = "#B58CFF"


def render_dump_chart(
    signal: DumpSignal,
    client: BinanceClient,
    history: HistoryStore,
    *,
    lookback_hours: int = 48,
    interval: str = "15m",
    width: int = 1200,
    height: int = 900,
) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    interval_minutes = _interval_minutes(interval)
    limit = min(500, max(48, math.ceil(lookback_hours * 60 / interval_minutes)))
    klines = client.get_klines(signal.symbol, interval=interval, limit=limit)
    if len(klines) < 10:
        raise RuntimeError(f"Not enough klines for {signal.symbol}: {len(klines)}")

    now = int(time.time())
    snapshots = history.get_market_snapshots(
        scanner="dump_binance",
        symbol=f"BINANCE:{signal.symbol}",
        since_ts=now - lookback_hours * 3600,
    )

    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = {
        "title": _font(ImageFont, 28, bold=True),
        "heading": _font(ImageFont, 18, bold=True),
        "body": _font(ImageFont, 15),
        "small": _font(ImageFont, 12),
    }

    left = 76
    right = width - 90
    price_top, price_bottom = 82, 520
    volume_top, volume_bottom = 540, 620
    oi_top, oi_bottom = 650, 742
    cvd_top, cvd_bottom = 770, 862

    mode_title = {
        "LIQUIDATION_FLUSH": "LIQUIDATION FLUSH",
        "SHORT_TREND": "SHORT TREND",
    }.get(signal.mode, signal.mode)
    draw.text((left, 22), f"{signal.symbol}  |  {interval}  |  BINANCE + BYBIT", font=fonts["title"], fill=TEXT)
    mode_color = RED if signal.mode == "LIQUIDATION_FLUSH" else YELLOW
    mode_box = draw.textbbox((0, 0), mode_title, font=fonts["heading"])
    mode_width = mode_box[2] - mode_box[0] + 26
    draw.rounded_rectangle((right - mode_width, 20, right, 55), radius=5, fill=mode_color)
    draw.text((right - mode_width + 13, 28), mode_title, font=fonts["heading"], fill=BACKGROUND)

    for top, bottom in ((price_top, price_bottom), (volume_top, volume_bottom), (oi_top, oi_bottom), (cvd_top, cvd_bottom)):
        draw.rectangle((left, top, right, bottom), fill=PANEL)

    recent = klines[-12:]
    entry = signal.price
    invalidation = max(entry * 1.01, max(kline.high_price for kline in recent))
    risk = max(entry * 0.002, invalidation - entry)
    target_1 = max(entry * 0.05, entry - risk)
    target_2 = max(entry * 0.03, entry - 2 * risk)

    price_values = [value for kline in klines for value in (kline.low_price, kline.high_price)]
    price_values.extend([entry, invalidation, target_1, target_2, signal.high_price])
    price_min = min(value for value in price_values if value > 0)
    price_max = max(price_values)
    padding = max((price_max - price_min) * 0.06, price_max * 0.002)
    price_min = max(0, price_min - padding)
    price_max += padding

    def x_for_index(index: int) -> float:
        return left + (index + 0.5) * (right - left) / len(klines)

    def y_for_price(value: float) -> float:
        return _scale(value, price_min, price_max, price_bottom, price_top)

    _draw_grid(draw, left, right, price_top, price_bottom, rows=5)
    candle_slot = (right - left) / len(klines)
    candle_width = max(2, min(7, int(candle_slot * 0.62)))
    for index, kline in enumerate(klines):
        x = x_for_index(index)
        color = GREEN if kline.close_price >= kline.open_price else RED
        draw.line((x, y_for_price(kline.low_price), x, y_for_price(kline.high_price)), fill=color, width=1)
        body_top = y_for_price(max(kline.open_price, kline.close_price))
        body_bottom = y_for_price(min(kline.open_price, kline.close_price))
        if body_bottom - body_top < 1:
            body_bottom = body_top + 1
        draw.rectangle(
            (x - candle_width / 2, body_top, x + candle_width / 2, body_bottom),
            fill=color,
        )

    _draw_price_axis(draw, fonts["small"], right, price_top, price_bottom, price_min, price_max)
    _draw_level(draw, fonts["small"], left, right, y_for_price(signal.high_price), "PUMP HIGH", signal.high_price, PURPLE)
    _draw_level(draw, fonts["small"], left, right, y_for_price(invalidation), "INVALIDATION", invalidation, RED)
    _draw_level(draw, fonts["small"], left, right, y_for_price(entry), "SIGNAL", entry, YELLOW, width=2)
    _draw_level(draw, fonts["small"], left, right, y_for_price(target_1), "TARGET 1R", target_1, GREEN)
    _draw_level(draw, fonts["small"], left, right, y_for_price(target_2), "TARGET 2R", target_2, BLUE)

    signal_x = x_for_index(len(klines) - 1)
    signal_y = y_for_price(entry)
    draw.ellipse((signal_x - 7, signal_y - 7, signal_x + 7, signal_y + 7), fill=YELLOW, outline=BACKGROUND, width=2)

    max_volume = max(kline.turnover for kline in klines) or 1
    draw.text((left + 8, volume_top + 5), "VOLUME", font=fonts["small"], fill=MUTED)
    for index, kline in enumerate(klines):
        x = x_for_index(index)
        bar_height = (kline.turnover / max_volume) * (volume_bottom - volume_top - 20)
        color = GREEN if kline.close_price >= kline.open_price else RED
        draw.rectangle((x - candle_width / 2, volume_bottom - bar_height, x + candle_width / 2, volume_bottom), fill=color)

    first_ms = klines[0].start_ms
    last_ms = klines[-1].start_ms + interval_minutes * 60_000
    _draw_time_axis(draw, fonts["small"], klines, left, right, volume_bottom)
    oi_points = [(row[0] * 1000, row[2]) for row in snapshots if row[2] > 0]
    cvd_points = [(row[0] * 1000, row[3]) for row in snapshots]
    _draw_series_panel(
        draw,
        fonts,
        label=f"OPEN INTEREST   window {signal.oi_change_pct:+.2f}%",
        points=oi_points,
        left=left,
        right=right,
        top=oi_top,
        bottom=oi_bottom,
        first_ms=first_ms,
        last_ms=last_ms,
        color=BLUE,
    )
    _draw_series_panel(
        draw,
        fonts,
        label=f"FUTURES CVD   window {signal.cvd_delta_usdt:,.0f} USDT",
        points=cvd_points,
        left=left,
        right=right,
        top=cvd_top,
        bottom=cvd_bottom,
        first_ms=first_ms,
        last_ms=last_ms,
        color=PURPLE,
        zero_line=True,
    )

    details = (
        f"Score {signal.signal_score}/10    "
        f"Drawdown {signal.drawdown_from_high_pct:+.2f}%    "
        f"Price {signal.price_change_window_pct:+.2f}%    "
        f"Bybit {signal.confirmation_price_change_pct:+.2f}%"
    )
    draw.text((left, height - 28), details, font=fonts["body"], fill=TEXT)
    note = "Scenario levels are analytical markers, not automatic orders"
    note_box = draw.textbbox((0, 0), note, font=fonts["small"])
    draw.text((right - (note_box[2] - note_box[0]), height - 25), note, font=fonts["small"], fill=MUTED)

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _font(image_font, size: int, *, bold: bool = False):
    names = ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"] if bold else ["DejaVuSans.ttf"]
    for name in names:
        try:
            return image_font.truetype(name, size=size)
        except OSError:
            continue
    try:
        return image_font.load_default(size=size)
    except TypeError:
        return image_font.load_default()


def _interval_minutes(interval: str) -> int:
    normalized = interval.strip().lower()
    if normalized.endswith("m"):
        return max(1, int(normalized[:-1]))
    if normalized.endswith("h"):
        return max(1, int(normalized[:-1]) * 60)
    if normalized.endswith("d"):
        return max(1, int(normalized[:-1]) * 1440)
    raise ValueError(f"Unsupported chart interval: {interval}")


def _scale(value: float, minimum: float, maximum: float, out_min: float, out_max: float) -> float:
    if maximum == minimum:
        return (out_min + out_max) / 2
    ratio = (value - minimum) / (maximum - minimum)
    return out_min + ratio * (out_max - out_min)


def _draw_grid(draw, left: int, right: int, top: int, bottom: int, *, rows: int) -> None:
    for row in range(rows + 1):
        y = top + row * (bottom - top) / rows
        draw.line((left, y, right, y), fill=GRID, width=1)
    for column in range(5):
        x = left + column * (right - left) / 4
        draw.line((x, top, x, bottom), fill=GRID, width=1)


def _draw_price_axis(draw, font, right: int, top: int, bottom: int, minimum: float, maximum: float) -> None:
    for row in range(6):
        ratio = row / 5
        y = top + ratio * (bottom - top)
        value = maximum - ratio * (maximum - minimum)
        draw.text((right + 8, y - 7), _price_text(value), font=font, fill=MUTED)


def _draw_level(draw, font, left: int, right: int, y: float, label: str, value: float, color: str, *, width: int = 1) -> None:
    draw.line((left, y, right, y), fill=color, width=width)
    text = f"{label}  {_price_text(value)}"
    box = draw.textbbox((0, 0), text, font=font)
    text_width = box[2] - box[0]
    draw.rectangle((left + 7, y - 10, left + text_width + 19, y + 9), fill=BACKGROUND)
    draw.text((left + 13, y - 8), text, font=font, fill=color)


def _draw_time_axis(draw, font, klines, left: int, right: int, y: int) -> None:
    indexes = sorted({0, len(klines) // 4, len(klines) // 2, (len(klines) * 3) // 4, len(klines) - 1})
    for index in indexes:
        x = left + (index + 0.5) * (right - left) / len(klines)
        label = datetime.fromtimestamp(klines[index].start_ms / 1000, timezone.utc).strftime("%d %b %H:%M")
        box = draw.textbbox((0, 0), label, font=font)
        draw.text((x - (box[2] - box[0]) / 2, y + 4), label, font=font, fill=MUTED)


def _draw_series_panel(
    draw,
    fonts,
    *,
    label: str,
    points: Iterable[tuple[int, float]],
    left: int,
    right: int,
    top: int,
    bottom: int,
    first_ms: int,
    last_ms: int,
    color: str,
    zero_line: bool = False,
) -> None:
    points = [(ts, value) for ts, value in points if first_ms <= ts <= last_ms]
    draw.text((left + 8, top + 5), label, font=fonts["small"], fill=MUTED)
    if len(points) < 2:
        draw.text((left + 8, top + 34), "Collecting history...", font=fonts["body"], fill=MUTED)
        return

    values = [value for _, value in points]
    minimum = min(values)
    maximum = max(values)
    padding = max((maximum - minimum) * 0.08, abs(maximum) * 0.002, 1e-9)
    minimum -= padding
    maximum += padding
    if zero_line and minimum <= 0 <= maximum:
        zero_y = _scale(0, minimum, maximum, bottom - 8, top + 22)
        draw.line((left, zero_y, right, zero_y), fill=GRID, width=1)

    line_points = []
    for ts, value in points:
        x = _scale(ts, first_ms, last_ms, left, right)
        y = _scale(value, minimum, maximum, bottom - 8, top + 22)
        line_points.append((x, y))
    draw.line(line_points, fill=color, width=3)
    last_x, last_y = line_points[-1]
    draw.ellipse((last_x - 4, last_y - 4, last_x + 4, last_y + 4), fill=color)


def _price_text(value: float) -> str:
    if value >= 1000:
        return f"{value:,.1f}"
    if value >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")
