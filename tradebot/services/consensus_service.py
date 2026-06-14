"""Consensus engine service wrapper.

Provides TTL-based caching so multiple callers requesting the same
symbol within the cache window share a single engine run.

Also provides Quant Consensus UI formatters and Sequoia-X Turtle
screening.
"""

from __future__ import annotations

import logging
from typing import Any
from tradebot.engines.consensus import TF_WEIGHTS as _TF_WEIGHTS
from tradebot.engines.consensus import TIMEFRAMES as _TIMEFRAMES
from tradebot.engines.consensus import MTFConsensus
from tradebot.engines.registry import Registry
from tradebot.storage.cache import TieredCache

# ── Sequoia-X Turtle engine (optional, soft-fail) ──
try:
    import pandas as pd  # type: ignore[import-untyped]

    from strategies.sequoia_math import (  # type: ignore[import-not-found]
        ma_volume_breakout,
        turtle_breakout,
        turtle_signal_strength,
        turtle_trend_filter,
        validate_ohlcv,
    )

    SEQUOIA_ENGINE = True
except Exception:
    SEQUOIA_ENGINE = False

LOG = logging.getLogger(__name__)

_signal_cache = TieredCache(default_ttl=120)


# ═══════════════════════════════════════════════════════════════════
#  ENGINE CONSENSUS
# ═══════════════════════════════════════════════════════════════════


async def run_engine_consensus(
    ohlcv: list[dict] | None = None,
    price: float | None = None,
    symbol: str = "XAUUSD",
) -> dict[str, Any]:
    cache_key = f"signal:{symbol}"
    cached = _signal_cache.get(cache_key)
    if isinstance(cached, dict):
        LOG.debug("Signal cache HIT for %s", cache_key)
        return cached

    LOG.debug("Signal cache MISS for %s — running engine consensus", cache_key)
    
    registry = Registry()
    mtf = MTFConsensus(registry)
    result = await mtf.analyze(symbol=symbol, price=price)
    
    if result:
        _signal_cache.set(cache_key, result)
    return result


def get_tf_weights() -> dict[str, float]:
    return dict(_TF_WEIGHTS)


def get_timeframes() -> list[str]:
    return list(_TIMEFRAMES)


# ═══════════════════════════════════════════════════════════════════
#  QUANT CONSENSUS UI
# ═══════════════════════════════════════════════════════════════════


