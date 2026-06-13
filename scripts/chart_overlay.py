"""Pillow overlay for Entry/SL/TP lines on chart-img.com screenshots.

Takes the raw PNG from chart-img.com and draws horizontal lines
with labels for Entry, SL, TP1, TP2 price levels.

Usage:
    from scripts.chart_overlay import overlay_signal_lines
    final_png = overlay_signal_lines(
        chart_png_bytes=chart_img_bytes,
        entry=107500, sl=106800, tp1=108200, tp2=109000,
        price_high=109500, price_low=106000,
        trend="BUY",
    )
"""
from __future__ import annotations

import io
import logging
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

COLORS = {
    "entry_buy": (34, 197, 94),
    "entry_sell": (239, 68, 68),
    "sl": (239, 68, 68),
    "tp": (34, 197, 94),
    "label_bg": (0, 0, 0),
    "label_text": (255, 255, 255),
}

# Chart area heuristics for 800x600 chart-img output
HEADER_PX = 30
RSI_RATIO = 0.22
CHART_RIGHT_MARGIN = 10


def _get_font(size: int):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
    except (OSError, IOError):
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
        except (OSError, IOError):
            return ImageFont.load_default()


def _price_to_y(
    price: float,
    price_high: float,
    price_low: float,
    chart_top: int,
    chart_bottom: int,
) -> int:
    if price_high == price_low:
        return (chart_top + chart_bottom) // 2
    ratio = (price_high - price) / (price_high - price_low)
    return int(chart_top + ratio * (chart_bottom - chart_top))


def _draw_line_with_label(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    color: tuple,
    width: int,
    image_width: int,
    font,
    style: str = "solid",
):
    line_end = image_width - CHART_RIGHT_MARGIN

    if style == "dashed":
        dash_len = 8
        gap_len = 5
        x = 0
        while x < line_end:
            x2 = min(x + dash_len, line_end)
            draw.line([(x, y), (x2, y)], fill=color, width=width)
            x += dash_len + gap_len
    else:
        draw.line([(0, y), (line_end, y)], fill=color, width=width)

    label_text = f" {label} "
    bbox = draw.textbbox((0, 0), label_text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    label_x = 4
    label_y = y - th - 3
    if label_y < 2:
        label_y = y + 3

    draw.rectangle(
        [label_x, label_y, label_x + tw + 6, label_y + th + 4],
        fill=COLORS["label_bg"],
    )
    draw.text((label_x + 3, label_y + 1), label_text, fill=color, font=font)


def overlay_signal_lines(
    chart_png_bytes: bytes,
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    price_high: float = 0.0,
    price_low: float = 0.0,
    trend: str = "BUY",
) -> Optional[bytes]:
    try:
        img = Image.open(io.BytesIO(chart_png_bytes)).convert("RGBA")
    except Exception as exc:
        logger.warning("chart_overlay: failed to open image: %s", exc)
        return None

    w, h = img.size
    chart_bottom = int(h * (1 - RSI_RATIO))
    chart_top = HEADER_PX

    if price_high == 0 or price_low == 0:
        prices = [p for p in [entry, sl, tp1, tp2] if p > 0]
        if prices:
            margin = max(prices) * 0.01
            price_high = max(prices) + margin
            price_low = min(prices) - margin
        else:
            return chart_png_bytes

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _get_font(13)

    trend_upper = trend.upper()
    entry_color = COLORS["entry_buy"] if trend_upper in ("BUY", "LONG") else COLORS["entry_sell"]

    lines_config = []
    if entry > 0:
        lines_config.append((entry, "ENTRY", entry_color, 2, "solid"))
    if sl > 0:
        lines_config.append((sl, "SL", COLORS["sl"], 1, "dashed"))
    if tp1 > 0:
        lines_config.append((tp1, "TP1", COLORS["tp"], 1, "solid"))
    if tp2 > 0:
        lines_config.append((tp2, "TP2", COLORS["tp"], 1, "dotted"))

    for price, label, color, width, style in lines_config:
        y = _price_to_y(price, price_high, price_low, chart_top, chart_bottom)
        y = max(chart_top, min(y, chart_bottom))
        _draw_line_with_label(draw, y, f"{label} {price:,.2f}", color, width, w, font, style)

    result = Image.alpha_composite(img, overlay)
    out = io.BytesIO()
    result.convert("RGB").save(out, format="PNG")
    return out.getvalue()
