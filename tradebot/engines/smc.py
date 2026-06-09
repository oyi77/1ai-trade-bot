"""
smc.py — SMC Scalper Engine (Smart Money Concepts)

Migrated from: scripts/smc_scalper_engine.py
Conforms to: tradebot.engines.base.Engine interface
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)

# ── Helpers ─────────────────────────────────────────────────────────


def _pip_size(symbol: str = "XAUUSD") -> float:
    """Return pip value for the symbol."""
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"):
        return 0.1
    if s in ("BTCUSD", "BTC"):
        return 1.0
    if s.endswith("JPY"):
        return 0.01
    if s in ("USOIL", "OIL", "CL"):
        return 0.01
    return 0.0001


def _to_pips(price_diff: float, symbol: str = "XAUUSD") -> float:
    ps = _pip_size(symbol)
    return abs(price_diff) / ps if ps > 0 else 0.0


def _ema(series: list[float], period: int) -> list[float]:
    if len(series) < period:
        return [0.0] * len(series)
    multiplier = 2.0 / (period + 1)
    ema = [sum(series[:period]) / period]
    for p in series[period:]:
        ema.append((p - ema[-1]) * multiplier + ema[-1])
    return [0.0] * (period - 1) + ema


def _sma(series: list[float], period: int) -> list[float]:
    if len(series) < period:
        return [0.0] * len(series)
    result: list[float] = [0.0] * (period - 1)
    for i in range(period - 1, len(series)):
        result.append(sum(series[i - period + 1: i + 1]) / period)
    return result


def _atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float]:
    if len(closes) < period + 1:
        return [0.0] * len(closes)
    tr: list[float] = [0.0]
    for i in range(1, len(closes)):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    atr_vals: list[float] = [0.0] * (period - 1)
    atr_vals.append(sum(tr[1: period + 1]) / period)
    for i in range(period, len(tr)):
        atr_vals.append((atr_vals[-1] * (period - 1) + tr[i]) / period)
    return atr_vals


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals: list[float] = [0.0] * period
    rsi_vals.append(
        100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0
    )
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_vals.append(
            100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0
        )
    return rsi_vals


# ── Signal Grade (local helpers) ────────────────────────────────────


class Grade:
    """Signal quality grading — Bahasa Indonesia style."""
    SANGAT_KUAT = 5
    KUAT = 4
    BAGUS = 3
    CUKUP = 2
    LEMAH = 1

    @classmethod
    def from_score_value(cls, score: int, max_score: int = 12) -> int:
        return cls.value_from_score(score, max_score)

    @classmethod
    def value_from_score(cls, score: int, max_score: int = 12) -> int:
        pct = score / max_score if max_score > 0 else 0
        if pct >= 0.90:
            return cls.SANGAT_KUAT
        if pct >= 0.70:
            return cls.KUAT
        if pct >= 0.50:
            return cls.BAGUS
        if pct >= 0.30:
            return cls.CUKUP
        return cls.LEMAH

    @classmethod
    def emoji(cls, grade: int) -> str:
        return {5: "⭐", 4: "🟢", 3: "🔵", 2: "🟡", 1: "⚪"}.get(grade, "⚪")

    @classmethod
    def label(cls, grade: int) -> str:
        return {
            5: "SANGAT KUAT ⭐",
            4: "KUAT 🟢",
            3: "BAGUS 🔵",
        }.get(grade, "LEMAH ⚪")

    @classmethod
    def from_score(cls, score: int, max_score: int = 24) -> int:
        """Grade from score using UltimateResult's 24-point thresholds (85/65/45/25%)."""
        pct = score / max_score if max_score > 0 else 0
        if pct >= 0.85:
            return cls.SANGAT_KUAT
        if pct >= 0.65:
            return cls.KUAT
        if pct >= 0.45:
            return cls.BAGUS
        if pct >= 0.25:
            return cls.CUKUP
        return cls.LEMAH


# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class SMCConfirmation:
    """14-factor SMC confirmation."""
    choch_detected: bool = False
    bos_detected: bool = False
    idm_detected: bool = False
    false_break_warning: bool = False
    valid_pullback: bool = False
    fvg_detected: bool = False
    order_block_valid: bool = False
    sd_zone_aligned: bool = False
    trend_aligned: bool = False
    price_in_zone: bool = False
    session_optimal: bool = False
    volatility_normal: bool = False
    momentum_ok: bool = False
    trend_strength_ok: bool = False

    @property
    def score(self) -> int:
        return sum([
            int(self.choch_detected) * 2,
            int(self.bos_detected) * 2,
            int(self.idm_detected) * 2,
            int(self.fvg_detected) * 2,
            int(self.trend_aligned) * 2,
            int(self.order_block_valid) * 1,
            int(self.sd_zone_aligned) * 1,
            int(self.valid_pullback) * 1,
            int(self.price_in_zone) * 1,
            int(self.session_optimal) * 1,
            int(self.volatility_normal) * 1,
            int(self.momentum_ok) * 1,
            int(self.trend_strength_ok) * 1,
            int(not self.false_break_warning) * 2,
        ])

    @property
    def grade_value(self) -> int:
        return Grade.value_from_score(self.score, 18)


# ── Detection Functions ────────────────────────────────────────────


def _build_bars(ticks: list[Tick]) -> list[dict]:
    """Convert ticks to OHLCV-like bar dicts with close|high|low|open keys."""
    bars: list[dict] = []
    if not ticks:
        return bars
    # Group into simple bars (every N ticks or use price directly)
    for t in ticks:
        bars.append({
            "close": t.price,
            "high": t.price,
            "low": t.price,
            "open": t.price,
        })
    return bars