def append_quant_consensus_ui(
    sig: dict, quant_result: dict | None, disp: str = "XAUUSD"
) -> tuple[str, list[str]]:
    """Inject Quant Consensus block + guardrail into formatted signal text.

    Returns (quant_block: str, guardrail_warnings: list[str]).
    human-readable, plain Indonesian — no G/R/D jargon.
    """
    if not quant_result or quant_result.get("error"):
        return "", []

    match_count = quant_result.get("match_count", 0)
    green_pct = quant_result.get("green_pct", 0)
    red_pct = quant_result.get("red_pct", 0)
    doji_pct = quant_result.get("doji_pct", 0)
    confidence = quant_result.get("confidence_score", 0)
    verdict = quant_result.get("quant_verdict", "?")
    dominant = quant_result.get("dominant_next", "?")
    series_len = quant_result.get("series_length", 0)
    pattern_size = quant_result.get("pattern_size", 5)

    # ── Visual bar: proportional █ blocks (max 20 blocks) ──
    def _bar(pct: float) -> str:
        n = min(20, round(pct / 5))
        return "█" * n + "░" * (20 - n)

    # ── Simple verdict in plain Indonesian ──
    verdict_text = {
        "BUY_BIAS_HISTORICAL": f"📈 <b>Historis cenderung NAIK</b> — {green_pct:.0f}% kejadian serupa lanjut bullish",
        "SELL_BIAS_HISTORICAL": f"📉 <b>Historis cenderung TURUN</b> — {red_pct:.0f}% kejadian serupa lanjut bearish",
        "NEUTRAL_HISTORICAL": "➖ <b>Historis gak jelas arah</b> — terlalu banyak sideways",
        "NO_HISTORICAL_MATCH": "⚠️ <b>Pola ini baru pertama kali</b> — belum ada data pembanding",
        "INSUFFICIENT_DATA": "⏳ <b>Data belum cukup</b> — butuh minimal 15 candle",
    }.get(verdict, "⚪ Data tidak tersedia")

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"📜 <b>Statistik Historis — Pola {pattern_size} Candle</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔍 Ketemu <b>{match_count}x</b> kejadian serupa dari {series_len} bar terakhir\n"
        f"\n"
        f"📈 NAIK   {green_pct:5.0f}%  {_bar(green_pct)}\n"
        f"📉 TURUN  {red_pct:5.0f}%  {_bar(red_pct)}\n"
        f"➖ Datar  {doji_pct:5.0f}%  {_bar(doji_pct)}\n"
        f"\n"
        f"🧠 <b>Kesimpulan:</b> {verdict_text}\n"
        f"   Keyakinan: {confidence:.0%}"
    )

    # Guardrail logic
    warnings: list[str] = []
    ai_action = sig.get("action", "HOLD")
    guard_threshold = 40

    if match_count == 0:
        warnings.append(
            "⚠️ <b>Belum ada data pembanding</b> — sinyal AI murni dari analisa teknikal, bukan statistik"
        )
    elif ai_action == "BUY" and green_pct < guard_threshold:
        warnings.append(
            f"⚠️ <b>Hati-hati:</b> AI bilang BUY tapi statistik cuma {green_pct:.0f}% kejadian yang lanjut naik — riskan!"
        )
    elif ai_action == "SELL" and red_pct < guard_threshold:
        warnings.append(
            f"⚠️ <b>Hati-hati:</b> AI bilang SELL tapi statistik cuma {red_pct:.0f}% kejadian yang lanjut turun — riskan!"
        )

    if ai_action == "BUY" and dominant == "R" and red_pct >= guard_threshold:
        warnings.append(
            f"🚨 <b>KONFLIK:</b> AI BUY vs Data Historis SELL ({red_pct:.0f}% kejadian malah turun!) 🚨"
        )
    elif ai_action == "SELL" and dominant == "G" and green_pct >= guard_threshold:
        warnings.append(
            f"🚨 <b>KONFLIK:</b> AI SELL vs Data Historis BUY ({green_pct:.0f}% kejadian malah naik!) 🚨"
        )

    return block, warnings


# ═══════════════════════════════════════════════════════════════════
#  SEQUOIA-X SCREENING
# ═══════════════════════════════════════════════════════════════════


def run_sequoia_screen(
    ohlcv_bars: list[dict] | None, disp: str = "XAUUSD"
) -> dict | None:
    """Run Sequoia-X quantitative screening on OHLCV bars.

    Runs multiple vectorized strategies:
      - Turtle 20-day breakout (turtle_breakout)
      - Turtle signal strength (0-1 momentum score)
      - MA Volume breakout (price > MA20 + volume spike)
      - Turtle Trend Filter (bull/bear/neutral for D1/H4 macro)

    Returns None on insufficient data or error.
    """
    if not SEQUOIA_ENGINE or not ohlcv_bars or len(ohlcv_bars) < 30:
        return None

    try:
        # Build DataFrame from OHLCV bars
        df_bars: list[dict] = []
        for b in ohlcv_bars:
            o = float(b.get("o", b.get("open", 0)))
            h = float(b.get("h", b.get("high", 0)))
            lv = float(b.get("l", b.get("low", 0)))
            c = float(b.get("c", b.get("close", 0)))
            v = float(b.get("v", b.get("volume", 0)))
            if o <= 0 or c <= 0:
                continue
            df_bars.append({"open": o, "high": h, "low": lv, "close": c, "volume": v})

        if len(df_bars) < 30:
            return None

        df = pd.DataFrame(df_bars)

        if not validate_ohlcv(df):
            return None

        # ── Run Sequoia strategies ──
        result: dict = {"status": "ok", "display": disp}

        # 1) MA Volume Breakout (fastest signal)
        ma_vol_sig = ma_volume_breakout(df, ma_period=20, volume_mult=1.5)
        result["ma_volume_trigger"] = bool(ma_vol_sig.iloc[-1])

        # 2) Turtle 20-day Breakout
        turtle_sig = turtle_breakout(df, lookback=20)
        result["turtle_trigger"] = bool(turtle_sig.iloc[-1])

        # 3) Turtle Signal Strength (continuous 0-1)
        strength = turtle_signal_strength(df, lookback=20)
        result["turtle_strength"] = (
            float(strength.iloc[-1]) if len(strength) > 0 else 0.0
        )

        # 4) Turtle Trend Filter (macro direction)
        is_bull, trend_strength, tf_dir = turtle_trend_filter(
            df, lookback=20, smoothing=3
        )
        result["trend_bullish"] = bool(is_bull.iloc[-1])
        result["trend_strength"] = (
            float(trend_strength.iloc[-1]) if len(trend_strength) > 0 else 0.0
        )
        result["trend_direction"] = tf_dir  # -1, 0, +1

        # Consensus summary
        bullish_score = sum(
            [
                1 if result["turtle_trigger"] else 0,
                1 if result["ma_volume_trigger"] else 0,
                1 if tf_dir > 0 else 0,
            ]
        )
        bearish_score = 1 if tf_dir < 0 else 0

        result["bullish_signals"] = bullish_score
        result["bearish_signals"] = bearish_score

        if bullish_score >= 2:
            result["sequoia_verdict"] = "BUY_BIAS"
        elif bearish_score >= 1 and bullish_score == 0:
            result["sequoia_verdict"] = "SELL_BIAS"
        elif tf_dir != 0:
            result["sequoia_verdict"] = (
                f"TREND_{'BULL' if tf_dir > 0 else 'BEAR'}"
            )
        else:
            result["sequoia_verdict"] = "NEUTRAL"

        return result

    except Exception as e:
        return {"status": "error", "error": str(e)}


