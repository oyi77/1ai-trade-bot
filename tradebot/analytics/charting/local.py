"""Local candlestick chart generator using yfinance + matplotlib.

No external API dependencies. No API keys required.
"""
from __future__ import annotations

import io
import logging
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

import yfinance as yf

logger = logging.getLogger(__name__)


# ── Font ──────────────────────────────────────────────────────────────────


def _get_font(size: int = 12) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ── Symbol resolution ─────────────────────────────────────────────────────

_YFINANCE_FX_MAP: dict[str, str] = {
    "XAUUSD=X": "GC=F",
    "USOIL": "CL=F",
    "OIL": "CL=F",
}

_YFINANCE_CRYPTO_MAP: dict[str, str] = {
    "BTC-USD": "BTC-USD",
    "ETH-USD": "ETH-USD",
}


def _resolve_yf_symbol(symbol: str) -> str:
    """Map Yahoo symbol to one yfinance can fetch OHLCV data for.

    Yahoo FX quotes (XAUUSD=X, EURUSD=X) don't provide intraday OHLCV.
    Use futures (GC=F, CL=F) or direct ETFs instead.
    """
    upper = symbol.upper().strip()
    if upper in _YFINANCE_FX_MAP:
        return _YFINANCE_FX_MAP[upper]
    # Crypto works as-is
    if upper in _YFINANCE_CRYPTO_MAP:
        return upper
    # IDX stocks: BBCA.JK is correct
    if upper.endswith(".JK"):
        return upper
    # US stocks: AAPL, TSLA, etc
    if upper.isalpha() and len(upper) <= 5:
        return upper
    # Anything else that looks like a standard ticker
    if "." not in upper and not upper.endswith("=X"):
        return upper
    # Try stripping =X suffix
    if upper.endswith("=X"):
        bare = upper[:-2]
        if bare.isalpha() and len(bare) <= 6:
            return bare
    return upper


# ── OHLCV fetching ────────────────────────────────────────────────────────


def fetch_ohlcv(symbol: str, interval: str = "15m", count: int = 80) -> list[dict[str, Any]]:
    """Fetch OHLCV data via yfinance, return list of candle dicts."""
    interval_map = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "1H": "1h", "4h": "4h", "4H": "4h",
        "1d": "1d", "1D": "1d",
    }
    mapped = interval_map.get(interval, "15m")
    period_map = {"1m": "1d", "5m": "1d", "15m": "2d", "30m": "5d",
                  "1h": "1mo", "4h": "3mo", "1d": "3mo"}
    period = period_map.get(mapped, "2d")
    yf_sym = _resolve_yf_symbol(symbol)

    try:
        df = yf.download(yf_sym, period=period, interval=mapped, progress=False)
        if df.empty:
            logger.warning("yfinance empty for %s (%s %s)", yf_sym, symbol, interval)
            return []
        if isinstance(df.columns, pd.MultiIndex):
            ticker_col = df.columns.get_level_values(1)[0]
            rows = []
            for idx, row in df.iterrows():
                rows.append({
                    "date": idx,
                    "open": float(row[("Open", ticker_col)]),
                    "high": float(row[("High", ticker_col)]),
                    "low": float(row[("Low", ticker_col)]),
                    "close": float(row[("Close", ticker_col)]),
                    "volume": float(row[("Volume", ticker_col)]),
                })
        else:
            rows = []
            for idx, row in df.iterrows():
                rows.append({
                    "date": idx,
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": float(row["Volume"]),
                })
        return rows[-count:]
    except Exception as e:
        logger.warning("yfinance fetch failed for %s: %s", symbol, e)
        return []


# ── Chart rendering ───────────────────────────────────────────────────────


def _candle_color(op: float, cl: float) -> str:
    return "#22c55e" if cl >= op else "#ef4444"