def detect_choch(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """Detect Change of Character (CHoCH) — market structure reversal."""
    if len(ohlcv) < lookback:
        return None
    bars = ohlcv[-lookback:]
    swing_lookback = 5

    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(swing_lookback, len(bars) - swing_lookback):
        h = float(bars[i].get("high", bars[i].get("h", 0)))
        low = float(bars[i].get("low", bars[i].get("l", 0)))
        is_swing_high = all(
            float(bars[j].get("high", bars[j].get("h", 0))) <= h
            for j in range(i - swing_lookback, i + swing_lookback + 1)
            if j != i
        )
        if is_swing_high:
            swing_highs.append((i, h))
        is_swing_low = all(
            float(bars[j].get("low", bars[j].get("l", 0))) >= low
            for j in range(i - swing_lookback, i + swing_lookback + 1)
            if j != i
        )
        if is_swing_low:
            swing_lows.append((i, low))

    if len(swing_lows) < 3 or len(swing_highs) < 2:
        return None

    last_close = float(bars[-1].get("close", bars[-1].get("c", 0)))

    # Bullish CHoCH
    for i in range(len(swing_lows) - 1):
        if swing_lows[i + 1][1] < swing_lows[i][1]:
            mid_highs = [
                h for h in swing_highs if swing_lows[i][0] < h[0] < swing_lows[i + 1][0]
            ]
            if mid_highs:
                last_high = max(mid_highs, key=lambda x: x[1])
                if last_close > last_high[1]:
                    return {"direction": "BUY", "price": last_high[1], "index": len(bars) - 1}

    # Bearish CHoCH
    for i in range(len(swing_highs) - 1):
        if swing_highs[i + 1][1] > swing_highs[i][1]:
            mid_lows = [
                low for low in swing_lows if swing_highs[i][0] < low[0] < swing_highs[i + 1][0]
            ]
            if mid_lows:
                last_low = min(mid_lows, key=lambda x: x[1])
                if last_close < last_low[1]:
                    return {"direction": "SELL", "price": last_low[1], "index": len(bars) - 1}

    return None


def detect_bos(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """Detect Break of Structure (BOS) — trend continuation."""
    if len(ohlcv) < lookback:
        return None
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    last_close = closes[-1]
    last_idx = len(bars) - 1

    swing_lookback = 5
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(swing_lookback, len(bars) - swing_lookback):
        h = highs[i]
        low = lows[i]
        if all(h >= highs[j] for j in range(i - swing_lookback, i + swing_lookback + 1) if j != i):
            swing_highs.append((i, h))
        if all(low <= lows[j] for j in range(i - swing_lookback, i + swing_lookback + 1) if j != i):
            swing_lows.append((i, low))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None

    all_highs = [(idx, h) for idx, h in swing_highs if idx < last_idx - 3]
    if all_highs:
        max_sh = max(all_highs, key=lambda x: x[1])
        if last_close > max_sh[1]:
            return {"direction": "BUY", "price": max_sh[1], "index": last_idx, "type": "BOS"}

    all_lows = [(idx, low) for idx, low in swing_lows if idx < last_idx - 3]
    if all_lows:
        min_sl = min(all_lows, key=lambda x: x[1])
        if last_close < min_sl[1]:
            return {"direction": "SELL", "price": min_sl[1], "index": last_idx, "type": "BOS"}

    return None


def detect_idm(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """Detect Inducement (IDM) — last valid pullback extreme before BOS."""
    if len(ohlcv) < lookback:
        return None
    bars = ohlcv[-lookback:]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]

    bos = detect_bos(ohlcv, lookback)
    if not bos:
        return None

    bos_idx = bos["index"]
    direction = bos["direction"]

    if direction == "BUY":
        for i in range(bos_idx - 1, max(bos_idx - 30, 0), -1):
            if i >= 3 and i < bos_idx - 2:  # noqa: SIM102
                if lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]:
                    return {"direction": "BUY", "price": lows[i], "index": i,
                            "type": "IDM", "description": "HL — last pullback in bullish trend"}
    else:
        for i in range(bos_idx - 1, max(bos_idx - 30, 0), -1):
            if i >= 3 and i < bos_idx - 2:  # noqa: SIM102
                if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]:
                    return {"direction": "SELL", "price": highs[i], "index": i,
                            "type": "IDM", "description": "LH — last pullback in bearish trend"}
    return None


def detect_false_break(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """Detect False Market Structure Break (liquidity grab / stop hunt)."""
    if len(ohlcv) < lookback:
        return None
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]

    for i in range(len(bars) - 5, len(bars) - 1):
        wick_high = highs[i]
        wick_low = lows[i]
        wick_range = wick_high - wick_low
        body_range = abs(closes[i] - closes[i - 1]) if i > 0 else 0
        if wick_range > body_range * 2.5:
            if closes[i] > closes[i - 1] if i > 0 else False:
                if wick_low < min(closes[i], closes[i - 1]) - (wick_range * 0.3):
                    return {"detected": True, "pattern": "Swept CHoCH Bearish Trap",
                            "direction_faked": "SELL", "real_direction": "BUY",
                            "wick_at": wick_low, "body_close": closes[i]}
            else:
                if wick_high > max(closes[i], closes[i - 1]) + (wick_range * 0.3):
                    return {"detected": True, "pattern": "Swept CHoCH Bullish Trap",
                            "direction_faked": "BUY", "real_direction": "SELL",
                            "wick_at": wick_high, "body_close": closes[i]}
    return None


