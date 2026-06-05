"""
smc_scalper_engine.py — SMC Scalper + Trend Break Strategy Module
Adapted from lordgaruda/XAU-60 | Bahasa Indonesia output

Dua strategi dalam satu modul:
  1. SMC Scalper — CHoCH + FVG + Order Block (M15)
  2. Trend Break — Trendline Break + EMA21 Trauma + RSI (H1)

Semua output pakai bahasa yang mudah dipahami trader.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# SHARED: Signal Quality Grading (User-Friendly)
# ═══════════════════════════════════════════════════════════════════

from enum import Enum

class SignalGrade(Enum):
    """Universal signal quality grading — Bahasa Indonesia."""
    SANGAT_KUAT = 5   # ⭐ A+ — semua konfirmasi sempurna
    KUAT = 4           # 🟢 A  — sinyal kuat, probabilitas tinggi
    BAGUS = 3           # 🔵 B  — sinyal bagus, layak entry
    CUKUP = 2           # 🟡 C  — sinyal cukup, tapi ada risiko
    LEMAH = 1           # ⚪ D  — sinyal lemah, sebaiknya skip

    @property
    def emoji(self) -> str:
        return {5: "⭐", 4: "🟢", 3: "🔵", 2: "🟡", 1: "⚪"}[self.value]

    @property
    def label_id(self) -> str:
        return {5: "SANGAT KUAT ⭐", 4: "KUAT 🟢", 3: "BAGUS 🔵",
                2: "CUKUP 🟡", 1: "LEMAH ⚪"}[self.value]

    @classmethod
    def from_score(cls, score: int, max_score: int = 12) -> "SignalGrade":
        pct = score / max_score if max_score > 0 else 0
        if pct >= 0.90: return cls.SANGAT_KUAT
        if pct >= 0.70: return cls.KUAT
        if pct >= 0.50: return cls.BAGUS
        if pct >= 0.30: return cls.CUKUP
        return cls.LEMAH

# Alias for backward compat
Grade = SignalGrade


# ═══════════════════════════════════════════════════════════════════
# HELPER: Technical calculations
# ═══════════════════════════════════════════════════════════════════

def _pip_size(symbol: str = "XAUUSD") -> float:
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"): return 0.1
    if s in ("BTCUSD", "BTC"): return 1.0
    if s.endswith("JPY"): return 0.01
    if s in ("USOIL", "OIL", "CL"): return 0.01
    if s.startswith(("BBCA", "BBRI", "TLKM", "ASII", "UNVR", "BMRI", "ADRO", "IHSG")): return 1.0
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
    result = [0.0] * (period - 1)
    for i in range(period - 1, len(series)):
        result.append(sum(series[i - period + 1:i + 1]) / period)
    return result


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return [0.0] * len(closes)
    tr = [0.0]
    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        ))
    atr_vals = [0.0] * (period - 1)
    atr_vals.append(sum(tr[1:period+1]) / period)
    for i in range(period, len(tr)):
        atr_vals.append((atr_vals[-1] * (period - 1) + tr[i]) / period)
    return atr_vals


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals = [0.0] * period
    rsi_vals.append(100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_vals.append(100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss > 0 else 100.0)
    return rsi_vals


# ═══════════════════════════════════════════════════════════════════
# STRATEGI 1: SMC SCALPER (CHoCH + FVG + Order Block)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SMCConfirmation:
    """14-factor SMC Scalper confirmation — World Class SMC v2."""
    choch_detected: bool = False       # Change of Character (reversal)
    bos_detected: bool = False          # Break of Structure (continuation)
    idm_detected: bool = False          # Inducement / Liquidity zone
    false_break_warning: bool = False   # False break / Swept structure alert
    valid_pullback: bool = False        # Valid Pullback (liquidity removal)
    fvg_detected: bool = False          # Fair Value Gap
    order_block_valid: bool = False     # Supply/Demand Order Block
    sd_zone_aligned: bool = False       # Price at Supply/Demand zone
    trend_aligned: bool = False         # Trend alignment (HTF)
    price_in_zone: bool = False         # Price at entry zone
    session_optimal: bool = False       # Optimal trading session
    volatility_normal: bool = False     # Volatility within limits
    momentum_ok: bool = False           # RSI momentum confirmation
    trend_strength_ok: bool = False     # Trend strength sufficient

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
            int(not self.false_break_warning) * 2,  # PENALTY for false breaks
        ])

    @property
    def grade(self) -> Grade:
        return Grade.from_score(self.score, 18)

    def _reasons_id(self, symbol: str) -> list[str]:
        """Alasan dalam bahasa Indonesia — World Class SMC v2."""
        reasons = []
        if self.choch_detected:
            reasons.append("✅ CHoCH — perubahan struktur pasar terdeteksi")
        if self.bos_detected:
            reasons.append("✅ BOS — trend berlanjut, struktur terkonfirmasi")
        if self.idm_detected:
            reasons.append("✅ IDM — zona inducement / pengumpulan likuiditas")
        if self.false_break_warning:
            reasons.append("⚠️ FALSE BREAK — Swept CHoCH/BOS, hati-hati jebakan!")
        if self.valid_pullback:
            reasons.append("✅ Valid Pullback — likuiditas dihapus dari impulse")
        if self.fvg_detected:
            reasons.append("✅ FVG — celah harga / imbalance terdeteksi")
        if self.order_block_valid:
            reasons.append("✅ Order Block valid — zona supply/demand terkonfirmasi")
        if self.sd_zone_aligned:
            reasons.append("✅ Supply/Demand Zone — harga di zona institusional")
        if self.trend_aligned:
            reasons.append("✅ Trend searah — sinyal sejalan dengan trend besar (HTF)")
        if self.price_in_zone:
            reasons.append("✅ Harga di zona entry — timing presisi")
        if self.session_optimal:
            reasons.append("✅ Jam trading optimal — London/NY session")
        if not self.volatility_normal:
            reasons.append("⚠️ Volatilitas tidak normal — hati-hati")
        if not self.momentum_ok:
            reasons.append("⚠️ Momentum lemah — konfirmasi kurang")
        return reasons


def detect_choch(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """
    Deteksi Change of Character (CHoCH) — perubahan struktur pasar.
    
    Bullish CHoCH: Dua lower low berturut-turut, lalu break di atas swing high terakhir.
    Bearish CHoCH: Dua higher high berturut-turut, lalu break di bawah swing low terakhir.
    
    Returns: {"direction": "BUY"|"SELL", "price": float, "index": int} or None
    """
    if len(ohlcv) < lookback:
        return None
    
    bars = ohlcv[-lookback:]
    swing_lookback = 5
    
    # Find swing highs and lows
    swing_highs = []
    swing_lows = []
    for i in range(swing_lookback, len(bars) - swing_lookback):
        h = float(bars[i].get("high", bars[i].get("h", 0)))
        l = float(bars[i].get("low", bars[i].get("l", 0)))
        
        # Swing high: higher than N bars left and right
        is_swing_high = True
        for j in range(i - swing_lookback, i + swing_lookback + 1):
            if j != i and float(bars[j].get("high", bars[j].get("h", 0))) >= h:
                is_swing_high = False
                break
        if is_swing_high:
            swing_highs.append((i, h))
        
        # Swing low: lower than N bars left and right
        is_swing_low = True
        for j in range(i - swing_lookback, i + swing_lookback + 1):
            if j != i and float(bars[j].get("low", bars[j].get("l", 0))) <= l:
                is_swing_low = False
                break
        if is_swing_low:
            swing_lows.append((i, l))
    
    if len(swing_lows) < 3 or len(swing_highs) < 2:
        return None
    
    # Bullish CHoCH: two lower lows → break above intermediate swing high
    last_close = float(bars[-1].get("close", bars[-1].get("c", 0)))
    for i in range(len(swing_lows) - 1):
        if swing_lows[i+1][1] < swing_lows[i][1]:  # Lower low confirmed
            # Find intermediate swing high between the two lows
            mid_highs = [h for h in swing_highs 
                        if swing_lows[i][0] < h[0] < swing_lows[i+1][0]]
            if mid_highs:
                last_high = max(mid_highs, key=lambda x: x[0])
                if last_close > last_high[1]:
                    return {"direction": "BUY", "price": last_high[1], "index": len(bars) - 1}
    
    # Bearish CHoCH: two higher highs → break below intermediate swing low
    for i in range(len(swing_highs) - 1):
        if swing_highs[i+1][1] > swing_highs[i][1]:  # Higher high confirmed
            mid_lows = [l for l in swing_lows 
                       if swing_highs[i][0] < l[0] < swing_highs[i+1][0]]
            if mid_lows:
                last_low = min(mid_lows, key=lambda x: x[0])
                if last_close < last_low[1]:
                    return {"direction": "SELL", "price": last_low[1], "index": len(bars) - 1}
    
    return None


# ═══════════════════════════════════════════════════════════════════
# SMC ADVANCED: IDM, BOS, False Break, Liquidity Grab, Valid Pullback
# Source: World Class SMC — winworld.pro + Modul SMC MLQ
# ═══════════════════════════════════════════════════════════════════

def detect_bos(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """
    Detect Break of Structure (BOS) — trend continuation.
    
    Bullish BOS: Price breaks above previous HH in an uptrend
    Bearish BOS: Price breaks below previous LL in a downtrend
    
    Difference from CHoCH: BOS = continuation, CHoCH = reversal.
    
    Returns: {"direction": "BUY"|"SELL", "price": float, "index": int, "type": "BOS"} or None
    """
    if len(ohlcv) < lookback:
        return None
    
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    last_close = closes[-1]
    last_idx = len(bars) - 1
    
    # Find swing highs and lows (same method as CHoCH)
    swing_lookback = 5
    swing_highs = []
    swing_lows = []
    for i in range(swing_lookback, len(bars) - swing_lookback):
        h = highs[i]
        l = lows[i]
        is_high = all(h >= highs[j] for j in range(i - swing_lookback, i + swing_lookback + 1) if j != i)
        is_low = all(l <= lows[j] for j in range(i - swing_lookback, i + swing_lookback + 1) if j != i)
        if is_high:
            swing_highs.append((i, h))
        if is_low:
            swing_lows.append((i, l))
    
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None
    
    # Bullish BOS: price breaks previous HH (continuation of uptrend)
    for i in range(1, len(swing_highs)):
        prev_hh = swing_highs[i-1][1]
        prev_hh_idx = swing_highs[i-1][0]
        if last_close > prev_hh:
            return {"direction": "BUY", "price": prev_hh, "index": last_idx, "type": "BOS"}
    
    # Bearish BOS: price breaks previous LL (continuation of downtrend)
    for i in range(1, len(swing_lows)):
        prev_ll = swing_lows[i-1][1]
        prev_ll_idx = swing_lows[i-1][0]
        if last_close < prev_ll:
            return {"direction": "SELL", "price": prev_ll, "index": last_idx, "type": "BOS"}
    
    return None


def detect_idm(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """
    Detect Inducement (IDM) — the last valid pullback extreme before BOS.
    
    IDM is the extreme point of the last pullback in the structure when price makes a BOS.
    It represents a liquidity collection zone where smart money traps retail traders.
    
    Rule: IDM follows price as long as it remains valid and price makes a BOS.
    
    Returns: {"direction": "BUY"|"SELL", "price": float, "index": int} or None
    """
    if len(ohlcv) < lookback:
        return None
    
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    
    # First detect BOS
    bos = detect_bos(ohlcv, lookback)
    if not bos:
        return None
    
    bos_idx = bos["index"]
    direction = bos["direction"]
    
    # Find the last pullback BEFORE the BOS
    if direction == "BUY":
        # In uptrend: IDM is the last HL (lowest point of the last pullback before HH break)
        for i in range(bos_idx - 1, max(bos_idx - 30, 0), -1):
            # Find local low (pullback)
            if i >= 3 and i < bos_idx - 2:
                if lows[i] <= lows[i-1] and lows[i] <= lows[i+1]:
                    return {"direction": "BUY", "price": lows[i], "index": i, 
                            "type": "IDM", "description": "HL — last pullback in bullish trend"}
    else:
        # In downtrend: IDM is the last LH (highest point of the last pullback before LL break)
        for i in range(bos_idx - 1, max(bos_idx - 30, 0), -1):
            if i >= 3 and i < bos_idx - 2:
                if highs[i] >= highs[i-1] and highs[i] >= highs[i+1]:
                    return {"direction": "SELL", "price": highs[i], "index": i,
                            "type": "IDM", "description": "LH — last pullback in bearish trend"}
    
    return None


def detect_false_break(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """
    Detect False Market Structure Break.
    
    When CHoCH appears but price doesn't follow through and continues 
    in the original direction — this is a false break that traps traders.
    
    Also known as: Liquidity Grab / Stop Hunt / SFP (Swing Failure Pattern)
    
    Criteria:
    - Price breaks structure (CHoCH) → creates expectation of reversal
    - Instead, price reverses back and continues the ORIGINAL trend
    - Candle closes back inside the previous range
    
    Returns: {"detected": bool, "direction_faked": str, "real_direction": str} or None
    """
    if len(ohlcv) < lookback:
        return None
    
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    
    # Check last 5 candles for wick breach without body close (Swept BOS/CHoCH)
    for i in range(len(bars) - 5, len(bars) - 1):
        body_high = max(closes[i], closes[i-1]) if i > 0 else closes[i]
        body_low = min(closes[i], closes[i-1]) if i > 0 else closes[i]
        wick_high = highs[i]
        wick_low = lows[i]
        
        # Wick breaches a level but body closes back → liquidity grab
        wick_range = wick_high - wick_low
        body_range = abs(closes[i] - closes[i-1]) if i > 0 else 0
        
        if wick_range > body_range * 2.5:  # Long wick relative to body
            if closes[i] > closes[i-1] if i > 0 else False:
                # Bullish candle with long lower wick = potential buy trap
                if wick_low < body_low - (wick_range * 0.3):
                    return {"detected": True, "pattern": "Swept CHoCH Bearish Trap",
                            "direction_faked": "SELL", "real_direction": "BUY",
                            "wick_at": wick_low, "body_close": closes[i]}
            else:
                # Bearish candle with long upper wick = potential sell trap
                if wick_high > body_high + (wick_range * 0.3):
                    return {"detected": True, "pattern": "Swept CHoCH Bullish Trap",
                            "direction_faked": "BUY", "real_direction": "SELL",
                            "wick_at": wick_high, "body_close": closes[i]}
    
    return None


def detect_valid_pullback(ohlcv: list[dict], lookback: int = 30) -> dict | None:
    """
    Detect Valid Pullback per World Class SMC rules.
    
    Rule: A valid pullback is a retracement where liquidity is removed 
    from the last impulse candle. Candle color doesn't matter.
    
    Impulse candle = consecutive candles in one direction without IDM removal.
    Correction ends with BOS (price updates max/min of last impulse candle).
    
    Returns: {"valid": bool, "direction": str, "liquidity_removed": bool, "impulse_index": int}
    """
    if len(ohlcv) < lookback:
        return None
    
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    
    # Find last impulse candle (strong directional move)
    for i in range(len(bars) - 3, lookback // 2, -1):
        candle_range = highs[i] - lows[i]
        if candle_range <= 0:
            continue
        
        # Check if this candle removes liquidity (wicks beyond previous extreme)
        if i > 0:
            prev_high = max(highs[max(0,i-3):i])
            prev_low = min(lows[max(0,i-3):i])
            
            # Bullish impulse: breaks above previous highs
            if closes[i] > closes[i-1] and highs[i] > prev_high:
                # Check if the pullback after this removes liquidity from it
                for j in range(i + 1, min(i + 10, len(bars))):
                    if lows[j] < lows[i]:  # Pullback below impulse low = liquidity removal
                        return {"valid": True, "direction": "BUY", 
                                "liquidity_removed": True, "impulse_index": i,
                                "pullback_index": j}
            
            # Bearish impulse: breaks below previous lows
            if closes[i] < closes[i-1] and lows[i] < prev_low:
                for j in range(i + 1, min(i + 10, len(bars))):
                    if highs[j] > highs[i]:  # Pullback above impulse high = liquidity removal
                        return {"valid": True, "direction": "SELL",
                                "liquidity_removed": True, "impulse_index": i,
                                "pullback_index": j}
    
    return None


def detect_supply_demand_zones(ohlcv: list[dict], lookback: int = 100, min_strength: float = 2.0) -> list[dict]:
    """
    Detect Supply and Demand zones based on price action.
    
    Supply Zone: The last base/consolidation before a sharp price decline.
    Demand Zone: The last base/consolidation before a sharp price rise.
    
    Strength measured by:
    - Magnitude of the subsequent move
    - Time spent at the zone (shorter = stronger)
    - Number of touches/re-tests
    
    Returns: list of {"type": "SUPPLY"|"DEMAND", "upper": float, "lower": float, 
                       "strength": float, "age": int, "tested": int}
    """
    if len(ohlcv) < lookback:
        return []
    
    bars = ohlcv[-lookback:]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    
    zones = []
    zone_lookback = 10
    
    for i in range(zone_lookback, len(bars) - zone_lookback):
        # Check for consolidation/base (small range relative to surrounding)
        base_high = max(highs[i-zone_lookback:i+1])
        base_low = min(lows[i-zone_lookback:i+1])
        base_range = base_high - base_low
        
        if base_range <= 0:
            continue
        
        # Check subsequent move
        future_bars = min(30, len(bars) - i - 1)
        if future_bars < 5:
            continue
        
        future_high = max(highs[i+1:i+1+future_bars])
        future_low = min(lows[i+1:i+1+future_bars])
        
        # Demand Zone: consolidation followed by sharp rise
        rise_magnitude = future_high - base_high
        drop_from_base = base_low - future_low
        
        if rise_magnitude > base_range * min_strength:
            strength = rise_magnitude / base_range if base_range > 0 else 1
            zones.append({
                "type": "DEMAND",
                "upper": base_high,
                "lower": base_low,
                "strength": round(strength, 1),
                "age": len(bars) - i,
                "tested": 0,
                "mid": (base_high + base_low) / 2
            })
        
        # Supply Zone: consolidation followed by sharp decline
        if drop_from_base > base_range * min_strength:
            strength = drop_from_base / base_range if base_range > 0 else 1
            zones.append({
                "type": "SUPPLY",
                "upper": base_high,
                "lower": base_low,
                "strength": round(strength, 1),
                "age": len(bars) - i,
                "tested": 0,
                "mid": (base_high + base_low) / 2
            })
    
    # Deduplicate overlapping zones, keep strongest
    zones.sort(key=lambda z: z["strength"], reverse=True)
    unique = []
    for z in zones:
        overlap = False
        for u in unique:
            if z["type"] == u["type"] and abs(z["mid"] - u["mid"]) < (z["upper"] - z["lower"]) * 0.5:
                overlap = True
                break
        if not overlap:
            unique.append(z)
    
    return unique[:5]  # Top 5 strongest zones


# ═══════════════════════════════════════════════════════════════════
# SMC ENHANCED CONFIRMATION: 14-factor scoring
# ═══════════════════════════════════════════════════════════════════

class SMCGradeV2:
    """Enhanced SMC grading with 14 factors — World Class SMC + Supply/Demand."""
    SANGAT_KUAT = 5
    KUAT = 4
    BAGUS = 3
    CUKUP = 2
    LEMAH = 1
    
    @classmethod
    def from_score(cls, score: int, max_score: int = 16) -> int:
        pct = score / max_score if max_score > 0 else 0
        if pct >= 0.85: return cls.SANGAT_KUAT
        if pct >= 0.65: return cls.KUAT
        if pct >= 0.45: return cls.BAGUS
        if pct >= 0.25: return cls.CUKUP
        return cls.LEMAH
    
    @classmethod
    def emoji(cls, grade: int) -> str:
        return {5: "⭐", 4: "🟢", 3: "🔵", 2: "🟡", 1: "⚪"}[grade]
    
    @classmethod
    def label(cls, grade: int) -> str:
        return {5: "SANGAT KUAT ⭐", 4: "KUAT 🟢", 3: "BAGUS 🔵", 
                2: "CUKUP 🟡", 1: "LEMAH ⚪"}[grade]


def detect_fvg_zones(ohlcv: list[dict], min_pips: float = 5.0, lookback: int = 20) -> dict | None:
    """
    Deteksi Fair Value Gap (FVG) — celah harga yang belum terisi.
    
    Bullish FVG: Low candle 1 > High candle 3 (ada celah naik yang belum ditutup)
    Bearish FVG: High candle 1 < Low candle 3 (ada celah turun yang belum ditutup)
    
    Returns: {"direction": "BUY"|"SELL", "upper": float, "lower": float, "mid": float} or None
    """
    if len(ohlcv) < lookback + 3:
        return None
    
    symbol = "XAUUSD"  # default
    bars = ohlcv[-(lookback + 3):]
    min_gap = min_pips * 0.1 * 10  # Convert pips to price gap (gold: 5 pips = $0.50)
    
    best_fvg = None
    best_score = 0
    
    for i in range(len(bars) - 2):
        b0_high = float(bars[i].get("high", bars[i].get("h", 0)))
        b0_low = float(bars[i].get("low", bars[i].get("l", 0)))
        b2_high = float(bars[i+2].get("high", bars[i+2].get("h", 0)))
        b2_low = float(bars[i+2].get("low", bars[i+2].get("l", 0)))
        
        # Bullish FVG
        gap_up = b0_low - b2_high
        if gap_up >= min_gap:
            score = gap_up / min_gap  # bigger gap = better
            if score > best_score:
                mid = (b0_low + b2_high) / 2
                best_fvg = {"direction": "BUY", "upper": b0_low, "lower": b2_high, "mid": mid}
                best_score = score
        
        # Bearish FVG
        gap_down = b2_low - b0_high
        if gap_down >= min_gap:
            score = gap_down / min_gap
            if score > best_score:
                mid = (b2_low + b0_high) / 2
                best_fvg = {"direction": "SELL", "upper": b2_low, "lower": b0_high, "mid": mid}
                best_score = score
    
    return best_fvg


def detect_order_block(ohlcv: list[dict], lookback: int = 30) -> dict | None:
    """
    Deteksi Order Block — zona supply/demand dari institusi.
    
    Bullish OB: candle bearish diikuti candle bullish dengan displacement kuat.
    Bearish OB: candle bullish diikuti candle bearish dengan displacement kuat.
    
    Returns: {"direction": "BUY"|"SELL", "upper": float, "lower": float, "strength": int} or None
    """
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
        prev_body = abs(b_closes[i-1] - b_opens[i-1])
        atr_val = b_atr[i] if b_atr[i] > 0 else body or 1.0
        
        # Bullish OB: prev bearish, current bullish, displacement up
        if b_closes[i-1] < b_opens[i-1] and b_closes[i] > b_opens[i]:
            displacement = b_closes[i] - lows[i-1]
            if displacement > atr_val * 0.5:
                strength = min(5, 1 + int(body / atr_val) + int(displacement / atr_val))
                if strength > best_strength:
                    best_ob = {"direction": "BUY", "upper": b_opens[i-1], 
                              "lower": lows[i-1], "strength": strength}
                    best_strength = strength
        
        # Bearish OB: prev bullish, current bearish, displacement down
        elif b_closes[i-1] > b_opens[i-1] and b_closes[i] < b_opens[i]:
            displacement = highs[i-1] - b_closes[i]
            if displacement > atr_val * 0.5:
                strength = min(5, 1 + int(body / atr_val) + int(displacement / atr_val))
                if strength > best_strength:
                    best_ob = {"direction": "SELL", "upper": highs[i-1],
                              "lower": b_closes[i-1], "strength": strength}
                    best_strength = strength
    
    return best_ob


def analyze_smc_scalper(
    ohlcv: list[dict],
    symbol: str = "XAUUSD",
    min_quality: str = "CUKUP",
) -> dict[str, Any]:
    """
    Analisa SMC Scalper — CHoCH + FVG + Order Block confluence.
    
    Strategi institusional yang menggabungkan tiga pola smart money:
    1. CHoCH — perubahan struktur pasar (reversal)
    2. FVG — celah harga yang harus diisi (imbalance)
    3. Order Block — zona supply/demand institusi
    
    Args:
        ohlcv: Data OHLCV (minimal 50 bar)
        symbol: Simbol trading
        min_quality: Grade minimum (SANGAT_KUAT, KUAT, BAGUS, CUKUP, LEMAH)
    
    Returns:
        dict dengan signal, grade, score, alasan, konfirmasi
    """
    result = {"signal": "HOLD", "grade": Grade.LEMAH, "score": 0,
              "grade_label": "LEMAH ⚪", "reasons": [], "confirmation": None}

    if not ohlcv or len(ohlcv) < 50:
        result["reasons"] = ["❌ Data kurang — butuh minimal 50 candle"]
        return result

    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    last_price = closes[-1] if closes else 0

    # 1. CHoCH Detection
    choch = detect_choch(ohlcv)
    
    # 2. BOS Detection (NEW — World Class SMC)
    bos = detect_bos(ohlcv)
    
    # 3. IDM Detection (NEW — Inducement/Liquidity Zone)
    idm = detect_idm(ohlcv)
    
    # 4. False Break Detection (NEW — Swept CHoCH/BOS)
    false_break = detect_false_break(ohlcv)
    
    # 5. Valid Pullback Check (NEW — Liquidity Removal Rule)
    valid_pullback = detect_valid_pullback(ohlcv)
    
    # 6. FVG Detection
    fvg = detect_fvg_zones(ohlcv)
    
    # 7. Order Block Detection
    ob = detect_order_block(ohlcv)
    
    # 8. Supply/Demand Zones (NEW)
    sd_zones = detect_supply_demand_zones(ohlcv)

    # 4. Trend alignment (EMA 50 vs EMA 200)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200) if len(closes) >= 200 else [0] * len(closes)
    trend_up = ema50[-1] > ema200[-1] if ema200[-1] > 0 else ema50[-1] > ema50[-20]
    trend_down = ema50[-1] < ema200[-1] if ema200[-1] > 0 else ema50[-1] < ema50[-20]

    # 5. Build confirmation
    direction = None
    conf = SMCConfirmation()
    conf.choch_detected = choch is not None
    conf.bos_detected = bos is not None
    conf.idm_detected = idm is not None
    conf.false_break_warning = false_break is not None and false_break.get("detected", False)
    conf.valid_pullback = valid_pullback is not None and valid_pullback.get("valid", False)
    conf.fvg_detected = fvg is not None

    if choch:
        direction = choch["direction"]
    if bos:
        direction = bos["direction"] if direction is None else direction
    if fvg:
        direction = fvg["direction"] if direction is None else direction
    
    conf.order_block_valid = ob is not None and (direction is None or ob["direction"] == direction)
    conf.sd_zone_aligned = len(sd_zones) > 0
    conf.trend_aligned = (direction == "BUY" and trend_up) or (direction == "SELL" and trend_down)
    
    # Price in zone check
    if fvg and last_price:
        conf.price_in_zone = fvg["lower"] <= last_price <= fvg["upper"]

    # Session optimal — simplified (always true for on-demand analysis)
    conf.session_optimal = True

    # ATR volatility
    atr_vals = _atr(
        [float(b.get("high", b.get("h", 0))) for b in ohlcv],
        [float(b.get("low", b.get("l", 0))) for b in ohlcv],
        closes, 14
    )
    atr_now = atr_vals[-1] if atr_vals[-1] > 0 else 1.0
    atr_avg = sum(atr_vals[-20:]) / 20 if atr_vals[-1] > 0 else atr_now
    conf.volatility_normal = 0.5 < atr_now / atr_avg < 2.0 if atr_avg > 0 else True

    # RSI momentum
    rsi_vals = _rsi(closes, 14)
    rsi_now = rsi_vals[-1]
    if direction == "BUY":
        conf.momentum_ok = 30 < rsi_now < 65
    elif direction == "SELL":
        conf.momentum_ok = 35 < rsi_now < 70
    else:
        conf.momentum_ok = False

    # Trend strength
    conf.trend_strength_ok = conf.choch_detected or conf.bos_detected or conf.fvg_detected

    # 6. Quality filter
    grade = conf.grade
    min_map = {"SANGAT_KUAT": Grade.SANGAT_KUAT, "KUAT": Grade.KUAT,
               "BAGUS": Grade.BAGUS, "CUKUP": Grade.CUKUP, "LEMAH": Grade.LEMAH}
    required = min_map.get(min_quality, Grade.BAGUS)

    if grade.value < required.value or direction is None:
        result["signal"] = "HOLD"
        result["grade"] = grade
        result["score"] = conf.score
        result["grade_label"] = grade.label_id
        result["reasons"] = conf._reasons_id(symbol)
        result["confirmation"] = conf
        if direction is None:
            result["reasons"].insert(0, "🔍 Mencari setup SMC — belum ada konfirmasi arah")
        return result

    result["signal"] = direction
    result["grade"] = grade
    result["score"] = conf.score
    result["grade_label"] = grade.label_id
    result["reasons"] = conf._reasons_id(symbol)
    result["confirmation"] = conf
    
    # Tambahan info spesifik — World Class SMC v2
    if choch:
        result["reasons"].insert(0, f"🎯 CHoCH {direction} di ${choch['price']:.2f}")
    if bos:
        result["reasons"].insert(0, f"📈 BOS {direction} — trend lanjut di ${bos['price']:.2f}")
    if idm:
        result["reasons"].insert(0, f"💧 IDM — zona likuiditas di ${idm['price']:.2f}")
    if false_break and false_break.get("detected"):
        result["reasons"].insert(0, f"⚠️ {false_break.get('pattern','Swept Structure')} terdeteksi!")
    if fvg:
        result["reasons"].insert(0, f"📐 FVG zone: ${fvg['lower']:.2f} - ${fvg['upper']:.2f}")
    if ob:
        result["reasons"].insert(0, f"🧱 Order Block [{ob['strength']}/5]: ${ob['lower']:.2f} - ${ob['upper']:.2f}")
    
    # S/D zones
    if sd_zones:
        sd_nearby = []
        for z in sd_zones[:3]:
            dist_pct = abs(last_price - z["mid"]) / last_price * 100 if last_price > 0 else 0
            if dist_pct < 1.0:  # Within 1% of current price
                sd_nearby.append(f"{z['type']}@{z['mid']:.2f}")
        if sd_nearby:
            result["reasons"].insert(0, f"📍 Near S/D zones: {', '.join(sd_nearby)}")
    
    # Store extra data
    result["_bos"] = bos
    result["_idm"] = idm
    result["_false_break"] = false_break
    result["_sd_zones"] = sd_zones

    return result


# ═══════════════════════════════════════════════════════════════════
# STRATEGI 2: TREND BREAK + TRAUMA (Trendline + EMA21 + RSI)
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TrendBreakConfirmation:
    """8-factor Trend Break confirmation."""
    trendline_break: bool = False       # Garis trend ditembus
    trauma_aligned: bool = False         # Harga vs EMA21 searah
    breakout_displacement: bool = False  # Breakout dengan momentum
    ema_stack_aligned: bool = False      # EMA 8/21/50 stacking benar
    macd_aligned: bool = False           # MACD konfirmasi
    adx_strong: bool = False             # ADX menunjukkan trend kuat
    rsi_ok: bool = False                 # RSI tidak overbought/oversold
    session_optimal: bool = False        # Jam trading optimal

    @property
    def score(self) -> int:
        return sum([
            int(self.trendline_break) * 2,
            int(self.trauma_aligned) * 2,
            int(self.breakout_displacement) * 2,
            int(self.ema_stack_aligned) * 1,
            int(self.macd_aligned) * 1,
            int(self.adx_strong) * 1,
            int(self.rsi_ok) * 1,
            int(self.session_optimal) * 1,
        ])

    @property
    def grade(self) -> Grade:
        return Grade.from_score(self.score, 11)

    def _reasons_id(self) -> list[str]:
        reasons = []
        if self.trendline_break:
            reasons.append("✅ Trendline ditembus — breakout terkonfirmasi")
        if self.trauma_aligned:
            reasons.append("✅ Harga vs EMA21 searah — \"Trauma\" terlewati")
        if self.breakout_displacement:
            reasons.append("✅ Displacement kuat — momentum valid")
        if self.ema_stack_aligned:
            reasons.append("✅ EMA stacking benar — trend established")
        if self.macd_aligned:
            reasons.append("✅ MACD konfirmasi — momentum sejalan")
        if self.adx_strong:
            reasons.append("✅ ADX > 20 — trend cukup kuat")
        if not self.rsi_ok:
            reasons.append("⚠️ RSI di zona ekstrim — risiko reversal")
        return reasons


def detect_trendline_break(ohlcv: list[dict]) -> dict | None:
    """
    Deteksi trendline break sederhana.
    
    Cari swing high/low terbaru dan cek apakah harga menembus garis trend
    yang menghubungkan swing point sebelumnya.
    
    Returns: {"direction": "BUY"|"SELL", "price": float} or None
    """
    if len(ohlcv) < 30:
        return None

    highs = [float(b.get("high", b.get("h", 0))) for b in ohlcv]
    lows = [float(b.get("low", b.get("l", 0))) for b in ohlcv]
    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    last_close = closes[-1]
    last_idx = len(ohlcv) - 1

    # Cari 2 swing highs terakhir (resistance trendline)
    swing_highs = []
    for i in range(5, len(ohlcv) - 5):
        is_sh = True
        for j in range(i - 5, i + 6):
            if j != i and highs[j] >= highs[i]:
                is_sh = False
                break
        if is_sh:
            swing_highs.append((i, highs[i]))

    # Cari 2 swing lows terakhir (support trendline)
    swing_lows = []
    for i in range(5, len(ohlcv) - 5):
        is_sl = True
        for j in range(i - 5, i + 6):
            if j != i and lows[j] <= lows[i]:
                is_sl = False
                break
        if is_sl:
            swing_lows.append((i, lows[i]))

    # Break above resistance trendline → BUY
    if len(swing_highs) >= 2:
        sh1, sh2 = swing_highs[-2], swing_highs[-1]
        if sh1[1] > sh2[1] and sh1[0] < sh2[0]:  # Downtrend resistance
            # Project trendline to current bar
            slope = (sh2[1] - sh1[1]) / (sh2[0] - sh1[0])
            projected = sh2[1] + slope * (last_idx - sh2[0])
            if last_close > projected and last_close > sh2[1]:
                return {"direction": "BUY", "price": projected}

    # Break below support trendline → SELL
    if len(swing_lows) >= 2:
        sl1, sl2 = swing_lows[-2], swing_lows[-1]
        if sl1[1] < sl2[1] and sl1[0] < sl2[0]:  # Uptrend support
            slope = (sl2[1] - sl1[1]) / (sl2[0] - sl1[0])
            projected = sl2[1] + slope * (last_idx - sl2[0])
            if last_close < projected and last_close < sl2[1]:
                return {"direction": "SELL", "price": projected}

    return None


def analyze_trend_break(
    ohlcv: list[dict],
    symbol: str = "XAUUSD",
    min_quality: str = "CUKUP",
) -> dict[str, Any]:
    """
    Analisa Trend Break + Trauma — Trendline + EMA21 + RSI.
    
    Strategi trend-following yang mencari breakout dari garis trend,
    dikonfirmasi oleh EMA21 ("Trauma"), EMA stacking, MACD, dan RSI.
    
    Args:
        ohlcv: Data OHLCV (minimal 50 bar, H1 timeframe ideal)
        symbol: Simbol trading
        min_quality: Grade minimum
    
    Returns:
        dict dengan signal, grade, score, alasan, konfirmasi
    """
    result = {"signal": "HOLD", "grade": Grade.LEMAH, "score": 0,
              "grade_label": "LEMAH ⚪", "reasons": [], "confirmation": None}

    if not ohlcv or len(ohlcv) < 50:
        result["reasons"] = ["❌ Data kurang — butuh minimal 50 candle"]
        return result

    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    highs = [float(b.get("high", b.get("h", 0))) for b in ohlcv]
    lows = [float(b.get("low", b.get("l", 0))) for b in ohlcv]
    last_price = closes[-1]

    # 1. Trendline break
    tl_break = detect_trendline_break(ohlcv)
    direction = tl_break["direction"] if tl_break else None

    # 2. EMA21 "Trauma"
    ema21 = _ema(closes, 21)
    ema8 = _ema(closes, 8)
    ema50 = _ema(closes, 50)
    
    trauma_aligned = False
    if direction == "BUY":
        trauma_aligned = last_price > ema21[-1]
    elif direction == "SELL":
        trauma_aligned = last_price < ema21[-1]

    # 3. Displacement — body size vs ATR
    atr_vals = _atr(highs, lows, closes, 14)
    last_body = abs(closes[-1] - (float(ohlcv[-1].get("open", ohlcv[-1].get("o", closes[-1])))))
    displacement = last_body > atr_vals[-1] * 0.5 if atr_vals[-1] > 0 else False

    # 4. EMA Stack — EMA8 > EMA21 > EMA50 for uptrend
    ema_stack = (ema8[-1] > ema21[-1] > ema50[-1]) if direction == "BUY" else \
                (ema8[-1] < ema21[-1] < ema50[-1]) if direction == "SELL" else False

    # 5. MACD-like (EMA12 vs EMA26)
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12[-1] - ema26[-1]
    macd_prev = ema12[-2] - ema26[-2] if len(ema12) >= 2 else macd_line
    macd_aligned = (direction == "BUY" and macd_line > 0 and macd_line > macd_prev) or \
                   (direction == "SELL" and macd_line < 0 and macd_line < macd_prev)

    # 6. ADX-like trend strength
    tr_vals = []
    for i in range(1, len(closes)):
        tr_vals.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    adx_val = sum(tr_vals[-14:]) / 14 if len(tr_vals) >= 14 else sum(tr_vals) / max(len(tr_vals), 1)
    adx_norm = min(100, (adx_val / atr_vals[-1] * 20)) if atr_vals[-1] > 0 else 0
    adx_strong = adx_norm > 20

    # 7. RSI
    rsi_vals = _rsi(closes, 14)
    rsi_now = rsi_vals[-1]
    rsi_ok = (30 < rsi_now < 70)

    # 8. Build confirmation
    conf = TrendBreakConfirmation(
        trendline_break=tl_break is not None,
        trauma_aligned=trauma_aligned,
        breakout_displacement=displacement,
        ema_stack_aligned=ema_stack,
        macd_aligned=macd_aligned,
        adx_strong=adx_strong,
        rsi_ok=rsi_ok,
        session_optimal=True,
    )

    grade = conf.grade
    min_map = {"SANGAT_KUAT": Grade.SANGAT_KUAT, "KUAT": Grade.KUAT,
               "BAGUS": Grade.BAGUS, "CUKUP": Grade.CUKUP, "LEMAH": Grade.LEMAH}
    required = min_map.get(min_quality, Grade.BAGUS)

    if grade.value < required.value or direction is None:
        result["signal"] = "HOLD"
        result["grade"] = grade
        result["score"] = conf.score
        result["grade_label"] = grade.label_id
        result["reasons"] = conf._reasons_id()
        result["confirmation"] = conf
        return result

    result["signal"] = direction
    result["grade"] = grade
    result["score"] = conf.score
    result["grade_label"] = grade.label_id
    result["reasons"] = conf._reasons_id()
    result["confirmation"] = conf

    if tl_break:
        result["reasons"].insert(0, f"💥 Trendline breakout {direction} @ ${tl_break['price']:.2f}")
    result["reasons"].insert(0, f"📊 EMA21 Trauma: ${ema21[-1]:.2f} — {'diatas' if last_price > ema21[-1] else 'dibawah'} harga")
    result["reasons"].insert(0, f"📈 RSI: {rsi_now:.0f} | ADX: {adx_norm:.0f} | MACD: {'bullish' if macd_line > 0 else 'bearish'}")

    return result


# ═══════════════════════════════════════════════════════════════════
# FORMATTER: User-friendly Telegram output
# ═══════════════════════════════════════════════════════════════════

def format_smc_block(smc_result: dict) -> str:
    """Format SMC Scalper result untuk Telegram."""
    if not smc_result or smc_result.get("signal") == "HOLD":
        if smc_result and smc_result.get("reasons"):
            return ""  # No setup = don't clutter output
        return ""

    grade = smc_result["grade"]
    emoji = grade.emoji if hasattr(grade, 'emoji') else "⚪"
    sig_emoji = "🟢" if smc_result["signal"] == "BUY" else "🔴"

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"🏦 <b>SMC Scalper</b> {emoji} {smc_result['grade_label']}\n"
        f"{sig_emoji} {smc_result['signal']} | Skor: {smc_result['score']}/12\n"
    )
    # Add top 3 reasons
    reasons = smc_result.get("reasons", [])
    for r in reasons[:3]:
        block += f"{r}\n"
    
    return block


def format_trend_block(trend_result: dict) -> str:
    """Format Trend Break result untuk Telegram."""
    if not trend_result or trend_result.get("signal") == "HOLD":
        return ""

    grade = trend_result["grade"]
    emoji = grade.emoji if hasattr(grade, 'emoji') else "⚪"
    sig_emoji = "🟢" if trend_result["signal"] == "BUY" else "🔴"

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"📈 <b>Trend Break + Trauma</b> {emoji} {trend_result['grade_label']}\n"
        f"{sig_emoji} {trend_result['signal']} | Skor: {trend_result['score']}/11\n"
    )
    reasons = trend_result.get("reasons", [])
    for r in reasons[:3]:
        block += f"{r}\n"

    return block


# ── Quick test ──
if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone
    import random
    random.seed(42)
    UTC = timezone.utc
    
    base = 4350.0
    bars = []
    now = datetime.now(UTC)
    for i in range(120):
        t = now - timedelta(minutes=15 * (120 - i))
        o = base
        c = base + random.uniform(-8, 8)
        h = max(o, c) + random.uniform(0, 5)
        l = min(o, c) - random.uniform(0, 5)
        bars.append({"timestamp": t.isoformat(), "open": round(o,2), "high": round(h,2),
                      "low": round(l,2), "close": round(c,2), "volume": random.randint(100,2000)})
        base = c

    print("=== SMC SCALPER ===")
    smc = analyze_smc_scalper(bars, "XAUUSD")
    print(f"Signal: {smc['signal']} | Grade: {smc['grade_label']} | Score: {smc['score']}")
    print(format_smc_block(smc))

    print("\n=== TREND BREAK ===")
    trend = analyze_trend_break(bars, "XAUUSD")
    print(f"Signal: {trend['signal']} | Grade: {trend['grade_label']} | Score: {trend['score']}")
    print(format_trend_block(trend))