def render_candlestick_chart(
    ohlcv: list[dict[str, Any]],
    width: int = 800,
    height: int = 600,
    title: str = "",
) -> bytes:
    """Render a candlestick chart to PNG bytes using matplotlib."""
    fig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    dates = [c["date"] for c in ohlcv]
    opens = [c["open"] for c in ohlcv]
    highs = [c["high"] for c in ohlcv]
    lows = [c["low"] for c in ohlcv]
    closes = [c["close"] for c in ohlcv]
    volumes = [c["volume"] for c in ohlcv]

    x = np.arange(len(ohlcv))
    body_width = 0.6

    for i in range(len(ohlcv)):
        color = _candle_color(opens[i], closes[i])
        body_bottom = min(opens[i], closes[i])
        body_top = max(closes[i], opens[i]) - body_bottom
        if body_top <= 0:
            body_top = 0.001
        ax.bar(x[i], body_top, bottom=body_bottom, width=body_width,
               color=color, edgecolor=color, linewidth=0.5, zorder=3)
        ax.plot([x[i], x[i]], [lows[i], highs[i]], color=color, linewidth=0.8, zorder=2)

    # Volume bars at bottom
    if highs and lows:
        price_range = max(highs) - min(lows)
        max_vol = max(volumes) if max(volumes) > 0 else 1
        vol_scale = price_range * 0.15 / max_vol
        vol_bottom = min(lows) - price_range * 0.01
        for i in range(len(ohlcv)):
            color = _candle_color(opens[i], closes[i])
            ax.bar(x[i], volumes[i] * vol_scale, bottom=vol_bottom, width=body_width,
                   color=color, alpha=0.15, zorder=1)

    ax.grid(True, color="#2d2d44", linestyle="-", linewidth=0.5, alpha=0.7)
    ax.tick_params(colors="#888888")
    for spine in ax.spines.values():
        spine.set_color("#2d2d44")

    if len(ohlcv) > 1:
        step = max(1, len(ohlcv) // 6)
        tick_pos = x[::step]
        tick_lbl = [
            dates[i].strftime("%m/%d %H:%M") if hasattr(dates[i], "strftime") else str(dates[i])
            for i in range(0, len(ohlcv), step)
        ]
        ax.set_xticks(tick_pos)
        ax.set_xticklabels(tick_lbl, rotation=30, ha="right", fontsize=8, color="#888888")

    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.tick_params(axis="y", labelsize=8, colors="#888888")

    if title:
        ax.set_title(title, color="#cccccc", fontsize=11, fontweight="bold", pad=8)

    ax.margins(x=0.02, y=0.08)

    buf = io.BytesIO()
    plt.tight_layout(pad=1.0)
    fig.savefig(buf, format="png", dpi=100, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    return buf.getvalue()


# ── Overlay ───────────────────────────────────────────────────────────────


def _price_to_y(price: float, high: float, low: float, top: int, bottom: int) -> int:
    if high == low:
        return (top + bottom) // 2
    ratio = (high - price) / (high - low)
    return int(top + ratio * (bottom - top))


def _draw_line_with_label(draw: ImageDraw.ImageDraw, y: int, label: str,
                          color: tuple[int, int, int], width: int,
                          img_w: int, font: Any) -> None:
    draw.line([(0, y), (img_w - 10, y)], fill=color, width=width)
    bbox = draw.textbbox((0, 0), f" {label} ", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    lx, ly = 4, y - th - 3
    if ly < 2:
        ly = y + 3
    draw.rectangle([lx, ly, lx + tw + 6, ly + th + 4], fill=(0, 0, 0))
    draw.text((lx + 3, ly + 1), f" {label} ", fill=color, font=font)


def overlay_signal_lines(
    chart_png_bytes: bytes,
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    price_high: float = 0.0,
    price_low: float = 0.0,
    trend: str = "BUY",
) -> bytes | None:
    """Draw Entry/SL/TP lines and labels on a chart PNG."""
    try:
        img = Image.open(io.BytesIO(chart_png_bytes)).convert("RGBA")
    except Exception as exc:
        logger.warning("overlay: failed to open image: %s", exc)
        return None

    w, h = img.size
    chart_top = 40
    chart_bottom = h - 10

    if price_high == 0 or price_low == 0:
        prices = [p for p in [entry, sl, tp1, tp2] if p > 0]
        if prices:
            marg = max(prices) * 0.01
            price_high = max(prices) + marg
            price_low = min(prices) - marg
        else:
            return chart_png_bytes

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _get_font(13)

    entry_color = (34, 197, 94) if trend.upper() in ("BUY", "LONG") else (239, 68, 68)
    sl_color = (239, 68, 68)
    tp_color = (34, 197, 94)

    lines = []
    if entry > 0:
        lines.append((entry, f"ENTRY {entry:,.2f}", entry_color, 2))
    if sl > 0:
        lines.append((sl, f"SL {sl:,.2f}", sl_color, 1))
    if tp1 > 0:
        lines.append((tp1, f"TP1 {tp1:,.2f}", tp_color, 1))
    if tp2 > 0:
        lines.append((tp2, f"TP2 {tp2:,.2f}", tp_color, 1))

    for price, label, color, lw in lines:
        y = _price_to_y(price, price_high, price_low, chart_top, chart_bottom)
        y = max(chart_top, min(y, chart_bottom))
        _draw_line_with_label(draw, y, label, color, lw, w, font)

    result = Image.alpha_composite(img, overlay)
    out = io.BytesIO()
    result.convert("RGB").save(out, format="PNG")
    return out.getvalue()


# ── Public API ────────────────────────────────────────────────────────────


async def generate_signal_chart(
    symbol: str,
    timeframe: str = "15m",
    trend: str = "BULLISH",
    entry: float = 0.0,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    width: int = 800,
    height: int = 600,
) -> bytes | None:
    """Generate a candlestick chart via yfinance, overlay signal lines."""
    from tradebot.bots.platforms.vilona.helpers import resolve_yahoo_symbol

    resolved = resolve_yahoo_symbol(symbol)

    ohlcv = fetch_ohlcv(resolved, interval=timeframe)
    if not ohlcv or len(ohlcv) < 5:
        logger.warning("Not enough OHLCV data for %s", resolved)
        return None

    all_prices = [c["high"] for c in ohlcv] + [c["low"] for c in ohlcv]
    for p in (entry, sl, tp1, tp2):
        if p > 0:
            all_prices.append(p)
    price_high = max(all_prices) * 1.01
    price_low = min(all_prices) * 0.99

    chart_bytes = render_candlestick_chart(
        ohlcv, width=width, height=height,
        title=f"{resolved} ({timeframe})",
    )
    if not chart_bytes:
        return None

    final = overlay_signal_lines(
        chart_png_bytes=chart_bytes,
        entry=entry, sl=sl, tp1=tp1, tp2=tp2,
        price_high=price_high, price_low=price_low,
        trend=trend,
    )
    return final or chart_bytes