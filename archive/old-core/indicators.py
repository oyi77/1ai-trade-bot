"""
Core indicator utilities — EMA, RSI, and position-scoring.
"""
from __future__ import annotations
from typing import Sequence


def ema(values: Sequence[float], span: int) -> list[float]:
    """Exponential Moving Average (iterative)."""
    k = 2 / (span + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def rsi(closes: Sequence[float], period: int = 14) -> float:
    """Relative Strength Index (last value)."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_g = sum(gains[-period:]) / period if gains else 1
    avg_l = sum(losses[-period:]) / period if losses else 1
    rs = avg_g / max(avg_l, 0.001)
    return 100 - (100 / (1 + rs))


def score_trend(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    mode: str = "binary",
) -> tuple[int, list[str]]:
    """
    Score a market for CALL/PUT bias. Returns (score_0‑100, reasons).
    Score ≥60 → CALL bias, ≤40 → PUT bias.
    
    mode='binary': optimized for short-term binary options (1m-5m expiry).
                   Uses EMA5/10, faster RSI thresholds, candle patterns.
    mode='swing':  original longer-term analysis (EMA9/21/50).
    """
    price = closes[-1]
    reasons = []
    score = 50

    if mode == "binary":
        # ── Binary-optimized indicators ──────────────────────────────
        # Fast EMA crossovers
        e5 = ema(closes, 5)
        e10 = ema(closes, 10)
        
        # RSI
        rsi_val = rsi(closes, 7)  # faster RSI period
        
        # Bollinger-like: recent volatility band
        recent_high = max(highs[-10:])
        recent_low = min(lows[-10:])
        mid = (recent_high + recent_low) / 2
        range_pct = 50 if recent_high == recent_low else (price - recent_low) / (recent_high - recent_low) * 100
        
        # 1. Fast EMA momentum
        if e5[-1] > e10[-1]:
            score += 6; reasons.append("EMA5↑EMA10")
        else:
            score -= 6; reasons.append("EMA5↓EMA10")
        
        # 2. EMA crossover (3 bar lookback)
        if len(e5) >= 4 and len(e10) >= 4:
            if e5[-4] <= e10[-4] and e5[-1] > e10[-1]:
                score += 12; reasons.append("bullX")
            elif e5[-4] >= e10[-4] and e5[-1] < e10[-1]:
                score -= 12; reasons.append("bearX")
        
        # 3. RSI — tighter for binary (oversold/overbought reversal)
        if rsi_val > 78:
            score -= 8; reasons.append(f"RSI{int(rsi_val)} OB")
        elif rsi_val < 22:
            score += 8; reasons.append(f"RSI{int(rsi_val)} OS")
        elif rsi_val > 65:
            score += 5; reasons.append(f"RSI{int(rsi_val)}↑")
        elif rsi_val < 35:
            score -= 5; reasons.append(f"RSI{int(rsi_val)}↓")
        
        # 4. Candle pattern — engulfing / pin bar
        if len(closes) >= 3:
            c1, c2, c3 = closes[-3], closes[-2], closes[-1]
            h1, h2 = highs[-2], highs[-1]
            l1, l2 = lows[-2], lows[-1]
            # Bullish engulfing
            if c2 < c1 and c3 > h2:
                score += 10; reasons.append("bullEngulf")
            # Bearish engulfing
            elif c2 > c1 and c3 < l2:
                score -= 10; reasons.append("bearEngulf")
            # Pin bar (long wick)
            range_c3 = h2 - l2 if h2 > l2 else 0.001
            upper_wick = (h2 - max(c2, c3)) / range_c3
            lower_wick = (min(c2, c3) - l2) / range_c3
            if upper_wick > 0.6:
                score -= 6; reasons.append("pinTop")
            elif lower_wick > 0.6:
                score += 6; reasons.append("pinBot")
        
        # 5. Momentum burst (3-candle surge/plunge)
        if len(closes) >= 3:
            mom3 = (closes[-1] - closes[-3]) / (closes[-3] or 0.001) * 100
            if mom3 > 0.3:
                score += 4; reasons.append(f"mom+{mom3:.1f}%")
            elif mom3 < -0.3:
                score -= 4; reasons.append(f"mom{mom3:.1f}%")
        
        # 6. Bollinger %b (position in recent range)
        if range_pct > 90:
            score -= 5; reasons.append("rangeTop")
        elif range_pct < 10:
            score += 5; reasons.append("rangeBot")
        elif range_pct > 60:
            score += 2; reasons.append("midHi")
        elif range_pct < 40:
            score -= 2; reasons.append("midLo")
    
    else:
        # ── Original swing analysis ──────────────────────────────────
        e9 = ema(closes, 9)
        e21 = ema(closes, 21)
        e50 = ema(closes, 50)
        rsi_val = rsi(closes)

        recent_high = max(highs[-20:])
        recent_low = min(lows[-20:])
        range_pct = 50 if recent_high == recent_low else (price - recent_low) / (recent_high - recent_low) * 100

        # EMA momentum
        if e9[-1] > e21[-1]:
            score += 8; reasons.append("EMA9↑EMA21")
        else:
            score -= 8; reasons.append("EMA9↓EMA21")

        # Trend filter
        if price > e50[-1]:
            score += 7; reasons.append("price↑EMA50")
        else:
            score -= 7; reasons.append("price↓EMA50")

        # Crossover
        if len(e9) >= 3 and len(e21) >= 3:
            if e9[-3] <= e21[-3] and e9[-1] > e21[-1]:
                score += 10; reasons.append("bullishX")
            elif e9[-3] >= e21[-3] and e9[-1] < e21[-1]:
                score -= 10; reasons.append("bearishX")

        # RSI
        if rsi_val > 70:
            score -= 5; reasons.append(f"RSI{int(rsi_val)} O/B")
        elif rsi_val < 30:
            score += 5; reasons.append(f"RSI{int(rsi_val)} O/S")
        elif rsi_val > 55:
            score += 4; reasons.append(f"RSI{int(rsi_val)} bull")
        elif rsi_val < 45:
            score -= 4; reasons.append(f"RSI{int(rsi_val)} bear")
        else:
            reasons.append(f"RSI{int(rsi_val)} flat")

        if range_pct > 85:
            score -= 4; reasons.append("nearH")
        elif range_pct < 15:
            score += 4; reasons.append("nearL")

        recent_delta = closes[-1] - closes[-3] if len(closes) >= 3 else 0
        if recent_delta > 0:
            score += 3; reasons.append("→up")
        elif recent_delta < 0:
            score -= 3; reasons.append("→dn")

    score = max(0, min(100, score))
    return score, reasons


def classify_signal(
    score: int,
    price: float,
    reasons: list[str],
    symbol: str = "",
    source: str = "yahoo",
) -> dict:
    """Convert a raw score to CALL / PUT / WAIT with confidence."""
    from datetime import datetime, timezone
    from core import Signal

    if score >= 60:
        action, confidence = "CALL", score
    elif score <= 40:
        action, confidence = "PUT", 100 - score
    else:
        action, confidence = "WAIT", max(score, 100 - score)

    sig = Signal(
        symbol=symbol,
        action=action,
        confidence=confidence,
        price=price,
        reason="; ".join(reasons[:5]),
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        source=source,
    )
    return sig