def format_sequoia_block(
    result: dict | None, ai_action: str | None = None
) -> tuple[str, list[str]]:
    """Format Sequoia screening results into a compact Telegram block.

    Includes guardrail warnings when Sequoia contradicts AI signal.
    Returns (block_text, warnings_list).
    """
    if not result or result.get("status") != "ok":
        return "", []

    verdict = result.get("sequoia_verdict", "?")
    verdict_emoji = {
        "BUY_BIAS": "🐢🟢",
        "SELL_BIAS": "🐢🔴",
        "TREND_BULL": "📈",
        "TREND_BEAR": "📉",
        "NEUTRAL": "⚪️",
    }.get(verdict, "⚪️")

    t_str = (
        f"{result.get('turtle_strength', 0):.0%}"
        if result.get("turtle_strength")
        else "—"
    )
    tr_str = (
        f"{result.get('trend_strength', 0):.0%}"
        if result.get("trend_strength")
        else "—"
    )

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"🐢 <b>Sequoia-X Quant</b> [Turtle+HTF+MA]\n"
        f"{verdict_emoji} <b>{verdict.replace('_', ' ')}</b> | "
        f"🟢{result.get('bullish_signals', 0)} 🔴{result.get('bearish_signals', 0)}\n"
        f"Turtle BO: {'✅' if result.get('turtle_trigger') else '❌'} | "
        f"MA Vol: {'✅' if result.get('ma_volume_trigger') else '❌'}\n"
        f"Momentum: {t_str} | Trend: {tr_str}"
    )

    # Guardrail warnings
    warnings: list[str] = []
    if ai_action:
        if ai_action == "BUY" and verdict == "SELL_BIAS":
            warnings.append(
                "🐢⚠️ <b>Sequoia Guardrail:</b> AI BUY vs Sequoia SELL — divergence!"
            )
        elif ai_action == "SELL" and verdict == "BUY_BIAS":
            warnings.append(
                "🐢⚠️ <b>Sequoia Guardrail:</b> AI SELL vs Sequoia BUY — divergence!"
            )
        elif ai_action in ("BUY", "SELL") and verdict == "NEUTRAL":
            warnings.append(
                "🐢💤 Sequoia neutral — sinyal AI tanpa konfirmasi kuantitatif"
            )

    return block, warnings


__all__ = [
    "append_quant_consensus_ui",
    "format_sequoia_block",
    "get_timeframes",
    "get_tf_weights",
    "run_engine_consensus",
    "run_sequoia_screen",
]