def detect_valid_pullback(ohlcv: list[dict], lookback: int = 30) -> dict | None:
    """Detect Valid Pullback per World Class SMC rules."""
    if len(ohlcv) < lookback:
        return None
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]

    for i in range(len(bars) - 3, lookback // 2, -1):
        candle_range = highs[i] - lows[i]
        if candle_range <= 0:
            continue
        if i > 0:
            prev_high = max(highs[max(0, i - 3): i])
            prev_low = min(lows[max(0, i - 3): i])
            if closes[i] > closes[i - 1] and highs[i] > prev_high:
                for j in range(i + 1, min(i + 10, len(bars))):
                    if lows[j] < lows[i]:
                        return {"valid": True, "direction": "BUY",
                                "liquidity_removed": True, "impulse_index": i,
                                "pullback_index": j}
            if closes[i] < closes[i - 1] and lows[i] < prev_low:
                for j in range(i + 1, min(i + 10, len(bars))):
                    if highs[j] > highs[i]:
                        return {"valid": True, "direction": "SELL",
                                "liquidity_removed": True, "impulse_index": i,
                                "pullback_index": j}
    return None


def detect_supply_demand_zones(
    ohlcv: list[dict], lookback: int = 100, min_strength: float = 2.0
) -> list[dict]:
    """Detect Supply and Demand zones based on price action."""
    if len(ohlcv) < lookback:
        return []
    bars = ohlcv[-lookback:]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    zones: list[dict] = []
    zone_lookback = 10

    for i in range(zone_lookback, len(bars) - zone_lookback):
        base_high = max(highs[i - zone_lookback: i + 1])
        base_low = min(lows[i - zone_lookback: i + 1])
        base_range = base_high - base_low
        if base_range <= 0:
            continue
        future_bars = min(30, len(bars) - i - 1)
        if future_bars < 5:
            continue
        future_high = max(highs[i + 1: i + 1 + future_bars])
        future_low = min(lows[i + 1: i + 1 + future_bars])
        rise_magnitude = future_high - base_high
        drop_from_base = base_low - future_low
        if rise_magnitude > base_range * min_strength:
            strength = rise_magnitude / base_range if base_range > 0 else 1
            zones.append({
                "type": "DEMAND", "upper": base_high, "lower": base_low,
                "strength": round(strength, 1), "age": len(bars) - i,
                "tested": 0, "mid": (base_high + base_low) / 2,
            })
        if drop_from_base > base_range * min_strength:
            strength = drop_from_base / base_range if base_range > 0 else 1
            zones.append({
                "type": "SUPPLY", "upper": base_high, "lower": base_low,
                "strength": round(strength, 1), "age": len(bars) - i,
                "tested": 0, "mid": (base_high + base_low) / 2,
            })

    zones.sort(key=lambda z: z["strength"], reverse=True)
    unique: list[dict] = []
    for z in zones:
        overlap = False
        for u in unique:
            if z["type"] == u["type"] and abs(z["mid"] - u["mid"]) < (z["upper"] - z["lower"]) * 0.5:  # noqa: E501
                overlap = True
                break
        if not overlap:
            unique.append(z)
    return unique[:5]


def detect_fvg_zones(ohlcv: list[dict], min_pips: float = 5.0, lookback: int = 20) -> dict | None:
    """Detect Fair Value Gap (FVG) — price imbalance."""
    if len(ohlcv) < lookback + 3:
        return None
    bars = ohlcv[-(lookback + 3):]
    min_gap = min_pips * 0.1 * 10
    best_fvg = None
    best_score = 0.0

    for i in range(len(bars) - 2):
        b0_high = float(bars[i].get("high", bars[i].get("h", 0)))
        b0_low = float(bars[i].get("low", bars[i].get("l", 0)))
        b2_high = float(bars[i + 2].get("high", bars[i + 2].get("h", 0)))
        b2_low = float(bars[i + 2].get("low", bars[i + 2].get("l", 0)))

        gap_up = b0_low - b2_high
        if gap_up >= min_gap:
            score = gap_up / min_gap
            if score > best_score:
                mid = (b0_low + b2_high) / 2
                best_fvg = {"direction": "BUY", "upper": b0_low, "lower": b2_high, "mid": mid}
                best_score = score

        gap_down = b2_low - b0_high
        if gap_down >= min_gap:
            score = gap_down / min_gap
            if score > best_score:
                mid = (b2_low + b0_high) / 2
                best_fvg = {"direction": "SELL", "upper": b2_low, "lower": b0_high, "mid": mid}
                best_score = score

    return best_fvg


def detect_order_block(ohlcv: list[dict], lookback: int = 30) -> dict | None:
    """Detect Order Block — institutional supply/demand zone."""
    if len(ohlcv) < lookback:
        return None
    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    opens = [float(b.get("open", b.get("o", 0))) for b in ohlcv]
    highs = [float(b.get("high", b.get("h", 0))) for b in ohlcv]
    lows = [float(b.get("low", b.get("l", 0))) for b in ohlcv]
    atr_vals = _atr(highs, lows, closes, 14)
    bars = ohlcv[-lookback:]
    b_closes = closes[-lookback:]
    b_opens = opens[-lookback:]
    b_atr = atr_vals[-lookback:]
    best_ob = None
    best_strength = 0

    for i in range(1, len(bars)):
        body = abs(b_closes[i] - b_opens[i])
        atr_val = b_atr[i] if b_atr[i] > 0 else body or 1.0
        if b_closes[i - 1] < b_opens[i - 1] and b_closes[i] > b_opens[i]:
            displacement = b_closes[i] - lows[i - 1]
            if displacement > atr_val * 0.5:
                strength = min(5, 1 + int(body / atr_val) + int(displacement / atr_val))
                if strength > best_strength:
                    best_ob = {"direction": "BUY", "upper": b_opens[i - 1],
                               "lower": lows[i - 1], "strength": strength}
                    best_strength = strength
        elif b_closes[i - 1] > b_opens[i - 1] and b_closes[i] < b_opens[i]:
            displacement = highs[i - 1] - b_closes[i]
            if displacement > atr_val * 0.5:
                strength = min(5, 1 + int(body / atr_val) + int(displacement / atr_val))
                if strength > best_strength:
                    best_ob = {"direction": "SELL", "upper": highs[i - 1],
                               "lower": b_closes[i - 1], "strength": strength}
                    best_strength = strength
    return best_ob


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    """Convert a list of Ticks to a list of OHLCV bar dicts."""
    if not ticks:
        return []
    bars: list[dict] = []
    # Simple: each tick as a bar (for engines that work with tick-level data)
    for t in ticks:
        bars.append({
            "open": t.price,
            "high": t.price,
            "low": t.price,
            "close": t.price,
            "volume": 1,
            "timestamp": t.epoch,
        })
    return bars
# ── Advanced Functions (merged from ultimate_smc_engine.py) ─────────
FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_EXTENSIONS = [1.272, 1.414, 1.618, 2.0, 2.618]
def detect_swing_points(ohlcv: list[dict], lookback: int = 100) -> dict:
    """Detect swing high and low using local extrema."""
    if len(ohlcv) < lookback:
        return {"swing_high": None, "swing_low": None}
    bars = ohlcv[-lookback:]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    order = 5
    swing_high_idx, swing_high_val = 0, highs[0]
    swing_low_idx, swing_low_val = 0, lows[0]
    for i in range(order, len(bars) - order):
        if all(highs[i] >= highs[j] for j in range(i - order, i + order + 1) if j != i):  # noqa: SIM102
            if highs[i] > swing_high_val:
                swing_high_val = highs[i]
                swing_high_idx = i
        if all(lows[i] <= lows[j] for j in range(i - order, i + order + 1) if j != i):  # noqa: SIM102
            if lows[i] < swing_low_val:
                swing_low_val = lows[i]
                swing_low_idx = i
    return {"swing_high": swing_high_val, "swing_low": swing_low_val,
            "swing_high_idx": swing_high_idx, "swing_low_idx": swing_low_idx}
def calc_fib_levels(swing_high: float, swing_low: float, direction: str = "DOWN") -> dict:
    """Calculate Fibonacci retracement levels — 7 standard ratios + extensions."""
    diff = swing_high - swing_low
    levels: dict[str, float] = {}
    for ratio in FIB_RATIOS:
        if direction == "DOWN":  # noqa: SIM108
            level = swing_high - diff * ratio
        else:
            level = swing_low + diff * ratio
        levels[f"{ratio:.3f}"] = round(level, 2)
    for ext in FIB_EXTENSIONS:
        if direction == "DOWN":  # noqa: SIM108
            level = swing_high - diff * ext
        else:
            level = swing_low + diff * ext
        levels[f"ext_{ext:.3f}"] = round(level, 2)
    return levels
def find_fib_confluence(price: float, fib_levels: dict, sd_zones: list[dict] | None = None,
                        tolerance_pct: float = 0.3) -> dict:
    """Find confluence between Fibonacci levels and Supply/Demand zones."""
    confluence: dict = {"matched": [], "strength": 0}
    for label, level in fib_levels.items():
        dist_pct = abs(price - level) / price * 100 if price > 0 else 0
        if dist_pct <= tolerance_pct:
            confluence["matched"].append({"fib_label": label, "fib_level": level,
                                          "distance_pct": dist_pct})
            confluence["strength"] += 1
    if sd_zones:
        for z in sd_zones:
            for match in confluence["matched"]:
                if abs(match["fib_level"] - z.get("mid", z.get("price", 0))) / price * 100 < tolerance_pct:  # noqa: E501
                    match["sd_confluence"] = True
                    confluence["strength"] += 1
    return confluence
def score_consolidation_strength(bars: list[dict], zone_high: float, zone_low: float) -> float:
    """Score zone tightness — tighter consolidation = stronger zone (40% weight)."""
    if not bars:
        return 0.0
    zone_range = zone_high - zone_low
    if zone_range <= 0:
        return 0.0
    avg_range = sum(float(b.get("high", b.get("h", 0))) - float(b.get("low", b.get("l", 0))) for b in bars) / len(bars)  # noqa: E501
    tightness = 1.0 - min(zone_range / avg_range, 1.0) if avg_range > 0 else 0.5
    return round(tightness, 2)
def score_wick_strength(bars: list[dict], zone_type: str = "DEMAND") -> float:
    """Score rejection wick proportion — long wicks = strong rejection (25% weight)."""
    if not bars:
        return 0.0
    scores: list[float] = []
    for b in bars[-5:]:
        high = float(b.get("high", b.get("h", 0)))
        low = float(b.get("low", b.get("l", 0)))
        total_range = high - low
        if total_range <= 0:
            scores.append(0.0)
            continue
        if zone_type == "DEMAND":
            wick = float(b.get("open", b.get("o", 0))) - low
        else:
            wick = high - float(b.get("open", b.get("o", 0)))
        wick_ratio = wick / total_range
        scores.append(min(wick_ratio * 2.5, 1.0))
    return round(sum(scores) / len(scores), 2) if scores else 0.0
def score_fvg_strength(bars: list[dict]) -> float:
    """Score Fair Value Gap — non-overlapping candles = stronger imbalance (10% weight)."""
    if len(bars) < 3:
        return 0.0
    gaps = 0
    for i in range(len(bars) - 2):
        b0_low = float(bars[i].get("low", bars[i].get("l", 0)))
        b0_high = float(bars[i].get("high", bars[i].get("h", 0)))
        b2_high = float(bars[i + 2].get("high", bars[i + 2].get("h", 0)))
        b2_low = float(bars[i + 2].get("low", bars[i + 2].get("l", 0)))
        if b0_low > b2_high or b0_high < b2_low:
            gaps += 1
    return min(gaps / max(len(bars) - 2, 1) * 3, 1.0)
def score_body_strength(bars: list[dict], direction: str = "BUY") -> float:
    """Score candle body size + directional alignment (25% weight)."""
    if not bars:
        return 0.0
    scores: list[float] = []
    for b in bars[-10:]:
        o = float(b.get("open", b.get("o", 0)))
        c = float(b.get("close", b.get("c", 0)))
        h = float(b.get("high", b.get("h", 0)))
        l = float(b.get("low", b.get("l", 0)))  # noqa: E741
        total_range = h - l
        if total_range <= 0:
            scores.append(0.0)
            continue
        body = abs(c - o)
        body_ratio = body / total_range
        direction_ok = (direction == "BUY" and c > o) or (direction == "SELL" and c < o)
        score = body_ratio * (1.0 if direction_ok else 0.3)
        scores.append(min(score, 1.0))
    return round(sum(scores) / len(scores), 2) if scores else 0.0
def score_volume_strength(bars: list[dict]) -> float:
    """Score volume delta — higher relative volume = stronger zone."""
    if not bars or ("volume" not in bars[0] and "v" not in bars[0]):
        return 0.5
    volumes = [float(b.get("volume", b.get("v", 0))) for b in bars[-20:]]
    if not volumes or sum(volumes) == 0:
        return 0.5
    avg_vol = sum(volumes) / len(volumes)
    recent_vol = sum(volumes[-5:]) / min(len(volumes[-5:]), 5)
    return min(recent_vol / avg_vol if avg_vol > 0 else 1.0, 2.0) / 2.0
def score_sd_zone_overall(bars: list[dict], zone_high: float, zone_low: float,
                          zone_type: str = "DEMAND", direction: str = "BUY") -> dict:
    """Overall S/D zone strength — weighted combination of 5 factors.
    Formula: 0.10×FVG + 0.40×Consolidation + 0.25×Wick + 0.25×Body
    """
    consolidation = score_consolidation_strength(bars, zone_high, zone_low)
    wick = score_wick_strength(bars, zone_type)
    fvg = score_fvg_strength(bars)
    body = score_body_strength(bars, direction)
    volume = score_volume_strength(bars)
    overall = 0.10 * fvg + 0.40 * consolidation + 0.25 * wick + 0.25 * body
    return {
        "overall": round(overall, 2),
        "consolidation": consolidation,
        "wick": wick,
        "fvg": fvg,
        "body": body,
        "volume": volume,
        "grade": Grade.from_score(int(overall * 20), 20),
    }
def detect_liquidity_levels(ohlcv: list[dict], lookback: int = 100) -> dict:
    """Detect liquidity levels — zones where stop orders accumulate."""
    if len(ohlcv) < lookback:
        return {"buy_side": [], "sell_side": []}
    bars = ohlcv[-lookback:]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    buy_side: list[dict] = []
    sell_side: list[dict] = []
    order = 5
    for i in range(order, len(bars) - order):
        if all(highs[i] >= highs[j] for j in range(i - order, i + order + 1) if j != i):
            nearby_eq = sum(1 for j in range(max(0, i - 10), min(len(highs), i + 10))
                          if abs(highs[j] - highs[i]) / highs[i] < 0.001)
            if nearby_eq >= 2:
                buy_side.append({"price": highs[i], "strength": nearby_eq, "index": i})
        if all(lows[i] <= lows[j] for j in range(i - order, i + order + 1) if j != i):
            nearby_eq = sum(1 for j in range(max(0, i - 10), min(len(lows), i + 10))
                          if abs(lows[j] - lows[i]) / lows[i] < 0.001)
            if nearby_eq >= 2:
                sell_side.append({"price": lows[i], "strength": nearby_eq, "index": i})
    return {"buy_side": buy_side[-5:], "sell_side": sell_side[-5:]}
def detect_liquidity_sweep(ohlcv: list[dict], lookback: int = 30) -> dict | None:
    """Detect liquidity sweep — price breaks a level and immediately reverses."""
    if len(ohlcv) < lookback:
        return None
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    liquidity = detect_liquidity_levels(ohlcv, lookback)
    for level in liquidity["buy_side"]:
        lvl_price = level["price"]
        for i in range(len(bars) - 3, len(bars)):
            if highs[i] > lvl_price and closes[i] < lvl_price:
                return {"type": "BUY_SIDE_SWEPT", "price": lvl_price,
                        "sweep_high": highs[i], "close": closes[i],
                        "signal": "SELL",
                        "description": "Buy-side liquidity swept → potensi reversal bearish"}
    for level in liquidity["sell_side"]:
        lvl_price = level["price"]
        for i in range(len(bars) - 3, len(bars)):
            if lows[i] < lvl_price and closes[i] > lvl_price:
                return {"type": "SELL_SIDE_SWEPT", "price": lvl_price,
                        "sweep_low": lows[i], "close": closes[i],
                        "signal": "BUY",
                        "description": "Sell-side liquidity swept → potensi reversal bullish"}
    return None
def anti_range_filter(ohlcv: list[dict]) -> dict:
    """Triple filter to avoid ranging/choppy markets.
    Rules: ADX >= 22, ATR/Close >= 0.12%, Volume >= SMA(20)×1.12
    """
    if len(ohlcv) < 30:
        return {"pass": False, "reason": "Data insufficient"}
    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    highs = [float(b.get("high", b.get("h", 0))) for b in ohlcv]
    lows = [float(b.get("low", b.get("l", 0))) for b in ohlcv]
    volumes = [float(b.get("volume", b.get("v", 0))) for b in ohlcv[-30:]]
    price = closes[-1]
    atr_vals = _atr(highs, lows, closes, 14)
    atr_now = atr_vals[-1] if atr_vals[-1] > 0 else 0.01
    atr_ratio = atr_now / price * 100 if price > 0 else 0
    # Simplified ADX
    dm_plus: list[float] = []
    dm_minus: list[float] = []
    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        dm_plus.append(up if up > down and up > 0 else 0)
        dm_minus.append(down if down > up and down > 0 else 0)
    smooth_period = 14
    if len(dm_plus) >= smooth_period:
        tr_sum = sum(atr_vals[-smooth_period:])
        di_plus = sum(dm_plus[-smooth_period:]) / tr_sum * 100 if tr_sum > 0 else 0
        di_minus = sum(dm_minus[-smooth_period:]) / tr_sum * 100 if tr_sum > 0 else 0
        dx = abs(di_plus - di_minus) / (di_plus + di_minus) * 100 if (di_plus + di_minus) > 0 else 0
        adx = dx
    else:
        adx = 0
    adx_ok = adx >= 22
    atr_close_ok = atr_ratio >= 0.12
    vol_sma20 = sum(volumes) / len(volumes) if volumes else 0
    vol_now = volumes[-1] if volumes else 0
    vol_ok = vol_now >= vol_sma20 * 1.12 if vol_sma20 > 0 else False
    all_pass = adx_ok and atr_close_ok and vol_ok
    reasons: list[str] = []
    if not adx_ok:
        reasons.append(f"ADX={adx:.1f} < 22 (ranging)")
    if not atr_close_ok:
        reasons.append(f"ATR/Close={atr_ratio:.3f}% < 0.12% (low volatility)")
    if not vol_ok:
        reasons.append(f"Vol={vol_now:.0f} < SMA×1.12={vol_sma20 * 1.12:.0f} (low volume)")
    return {"pass": all_pass, "adx": round(adx, 1), "atr_ratio": round(atr_ratio, 3),
            "vol_ok": vol_ok, "reasons": reasons}
def ema200_daily_bias(ohlcv: list[dict]) -> dict:
    """Daily bias using EMA200 comparison. Neutral zone = EMA200 × 0.85% dead band."""
    if len(ohlcv) < 200:
        return {"bias": "NEUTRAL", "reason": "Need 200+ bars"}
    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    ema200_vals = _ema(closes, 200)
    price = closes[-1]
    ema200_val = ema200_vals[-1]
    diff_pct = (price - ema200_val) / ema200_val * 100 if ema200_val > 0 else 0
    if abs(diff_pct) < 0.85:
        return {"bias": "NEUTRAL", "ema200": ema200_val, "diff_pct": round(diff_pct, 2),
                "description": "Harga dalam dead band EMA200 — tidak ada bias jelas"}
    elif price > ema200_val:
        return {"bias": "BULLISH", "ema200": ema200_val, "diff_pct": round(diff_pct, 2),
                "description": f"Harga di atas EMA200 (+{diff_pct:.1f}%) — bias bullish"}
    else:
        return {"bias": "BEARISH", "ema200": ema200_val, "diff_pct": round(diff_pct, 2),
                "description": f"Harga di bawah EMA200 ({diff_pct:.1f}%) — bias bearish"}
def detect_order_block_advanced(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """Advanced Order Block detection with body/range ratio filter.
    Rule: body/range >= 0.35, opposite confirmation bar.
    """
    if len(ohlcv) < lookback:
        return None
    bars = ohlcv[-lookback:]
    best_ob = None
    best_strength = 0.0
    for i in range(len(bars) - 3, 0, -1):
        b = bars[i]
        o = float(b.get("open", b.get("o", 0)))
        c = float(b.get("close", b.get("c", 0)))
        h = float(b.get("high", b.get("h", 0)))
        l = float(b.get("low", b.get("l", 0)))  # noqa: E741
        body = abs(c - o)
        candle_range = h - l
        if candle_range <= 0:
            continue
        body_ratio = body / candle_range
        if body_ratio < 0.35:
            continue
        # Bearish OB (Supply)
        if c < o:  # noqa: SIM102
            if i + 1 < len(bars):
                next_c = float(bars[i + 1].get("close", bars[i + 1].get("c", 0)))
                next_o = float(bars[i + 1].get("open", bars[i + 1].get("o", 0)))
                if next_c < next_o:
                    strength = body_ratio * 5
                    if strength > best_strength:
                        best_strength = strength
                        best_ob = {"direction": "SELL", "upper": o, "lower": c,
                                   "strength": min(round(strength, 1), 5.0),
                                   "body_ratio": round(body_ratio, 2)}
        # Bullish OB (Demand)
        if c > o:  # noqa: SIM102
            if i + 1 < len(bars):
                next_c = float(bars[i + 1].get("close", bars[i + 1].get("c", 0)))
                next_o = float(bars[i + 1].get("open", bars[i + 1].get("o", 0)))
                if next_c > next_o:
                    strength = body_ratio * 5
                    if strength > best_strength:
                        best_strength = strength
                        best_ob = {"direction": "BUY", "upper": c, "lower": o,
                                   "strength": min(round(strength, 1), 5.0),
                                   "body_ratio": round(body_ratio, 2)}
    return best_ob
def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD indicator."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    signal_line = _ema(macd_line, signal)
    histogram = [macd_line[i] - signal_line[i] for i in range(len(closes))]
    return {"macd": macd_line[-1], "signal": signal_line[-1],
            "histogram": histogram[-1], "bullish": macd_line[-1] > signal_line[-1]}
def calc_bollinger(closes: list[float], period: int = 20, std: float = 2.0) -> dict:
    """Bollinger Bands indicator."""
    sma = _sma(closes, period)
    if len(closes) < period:
        return {}
    std_dev = (sum((c - sma[-1]) ** 2 for c in closes[-period:]) / period) ** 0.5
    upper = sma[-1] + std * std_dev
    lower = sma[-1] - std * std_dev
    pct_b = (closes[-1] - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return {"upper": upper, "lower": lower, "middle": sma[-1],
            "pct_b": round(pct_b, 2), "width": round((upper - lower) / sma[-1] * 100, 2) if sma[-1] > 0 else 0}  # noqa: E501
def calc_heikin_ashi(bars: list[dict]) -> list[dict]:
    """Heikin-Ashi candlestick transformation — noise filter."""
    ha_bars: list[dict] = []
    for i, b in enumerate(bars):
        o = float(b.get("open", b.get("o", 0)))
        h = float(b.get("high", b.get("h", 0)))
        l = float(b.get("low", b.get("l", 0)))  # noqa: E741
        c = float(b.get("close", b.get("c", 0)))
        ha_close = (o + h + l + c) / 4
        if i == 0:  # noqa: SIM108
            ha_open = (o + c) / 2
        else:
            ha_open = (ha_bars[-1]["ha_open"] + ha_bars[-1]["ha_close"]) / 2
        ha_high = max(h, ha_open, ha_close)
        ha_low = min(l, ha_open, ha_close)
        ha_bars.append({"ha_open": ha_open, "ha_high": ha_high, "ha_low": ha_low,
                        "ha_close": ha_close, "bullish": ha_close > ha_open})
    return ha_bars
def london_breakout_levels(ohlcv: list[dict], session_hour: int = 7) -> dict | None:
    """London Breakout strategy. Pre-London hour range defines breakout levels."""
    if len(ohlcv) < 12:
        return None
    bars = ohlcv[-12:]
    session_bars = [b for b in bars if 7 <= float(b.get("hour", 7)) < 8]
    if not session_bars:
        return None
    session_high = max(float(b.get("high", b.get("h", 0))) for b in session_bars)
    session_low = min(float(b.get("low", b.get("l", 0))) for b in session_bars)
    current = float(bars[-1].get("close", bars[-1].get("c", 0)))
    return {"range_high": session_high, "range_low": session_low,
            "current": current, "range_size": session_high - session_low,
            "breakout_up": current > session_high, "breakout_down": current < session_low}
@dataclass
class UltimateResult:
    """24-point SMC scoring system combining all advanced features."""
    signal: str = "HOLD"
    direction: str = ""
    grade: int = 1
    grade_label: str = ""
    score: int = 0
    max_score: int = 24
    reasons: list = field(default_factory=list)
    # Sub-scores
    fibonacci_score: int = 0
    sd_strength_score: int = 0
    liquidity_score: int = 0
    order_block_score: int = 0
    technical_score: int = 0
    bias_score: int = 0
    # Raw data
    fib_levels: dict = field(default_factory=dict)
    fib_confluence: dict = field(default_factory=dict)
    sd_zones: list = field(default_factory=list)
    sd_overall: dict = field(default_factory=dict)
    liquidity: dict = field(default_factory=dict)
    sweep: dict = field(default_factory=dict)
    order_block: dict = field(default_factory=dict)
    anti_range: dict = field(default_factory=dict)
    ema_bias: dict = field(default_factory=dict)
    macd: dict = field(default_factory=dict)
    bollinger: dict = field(default_factory=dict)
    london_breakout: dict = field(default_factory=dict)


# ── Engine Implementation ──────────────────────────────────────────


class SMCEngine(Engine):
    """SMC Scalper Engine — Smart Money Concepts analysis.

    Detects CHoCH, BOS, IDM, FVG, Order Blocks, and Supply/Demand zones.
    When OHLCV candle data is available (via tick metadata), runs advanced
    analysis including Fibonacci, zone scoring, EMA200 bias, liquidity sweep,
    MACD, Bollinger, and the 24-point UltimateResult scoring system.
    """

    def __init__(self) -> None:
        self._min_quality: str = getattr(settings, "SMC_MIN_QUALITY", "BAGUS")
        self._lookback: int = getattr(settings, "SMC_LOOKBACK", 50)

    @property
    def name(self) -> str:
        return "smc_scalper"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks using SMC Smart Money Concepts."""
        if not ticks or len(ticks) < 50:
            LOG.debug("SMC: insufficient ticks (%d)", len(ticks) if ticks else 0)
            return None

        try:
            # Prefer real OHLCV candles from tick metadata when available
            ohlcv = self._extract_ohlcv(ticks)
            return self._run_analysis(ohlcv)
        except Exception as exc:
            LOG.warning("SMC engine error: %s", exc)
            raise SignalError("SMC analysis failed", details={"error": str(exc)}) from exc

    # ── OHLCV Extraction ─────────────────────────────────────────────

    @staticmethod
    def _extract_ohlcv(ticks: list[Tick]) -> list[dict]:
        """Extract OHLCV bars from tick metadata or fall back to tick-as-bar."""
        # Check if any tick carries real OHLCV data
        for t in ticks:
            meta = getattr(t, "metadata", None)
            if meta and isinstance(meta, dict) and "ohlcv" in meta:
                return meta["ohlcv"]
        return _ticks_to_ohlcv(ticks)

    # ── Core Analysis (primary path — unchanged behavior) ────────────

    def _run_analysis(self, ohlcv: list[dict]) -> Signal | None:
        closes = [float(b.get("close", 0)) for b in ohlcv]
        last_price = closes[-1] if closes else 0.0

        choch = detect_choch(ohlcv, self._lookback)
        bos = detect_bos(ohlcv, self._lookback)
        fvg = detect_fvg_zones(ohlcv, lookback=self._lookback)
        ob = detect_order_block(ohlcv, self._lookback)
        sd_zones = detect_supply_demand_zones(ohlcv, lookback=self._lookback)

        direction = None
        if choch:
            direction = choch["direction"]
        if bos and direction is None:
            direction = bos["direction"]
        if fvg and direction is None:
            direction = fvg["direction"]

        if direction is None:
            return None

        # Trend alignment
        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200) if len(closes) >= 200 else [0.0] * len(closes)
        trend_up = ema50[-1] > ema200[-1] if ema200[-1] > 0 else ema50[-1] > ema50[-20]
        trend_down = ema50[-1] < ema200[-1] if ema200[-1] > 0 else ema50[-1] < ema50[-20]

        conf = SMCConfirmation()
        conf.choch_detected = choch is not None
        conf.bos_detected = bos is not None
        conf.fvg_detected = fvg is not None
        conf.order_block_valid = ob is not None and (direction is None or ob["direction"] == direction)  # noqa: E501
        conf.sd_zone_aligned = len(sd_zones) > 0
        conf.trend_aligned = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_down)  # noqa: E501
        conf.session_optimal = True

        grade_val = conf.grade_value
        if grade_val < Grade.BAGUS:
            LOG.debug("SMC: grade too weak (%d)", grade_val)
            return None

        # Build Signal
        signal_direction = "CALL" if direction == "BUY" else "PUT"
        conf_pct = min(1.0, conf.score / 18.0)
        symbol = "XAUUSD"

        metadata: dict = {
            "engine": self.name,
            "smc_score": conf.score,
            "grade_value": grade_val,
            "grade_label": Grade.label(grade_val),
            "direction_raw": direction,
            "choch": choch is not None,
            "bos": bos is not None,
            "fvg": fvg is not None,
            "order_block": ob is not None,
            "reasons": [f"SMC {direction} — {Grade.label(grade_val)}"],
        }

        # ── Enhanced analysis when real OHLCV is available ──
        if self._has_real_ohlcv(ohlcv):
            enhanced = self._run_enhanced_analysis(ohlcv, direction)
            if enhanced:
                metadata.update(enhanced)

        return Signal(
            symbol=symbol,
            direction=signal_direction,
            predicted_digit=int(last_price * 10) % 10,
            confidence=conf_pct,
            source=SignalSource.MOMEN,
            grade=SignalGrade.STRONG if conf_pct >= 0.7 else (
                SignalGrade.MODERATE if conf_pct >= 0.5 else SignalGrade.WEAK
            ),
            metadata=metadata,
        )

    # ── Enhanced Analysis (OHLCV-based, 24-point scoring) ────────────

    @staticmethod
    def _has_real_ohlcv(ohlcv: list[dict]) -> bool:
        """Check whether bars carry real candle data (not tick-as-bar)."""
        if len(ohlcv) < 5:
            return False
        for b in ohlcv[:5]:
            h = float(b.get("high", b.get("h", 0)))
            l = float(b.get("low", b.get("l", 0)))  # noqa: E741
            if h != l:
                return True
        return False

    def _run_enhanced_analysis(self, ohlcv: list[dict], direction: str) -> dict | None:
        """Run advanced OHLCV-based analysis and return metadata dict."""
        result = UltimateResult()
        result.direction = direction

        closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
        last_price = closes[-1]

        # 1. EMA200 bias
        ema_bias = ema200_daily_bias(ohlcv)
        result.ema_bias = ema_bias
        if ema_bias.get("bias") != "NEUTRAL":
            result.bias_score += 1

        # 2. Anti-range filter
        ar = anti_range_filter(ohlcv)
        result.anti_range = ar

        # 3. Fibonacci levels
        swings = detect_swing_points(ohlcv)
        if swings.get("swing_high") and swings.get("swing_low"):
            fib_dir = "DOWN" if swings["swing_high_idx"] > swings["swing_low_idx"] else "UP"
            fib_levels = calc_fib_levels(swings["swing_high"], swings["swing_low"], fib_dir)
            result.fib_levels = fib_levels

        # 4. Fib + S/D confluence
        sd_zones = detect_supply_demand_zones(ohlcv, lookback=self._lookback)
        result.sd_zones = sd_zones
        if result.fib_levels and sd_zones:
            confluence = find_fib_confluence(last_price, result.fib_levels, sd_zones)
            result.fib_confluence = confluence
            result.fibonacci_score = min(confluence.get("strength", 0), 4)

        # 5. Advanced order block
        adv_ob = detect_order_block_advanced(ohlcv)
        result.order_block = adv_ob
        if adv_ob:
            result.order_block_score = min(int(adv_ob.get("strength", 0)), 4)

        # 6. S/D zone strength scoring
        if sd_zones:
            best_sd = sd_zones[0]
            sd_score = score_sd_zone_overall(
                ohlcv[-min(len(ohlcv), 50):],
                best_sd.get("upper", best_sd.get("mid", last_price * 1.01)),
                best_sd.get("lower", best_sd.get("mid", last_price * 0.99)),
                best_sd.get("type", "DEMAND"),
                "BUY" if best_sd.get("type") == "DEMAND" else "SELL",
            )
            result.sd_overall = sd_score
            result.sd_strength_score = int(sd_score.get("overall", 0) * 5)

        # 7. Liquidity detection + sweep
        liquidity = detect_liquidity_levels(ohlcv)
        result.liquidity = liquidity
        sweep = detect_liquidity_sweep(ohlcv)
        result.sweep = sweep
        if sweep:
            result.liquidity_score = 4
            if not result.direction:
                result.direction = sweep.get("signal", "")
        elif liquidity.get("buy_side") or liquidity.get("sell_side"):
            result.liquidity_score = 2

        # 8. Technical indicators
        macd = calc_macd(closes)
        result.macd = macd
        bollinger = calc_bollinger(closes)
        result.bollinger = bollinger
        rsi_vals = _rsi(closes, 14)
        rsi_now = rsi_vals[-1]

        tech_score = 0
        if macd.get("bullish"):
            tech_score += 1
        if bollinger and 0.2 < bollinger.get("pct_b", 0.5) < 0.8:
            tech_score += 1
        if 30 < rsi_now < 70:
            tech_score += 1
        result.technical_score = tech_score

        # 9. London breakout
        lb = london_breakout_levels(ohlcv)
        result.london_breakout = lb
        if lb:
            result.bias_score += 1

        # 10. Combined scoring
        total = (result.fibonacci_score + result.sd_strength_score + result.liquidity_score
                 + result.order_block_score + result.technical_score + result.bias_score)
        result.score = min(total, 24)
        result.max_score = 24
        result.grade = Grade.from_score(result.score, 24)
        result.grade_label = Grade.label(result.grade)
        result.emoji = Grade.emoji(result.grade)

        # 11. Build reasons
        reasons: list[str] = []
        if ema_bias.get("bias") != "NEUTRAL":
            reasons.append(f"EMA200: {ema_bias.get('description', '')}")
        if ar.get("pass"):
            reasons.append("Anti-Range: PASS — market trending")
        elif ar.get("reasons"):
            reasons.append(f"Anti-Range: {ar['reasons'][0]}")
        if result.fib_levels:
            fib_618 = result.fib_levels.get("0.618")
            if fib_618:
                reasons.append(f"Fib 0.618: {fib_618:.2f}")
        if result.fib_confluence.get("matched"):
            reasons.append(f"Fib+S/D Confluence: {result.fib_confluence.get('strength', 0)} matches")  # noqa: E501
        if sd_zones:
            best = sd_zones[0]
            reasons.append(f"Nearest S/D: {best['type']} @ {best.get('mid', best.get('upper', 0)):.2f} [str:{best.get('strength', 0):.1f}]")  # noqa: E501
        if adv_ob:
            reasons.append(f"Order Block: {adv_ob['direction']} [str:{adv_ob.get('strength', 0):.1f}] body:{adv_ob.get('body_ratio', 0):.0%}")  # noqa: E501
        if sweep:
            reasons.append(sweep.get("description", "Liquidity Sweep"))
        if macd:
            reasons.append(f"MACD: {'Bullish' if macd.get('bullish') else 'Bearish'}")
        if bollinger:
            reasons.append(f"BB %b: {bollinger.get('pct_b', 0):.2f} | Width: {bollinger.get('width', 0):.1f}%")  # noqa: E501
        reasons.append(f"Score: {result.score}/{result.max_score} | Fib:{result.fibonacci_score} SD:{result.sd_strength_score} Liq:{result.liquidity_score} OB:{result.order_block_score} Tech:{result.technical_score}")  # noqa: E501

        result.reasons = reasons
        return self._ultimate_to_metadata(result)

    @staticmethod
    def _ultimate_to_metadata(result: UltimateResult) -> dict:
        """Convert UltimateResult to a flat metadata dict for Signal."""
        return {
            "ultimate_score": result.score,
            "ultimate_max": result.max_score,
            "ultimate_grade": result.grade,
            "ultimate_grade_label": result.grade_label,
            "ultimate_emoji": getattr(result, "emoji", Grade.emoji(result.grade)),
            "sub_scores": {
                "fibonacci": result.fibonacci_score,
                "sd_strength": result.sd_strength_score,
                "liquidity": result.liquidity_score,
                "order_block": result.order_block_score,
                "technical": result.technical_score,
                "bias": result.bias_score,
            },
            "fib_levels": result.fib_levels,
            "fib_confluence": result.fib_confluence,
            "sd_zones": result.sd_zones,
            "sd_overall": result.sd_overall,
            "liquidity": result.liquidity,
            "liquidity_sweep": result.sweep,
            "advanced_ob": result.order_block,
            "anti_range": result.anti_range,
            "ema_bias": result.ema_bias,
            "macd": result.macd,
            "bollinger": result.bollinger,
            "london_breakout": result.london_breakout,
            "ultimate_reasons": result.reasons,
        }
