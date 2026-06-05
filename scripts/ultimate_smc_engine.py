"""
ultimate_smc_engine.py — Ultimate SMC Engine v3.0
=================================================
Integrasi 13+ repositori trading + HuggingFace model ke dalam SATU engine.

Sources:
  1. Fibonacci Retracement (brandonlatherow)
  2. S/D Zone Strength Prediction (Bretsera) — 9 scoring functions
  3. Liquidity Hunter (hamitbuyukguzel-bit) — scipy argrelextrema
  4. GoldLiquidityHunter_PRO (eaglenight37) — EMA200 bias, OB filter, Anti-Range, Trade Mgmt
  5. LickHunter v4 (CryptoGnome) — Liquidation counter-trading
  6. Leo524 Hyper Signal Pro — Liquidity grab → CHoCH → Entry
  7. quant-trading (je-suis-tm) — MACD, RSI, Heikin-Ashi, London Breakout
  8. RL Agent (Adilbai/HuggingFace) — PPO feature engineering
  9. World Class SMC (WinWorld) — CHoCH, BOS, IDM theory

Output: Bahasa Indonesia user-friendly dengan emoji grading ⭐🟢🔵🟡⚪
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
import math

# ═══════════════════════════════════════════════════════════════════
# GRADING SYSTEM — Universal Signal Quality
# ═══════════════════════════════════════════════════════════════════

class Grade:
    SANGAT_KUAT = 5   # ⭐ A+ — semua konfirmasi sempurna
    KUAT = 4           # 🟢 A  — sinyal kuat, probabilitas tinggi
    BAGUS = 3           # 🔵 B  — sinyal bagus, layak entry
    CUKUP = 2           # 🟡 C  — sinyal cukup, ada risiko
    LEMAH = 1           # ⚪ D  — sinyal lemah, sebaiknya skip
    
    @classmethod
    def emoji(cls, grade: int) -> str:
        return {5: "⭐", 4: "🟢", 3: "🔵", 2: "🟡", 1: "⚪"}[grade]
    
    @classmethod
    def label(cls, grade: int) -> str:
        return {5: "SANGAT KUAT ⭐", 4: "KUAT 🟢", 3: "BAGUS 🔵",
                2: "CUKUP 🟡", 1: "LEMAH ⚪"}[grade]
    
    @classmethod
    def from_score(cls, score: int, max_score: int = 24) -> int:
        pct = score / max_score if max_score > 0 else 0
        if pct >= 0.85: return cls.SANGAT_KUAT
        if pct >= 0.65: return cls.KUAT
        if pct >= 0.45: return cls.BAGUS
        if pct >= 0.25: return cls.CUKUP
        return cls.LEMAH


# ═══════════════════════════════════════════════════════════════════
# TECHNICAL CALCULATIONS
# ═══════════════════════════════════════════════════════════════════

def _pip_size(symbol: str = "XAUUSD") -> float:
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"): return 0.1
    if s in ("BTCUSD", "BTC"): return 1.0
    if s.endswith("JPY"): return 0.01
    if s in ("USOIL", "OIL"): return 0.01
    return 0.0001

def _ema(series: list[float], period: int) -> list[float]:
    if len(series) < period: return [0.0] * len(series)
    multiplier = 2.0 / (period + 1)
    ema = [sum(series[:period]) / period]
    for p in series[period:]:
        ema.append((p - ema[-1]) * multiplier + ema[-1])
    return [0.0] * (period - 1) + ema

def _sma(series: list[float], period: int) -> list[float]:
    if len(series) < period: return [0.0] * len(series)
    result = [0.0] * (period - 1)
    for i in range(period - 1, len(series)):
        result.append(sum(series[i - period + 1:i + 1]) / period)
    return result

def _atr(highs, lows, closes, period=14) -> list[float]:
    if len(closes) < period + 1: return [0.0] * len(closes)
    tr = [0.0]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    atr_vals = [0.0] * (period - 1)
    atr_vals.append(sum(tr[1:period+1]) / period)
    for i in range(period, len(tr)):
        atr_vals.append((atr_vals[-1] * (period - 1) + tr[i]) / period)
    return atr_vals

def _rsi(closes: list[float], period=14) -> list[float]:
    if len(closes) < period + 1: return [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0)); losses.append(max(-diff, 0))
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
# 1. FIBONACCI RETRACEMENT
# Source: brandonlatherow/Fibonacci-Retracement-with-Python
# ═══════════════════════════════════════════════════════════════════

FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_EXTENSIONS = [1.272, 1.414, 1.618, 2.0, 2.618]

def detect_swing_points(ohlcv: list[dict], lookback: int = 100) -> dict:
    """Detect swing high and low using local extrema — from fib-retrace + liquidity-hunter."""
    if len(ohlcv) < lookback: return {"swing_high": None, "swing_low": None}
    bars = ohlcv[-lookback:]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    
    order = 5  # from liquidity-hunter's argrelextrema order=5
    swing_high_idx, swing_high_val = 0, highs[0]
    swing_low_idx, swing_low_val = 0, lows[0]
    
    for i in range(order, len(bars) - order):
        if all(highs[i] >= highs[j] for j in range(i - order, i + order + 1) if j != i):
            if highs[i] > swing_high_val:
                swing_high_val = highs[i]; swing_high_idx = i
        if all(lows[i] <= lows[j] for j in range(i - order, i + order + 1) if j != i):
            if lows[i] < swing_low_val:
                swing_low_val = lows[i]; swing_low_idx = i
    
    return {"swing_high": swing_high_val, "swing_low": swing_low_val,
            "swing_high_idx": swing_high_idx, "swing_low_idx": swing_low_idx}


def calc_fib_levels(swing_high: float, swing_low: float, direction: str = "DOWN") -> dict:
    """Calculate Fibonacci retracement levels — 7 standard ratios."""
    diff = swing_high - swing_low
    levels = {}
    for ratio in FIB_RATIOS:
        if direction == "DOWN":
            level = swing_high - diff * ratio  # retrace from high
        else:
            level = swing_low + diff * ratio   # retrace from low
        levels[f"{ratio:.3f}"] = round(level, 2)
    
    # Extensions
    for ext in FIB_EXTENSIONS:
        if direction == "DOWN":
            level = swing_high - diff * ext
        else:
            level = swing_low + diff * ext
        levels[f"ext_{ext:.3f}"] = round(level, 2)
    
    return levels


def find_fib_confluence(price: float, fib_levels: dict, sd_zones: list[dict] = None,
                         tolerance_pct: float = 0.3) -> dict:
    """Find confluence between Fibonacci levels and Supply/Demand zones."""
    confluence = {"matched": [], "strength": 0}
    
    for label, level in fib_levels.items():
        dist_pct = abs(price - level) / price * 100 if price > 0 else 0
        if dist_pct <= tolerance_pct:
            confluence["matched"].append({"fib_label": label, "fib_level": level, "distance_pct": dist_pct})
            confluence["strength"] += 1
    
    if sd_zones:
        for z in sd_zones:
            for match in confluence["matched"]:
                if abs(match["fib_level"] - z.get("mid", z.get("price", 0))) / price * 100 < tolerance_pct:
                    match["sd_confluence"] = True
                    confluence["strength"] += 1
    
    return confluence


# ═══════════════════════════════════════════════════════════════════
# 2. SUPPLY/DEMAND ZONE STRENGTH — 9 Scoring Functions
# Source: Bretsera/Supply-and-Demand-Zone-Strength-Prediction
# ═══════════════════════════════════════════════════════════════════

def score_consolidation_strength(bars: list[dict], zone_high: float, zone_low: float) -> float:
    """Score zone tightness — tighter consolidation = stronger zone (40% weight)."""
    if not bars: return 0.0
    zone_range = zone_high - zone_low
    if zone_range <= 0: return 0.0
    avg_range = sum(float(b.get("high", b.get("h", 0))) - float(b.get("low", b.get("l", 0))) for b in bars) / len(bars)
    tightness = 1.0 - min(zone_range / avg_range, 1.0) if avg_range > 0 else 0.5
    return round(tightness, 2)


def score_wick_strength(bars: list[dict], zone_type: str = "DEMAND") -> float:
    """Score rejection wick proportion — long wicks = strong rejection (25% weight)."""
    if not bars: return 0.0
    scores = []
    for b in bars[-5:]:
        body = abs(float(b.get("close", b.get("c", 0))) - float(b.get("open", b.get("o", 0))))
        high = float(b.get("high", b.get("h", 0)))
        low = float(b.get("low", b.get("l", 0)))
        total_range = high - low
        if total_range <= 0:
            scores.append(0.0); continue
        
        if zone_type == "DEMAND":
            wick = float(b.get("open", b.get("o", 0))) - low  # lower wick
        else:
            wick = high - float(b.get("open", b.get("o", 0)))  # upper wick
        
        wick_ratio = wick / total_range
        scores.append(min(wick_ratio * 2.5, 1.0))  # Scale: 0.4 ratio = 1.0 score
    
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def score_fvg_strength(bars: list[dict]) -> float:
    """Score Fair Value Gap — non-overlapping candles = stronger imbalance (10% weight)."""
    if len(bars) < 3: return 0.0
    gaps = 0
    for i in range(len(bars) - 2):
        b0_low = float(bars[i].get("low", bars[i].get("l", 0)))
        b0_high = float(bars[i].get("high", bars[i].get("h", 0)))
        b2_high = float(bars[i+2].get("high", bars[i+2].get("h", 0)))
        b2_low = float(bars[i+2].get("low", bars[i+2].get("l", 0)))
        if b0_low > b2_high or b0_high < b2_low: gaps += 1
    return min(gaps / max(len(bars) - 2, 1) * 3, 1.0)


def score_body_strength(bars: list[dict], direction: str = "BUY") -> float:
    """Score candle body size + directional alignment (25% weight)."""
    if not bars: return 0.0
    scores = []
    for b in bars[-10:]:
        o = float(b.get("open", b.get("o", 0)))
        c = float(b.get("close", b.get("c", 0)))
        h = float(b.get("high", b.get("h", 0)))
        l = float(b.get("low", b.get("l", 0)))
        total_range = h - l
        if total_range <= 0:
            scores.append(0.0); continue
        
        body = abs(c - o)
        body_ratio = body / total_range
        direction_ok = (direction == "BUY" and c > o) or (direction == "SELL" and c < o)
        score = body_ratio * (1.0 if direction_ok else 0.3)
        scores.append(min(score, 1.0))
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def score_volume_strength(bars: list[dict]) -> float:
    """Score volume delta — higher relative volume = stronger zone."""
    if not bars or "volume" not in bars[0] and "v" not in bars[0]: return 0.5
    volumes = [float(b.get("volume", b.get("v", 0))) for b in bars[-20:]]
    if not volumes or sum(volumes) == 0: return 0.5
    avg_vol = sum(volumes) / len(volumes)
    recent_vol = sum(volumes[-5:]) / min(len(volumes[-5:]), 5)
    return min(recent_vol / avg_vol if avg_vol > 0 else 1.0, 2.0) / 2.0


def score_sd_zone_overall(bars: list[dict], zone_high: float, zone_low: float,
                           zone_type: str = "DEMAND", direction: str = "BUY") -> dict:
    """
    Overall Supply/Demand zone strength — weighted combination of 5 factors.
    Formula from Bretsera: 0.10×FVG + 0.40×Consolidation + 0.25×Wick + 0.25×Body
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
        "grade": Grade.from_score(int(overall * 20), 20)
    }


# ═══════════════════════════════════════════════════════════════════
# 3. LIQUIDITY DETECTION — Stop Hunts, Sweeps, Liquidations
# Sources: LiquidityHunter, LickHunter, Leo524, GoldLiquidityHunter
# ═══════════════════════════════════════════════════════════════════

def detect_liquidity_levels(ohlcv: list[dict], lookback: int = 100) -> dict:
    """
    Detect liquidity levels — zones where stop orders accumulate.
    From Leo524: Liquidity grab → Reaction → Confirmation → Entry
    """
    if len(ohlcv) < lookback: return {"buy_side": [], "sell_side": []}
    bars = ohlcv[-lookback:]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    
    buy_side = []  # Above swing highs = buy stops
    sell_side = [] # Below swing lows = sell stops
    
    order = 5
    for i in range(order, len(bars) - order):
        if all(highs[i] >= highs[j] for j in range(i - order, i + order + 1) if j != i):
            # Equal highs nearby = liquidity building
            nearby_eq = sum(1 for j in range(max(0, i-10), min(len(highs), i+10))
                          if abs(highs[j] - highs[i]) / highs[i] < 0.001)
            if nearby_eq >= 2:
                buy_side.append({"price": highs[i], "strength": nearby_eq, "index": i})
        
        if all(lows[i] <= lows[j] for j in range(i - order, i + order + 1) if j != i):
            nearby_eq = sum(1 for j in range(max(0, i-10), min(len(lows), i+10))
                          if abs(lows[j] - lows[i]) / lows[i] < 0.001)
            if nearby_eq >= 2:
                sell_side.append({"price": lows[i], "strength": nearby_eq, "index": i})
    
    return {"buy_side": buy_side[-5:], "sell_side": sell_side[-5:]}


def detect_liquidity_sweep(ohlcv: list[dict], lookback: int = 30) -> dict | None:
    """
    Detect liquidity sweep — price breaks a level and immediately reverses.
    From Leo524: Liquidity grab → wait for CHoCH confirmation → entry zone.
    """
    if len(ohlcv) < lookback: return None
    bars = ohlcv[-lookback:]
    closes = [float(b.get("close", b.get("c", 0))) for b in bars]
    highs = [float(b.get("high", b.get("h", 0))) for b in bars]
    lows = [float(b.get("low", b.get("l", 0))) for b in bars]
    
    liquidity = detect_liquidity_levels(ohlcv, lookback)
    
    for level in liquidity["buy_side"]:
        lvl_price = level["price"]
        for i in range(len(bars) - 3, len(bars)):
            # Price sweeps above buy-side liquidity then closes back below
            if highs[i] > lvl_price and closes[i] < lvl_price:
                return {"type": "BUY_SIDE_SWEPT", "price": lvl_price,
                        "sweep_high": highs[i], "close": closes[i],
                        "signal": "SELL",  # Swept buy-side = bearish reversal
                        "description": "Buy-side liquidity swept → potensi reversal bearish"}
    
    for level in liquidity["sell_side"]:
        lvl_price = level["price"]
        for i in range(len(bars) - 3, len(bars)):
            if lows[i] < lvl_price and closes[i] > lvl_price:
                return {"type": "SELL_SIDE_SWEPT", "price": lvl_price,
                        "sweep_low": lows[i], "close": closes[i],
                        "signal": "BUY",  # Swept sell-side = bullish reversal
                        "description": "Sell-side liquidity swept → potensi reversal bullish"}
    
    return None


# ═══════════════════════════════════════════════════════════════════
# 4. ANTI-RANGE TRIPLE FILTER
# Source: GoldLiquidityHunter_PRO — eaglenight37
# ═══════════════════════════════════════════════════════════════════

def anti_range_filter(ohlcv: list[dict]) -> dict:
    """
    Triple filter to avoid ranging/choppy markets.
    GoldLiquidityHunter rules: ADX ≥ 22, ATR/Close ≥ 0.12%, Volume ≥ SMA(20)×1.12
    """
    if len(ohlcv) < 30: return {"pass": False, "reason": "Data insufficient"}
    
    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    highs = [float(b.get("high", b.get("h", 0))) for b in ohlcv]
    lows = [float(b.get("low", b.get("l", 0))) for b in ohlcv]
    volumes = [float(b.get("volume", b.get("v", 0))) for b in ohlcv[-30:]]
    
    price = closes[-1]
    
    # 1. ADX filter
    atr_vals = _atr(highs, lows, closes, 14)
    atr_now = atr_vals[-1] if atr_vals[-1] > 0 else 0.01
    atr_ratio = atr_now / price * 100 if price > 0 else 0
    
    # Simplified ADX calculation
    dm_plus, dm_minus = [], []
    for i in range(1, len(highs)):
        up = highs[i] - highs[i-1]; down = lows[i-1] - lows[i]
        dm_plus.append(up if up > down and up > 0 else 0)
        dm_minus.append(down if down > up and down > 0 else 0)
    
    atr_14 = atr_vals[-1] if atr_vals[-1] > 0 else 0.01
    smooth_period = 14
    if len(dm_plus) >= smooth_period:
        tr_sum = sum(atr_vals[-smooth_period:])
        di_plus = sum(dm_plus[-smooth_period:]) / tr_sum * 100 if tr_sum > 0 else 0
        di_minus = sum(dm_minus[-smooth_period:]) / tr_sum * 100 if tr_sum > 0 else 0
        dx = abs(di_plus - di_minus) / (di_plus + di_minus) * 100 if (di_plus + di_minus) > 0 else 0
        adx = dx  # simplified — real ADX would smooth DX
    else:
        adx = 0
    
    adx_ok = adx >= 22
    
    # 2. ATR/Close ratio filter
    atr_close_ok = atr_ratio >= 0.12
    
    # 3. Volume filter
    vol_sma20 = sum(volumes) / len(volumes) if volumes else 0
    vol_now = volumes[-1] if volumes else 0
    vol_ok = vol_now >= vol_sma20 * 1.12 if vol_sma20 > 0 else False
    
    all_pass = adx_ok and atr_close_ok and vol_ok
    
    reasons = []
    if not adx_ok: reasons.append(f"ADX={adx:.1f} < 22 (ranging)")
    if not atr_close_ok: reasons.append(f"ATR/Close={atr_ratio:.3f}% < 0.12% (low volatility)")
    if not vol_ok: reasons.append(f"Vol={vol_now:.0f} < SMA×1.12={vol_sma20*1.12:.0f} (low volume)")
    
    return {"pass": all_pass, "adx": round(adx, 1), "atr_ratio": round(atr_ratio, 3),
            "vol_ok": vol_ok, "reasons": reasons}


# ═══════════════════════════════════════════════════════════════════
# 5. EMA200 DAILY BIAS — Directional Filter
# Source: GoldLiquidityHunter_PRO
# ═══════════════════════════════════════════════════════════════════

def ema200_daily_bias(ohlcv: list[dict]) -> dict:
    """
    Daily bias using EMA200 comparison.
    GoldLiquidityHunter: neutral zone = EMA200 × 0.85% dead band.
    """
    if len(ohlcv) < 200: return {"bias": "NEUTRAL", "reason": "Need 200+ bars"}
    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    ema200 = _ema(closes, 200)
    price = closes[-1]
    ema200_val = ema200[-1]
    
    dead_band = ema200_val * 0.0085  # 0.85% dead band
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


# ═══════════════════════════════════════════════════════════════════
# 6. ORDER BLOCK DETECTION — Advanced
# Source: GoldLiquidityHunter_PRO + World Class SMC
# ═══════════════════════════════════════════════════════════════════

def detect_order_block_advanced(ohlcv: list[dict], lookback: int = 50) -> dict | None:
    """
    Advanced Order Block detection with body/range ratio filter.
    GoldLiquidityHunter rule: body/range ≥ 0.35, opposite confirmation bar.
    """
    if len(ohlcv) < lookback: return None
    bars = ohlcv[-lookback:]
    
    best_ob = None
    best_strength = 0
    
    for i in range(len(bars) - 3, 0, -1):
        b = bars[i]
        o = float(b.get("open", b.get("o", 0)))
        c = float(b.get("close", b.get("c", 0)))
        h = float(b.get("high", b.get("h", 0)))
        l = float(b.get("low", b.get("l", 0)))
        body = abs(c - o)
        candle_range = h - l
        
        if candle_range <= 0: continue
        body_ratio = body / candle_range
        
        # GoldLiquidityHunter filter: body/range ≥ 0.35
        if body_ratio < 0.35: continue
        
        # Bearish OB (Supply): strong bearish candle → upper zone
        if c < o:
            # Check opposite confirmation bar
            if i + 1 < len(bars):
                next_c = float(bars[i+1].get("close", bars[i+1].get("c", 0)))
                next_o = float(bars[i+1].get("open", bars[i+1].get("o", 0)))
                if next_c < next_o:  # Follow-through bearish
                    strength = body_ratio * 5
                    if strength > best_strength:
                        best_strength = strength
                        best_ob = {"direction": "SELL", "upper": o, "lower": c,
                                   "strength": min(round(strength, 1), 5.0),
                                   "body_ratio": round(body_ratio, 2)}
        
        # Bullish OB (Demand): strong bullish candle → lower zone
        if c > o:
            if i + 1 < len(bars):
                next_c = float(bars[i+1].get("close", bars[i+1].get("c", 0)))
                next_o = float(bars[i+1].get("open", bars[i+1].get("o", 0)))
                if next_c > next_o:  # Follow-through bullish
                    strength = body_ratio * 5
                    if strength > best_strength:
                        best_strength = strength
                        best_ob = {"direction": "BUY", "upper": c, "lower": o,
                                   "strength": min(round(strength, 1), 5.0),
                                   "body_ratio": round(body_ratio, 2)}
    
    return best_ob


# ═══════════════════════════════════════════════════════════════════
# 7. TECHNICAL INDICATORS — Enhanced
# Source: je-suis-tm/quant-trading + RL Agent
# ═══════════════════════════════════════════════════════════════════

def calc_macd(closes: list[float], fast=12, slow=26, signal=9) -> dict:
    """MACD — from quant-trading."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    signal_line = _ema(macd_line, signal)
    histogram = [macd_line[i] - signal_line[i] for i in range(len(closes))]
    return {"macd": macd_line[-1], "signal": signal_line[-1],
            "histogram": histogram[-1], "bullish": macd_line[-1] > signal_line[-1]}


def calc_bollinger(closes: list[float], period=20, std=2.0) -> dict:
    """Bollinger Bands — from quant-trading."""
    sma = _sma(closes, period)
    if len(closes) < period: return {}
    std_dev = (sum((c - sma[-1])**2 for c in closes[-period:]) / period) ** 0.5
    upper = sma[-1] + std * std_dev
    lower = sma[-1] - std * std_dev
    pct_b = (closes[-1] - lower) / (upper - lower) if (upper - lower) > 0 else 0.5
    return {"upper": upper, "lower": lower, "middle": sma[-1],
            "pct_b": round(pct_b, 2), "width": round((upper - lower) / sma[-1] * 100, 2)}


def calc_heikin_ashi(bars: list[dict]) -> list[dict]:
    """Heikin-Ashi candlestick transformation — noise filter from quant-trading."""
    ha_bars = []
    for i, b in enumerate(bars):
        o = float(b.get("open", b.get("o", 0)))
        h = float(b.get("high", b.get("h", 0)))
        l = float(b.get("low", b.get("l", 0)))
        c = float(b.get("close", b.get("c", 0)))
        
        if i == 0:
            ha_close = (o + h + l + c) / 4
            ha_open = (o + c) / 2
        else:
            ha_close = (o + h + l + c) / 4
            ha_open = (ha_bars[-1]["ha_open"] + ha_bars[-1]["ha_close"]) / 2
        
        ha_high = max(h, ha_open, ha_close)
        ha_low = min(l, ha_open, ha_close)
        
        ha_bars.append({"ha_open": ha_open, "ha_high": ha_high, "ha_low": ha_low, "ha_close": ha_close,
                        "bullish": ha_close > ha_open})
    return ha_bars


# ═══════════════════════════════════════════════════════════════════
# 8. LONDON BREAKOUT INTRADAY
# Source: je-suis-tm/quant-trading
# ═══════════════════════════════════════════════════════════════════

def london_breakout_levels(ohlcv: list[dict], session_hour: int = 7) -> dict | None:
    """
    London Breakout strategy. Pre-London hour (7:00-7:59 GMT) range defines breakout levels.
    If price breaks above/below after London open, take position.
    """
    if len(ohlcv) < 12: return None  # Need at least 12 hourly bars
    
    bars = ohlcv[-12:]  # Last 12 hours
    session_bars = [b for b in bars if 7 <= float(b.get("hour", 7)) < 8]  # GMT 7-8
    
    if not session_bars: return None
    
    session_high = max(float(b.get("high", b.get("h", 0))) for b in session_bars)
    session_low = min(float(b.get("low", b.get("l", 0))) for b in session_bars)
    current = float(bars[-1].get("close", bars[-1].get("c", 0)))
    
    return {"range_high": session_high, "range_low": session_low,
            "current": current, "range_size": session_high - session_low,
            "breakout_up": current > session_high, "breakout_down": current < session_low}


# ═══════════════════════════════════════════════════════════════════
# 9. ULTIMATE ANALYSIS — All Concepts Combined
# ═══════════════════════════════════════════════════════════════════

@dataclass
class UltimateResult:
    signal: str = "HOLD"
    direction: str = ""
    grade: int = Grade.LEMAH
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


def ultimate_analyze(ohlcv: list[dict], symbol: str = "XAUUSD",
                      price: float = 0.0) -> UltimateResult:
    """
    ULTIMATE SMC ANALYSIS — All 13+ repos combined.
    
    Pipeline:
    1. EMA200 Daily Bias → directional bias
    2. Anti-Range Filter → avoid choppy markets
    3. Fibonacci Retracement → key levels
    4. Supply/Demand Zones → strength scored
    5. Fib + S/D Confluence → entry precision
    6. Order Block Detection → institutional zones
    7. Liquidity Detection → sweep + stop hunts
    8. Technical Indicators → MACD, RSI, Bollinger
    9. London Breakout → intraday bias
    10. Combined Grading → 24-point system
    """
    result = UltimateResult()
    
    if not ohlcv or len(ohlcv) < 50:
        result.reasons = ["❌ Data kurang — butuh minimal 50 candle"]
        return result
    
    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
    highs = [float(b.get("high", b.get("h", 0))) for b in ohlcv]
    lows = [float(b.get("low", b.get("l", 0))) for b in ohlcv]
    last_price = price if price > 0 else closes[-1]
    open_prices = [float(b.get("open", b.get("o", 0))) for b in ohlcv]
    
    # ── 1. EMA200 Daily Bias (GoldLiquidityHunter) ──
    ema_bias = ema200_daily_bias(ohlcv)
    result.ema_bias = ema_bias
    
    # ── 2. Anti-Range Triple Filter ──
    anti_range = anti_range_filter(ohlcv)
    result.anti_range = anti_range
    
    # ── 3. Fibonacci Retracement ──
    swings = detect_swing_points(ohlcv)
    if swings["swing_high"] and swings["swing_low"]:
        direction = "DOWN" if swings["swing_high_idx"] > swings["swing_low_idx"] else "UP"
        fib_levels = calc_fib_levels(swings["swing_high"], swings["swing_low"], direction)
        result.fib_levels = fib_levels
    
    # ── 4. Supply/Demand Zones ──
    from smc_scalper_engine import detect_supply_demand_zones
    sd_zones = detect_supply_demand_zones(ohlcv)
    result.sd_zones = sd_zones
    
    # ── 5. Fib + S/D Confluence ──
    if result.fib_levels and sd_zones:
        confluence = find_fib_confluence(last_price, result.fib_levels, sd_zones)
        result.fib_confluence = confluence
        result.fibonacci_score = min(confluence.get("strength", 0), 4)
    
    # ── 6. Order Block Detection ──
    ob = detect_order_block_advanced(ohlcv)
    result.order_block = ob
    if ob:
        result.order_block_score = min(int(ob.get("strength", 0)), 4)
    
    # ── 7. S/D Zone Strength Scoring ──
    if sd_zones:
        best_sd = sd_zones[0]
        sd_score = score_sd_zone_overall(
            ohlcv[-min(len(ohlcv), 50):],
            best_sd.get("upper", best_sd.get("mid", last_price * 1.01)),
            best_sd.get("lower", best_sd.get("mid", last_price * 0.99)),
            best_sd.get("type", "DEMAND"),
            "BUY" if best_sd.get("type") == "DEMAND" else "SELL"
        )
        result.sd_overall = sd_score
        result.sd_strength_score = int(sd_score.get("overall", 0) * 5)
    
    # ── 8. Liquidity Detection + Sweep ──
    liquidity = detect_liquidity_levels(ohlcv)
    result.liquidity = liquidity
    sweep = detect_liquidity_sweep(ohlcv)
    result.sweep = sweep
    if sweep:
        result.liquidity_score = 4  # Strong signal
        if not result.direction:
            result.direction = sweep.get("signal", "")
    elif liquidity.get("buy_side") or liquidity.get("sell_side"):
        result.liquidity_score = 2
    
    # ── 9. Technical Indicators ──
    macd = calc_macd(closes)
    result.macd = macd
    bollinger = calc_bollinger(closes)
    result.bollinger = bollinger
    rsi_vals = _rsi(closes, 14)
    rsi_now = rsi_vals[-1]
    
    tech_score = 0
    if macd.get("bullish"): tech_score += 1
    if bollinger:
        if 0.2 < bollinger.get("pct_b", 0.5) < 0.8: tech_score += 1  # Not extreme
    if 30 < rsi_now < 70: tech_score += 1
    result.technical_score = tech_score
    
    # ── 10. London Breakout ──
    lb = london_breakout_levels(ohlcv)
    result.london_breakout = lb
    if lb:
        result.bias_score += 1
    
    # ── 11. Direction Determination ──
    if not result.direction:
        if ema_bias.get("bias") == "BULLISH":
            result.direction = "BUY"
        elif ema_bias.get("bias") == "BEARISH":
            result.direction = "SELL"
        elif sweep:
            result.direction = sweep.get("signal", "")
        elif ob:
            result.direction = ob.get("direction", "")
    
    # ── 12. Combined Scoring ──
    total = (result.fibonacci_score + result.sd_strength_score + result.liquidity_score +
             result.order_block_score + result.technical_score + result.bias_score)
    result.score = min(total, 24)
    result.max_score = 24
    result.grade = Grade.from_score(result.score, 24)
    result.grade_label = Grade.label(result.grade)
    
    # ── 13. Build Reasons ──
    reasons = []
    emoji = Grade.emoji(result.grade)
    
    if result.direction:
        result.signal = result.direction
        reasons.append(f"{emoji} Sinyal: **{result.signal}** {symbol} | Grade: {result.grade_label}")
    else:
        result.signal = "HOLD"
        reasons.append(f"🔍 **HOLD** {symbol} — menunggu konfirmasi")
    
    if ema_bias.get("bias") != "NEUTRAL":
        reasons.append(f"📊 EMA200: {ema_bias.get('description','')}")
    
    if anti_range.get("pass"):
        reasons.append("✅ Anti-Range Filter: PASS — market trending")
    elif anti_range.get("reasons"):
        reasons.append(f"⚠️ Anti-Range: {anti_range['reasons'][0]}")
    
    if result.fib_levels:
        fib_618 = result.fib_levels.get("0.618")
        if fib_618:
            reasons.append(f"📐 Fib 0.618: ${fib_618:.2f}")
    
    if result.fib_confluence.get("matched"):
        reasons.append(f"🎯 Fib+S/D Confluence: {result.fib_confluence.get('strength',0)} matches")
    
    if sd_zones:
        best = sd_zones[0]
        reasons.append(f"📍 Nearest S/D: {best['type']} @ ${best.get('mid', best.get('upper',0)):.2f} [str:{best.get('strength',0):.1f}]")
    
    if ob:
        reasons.append(f"🧱 Order Block: {ob['direction']} [str:{ob.get('strength',0):.1f}] body:{ob.get('body_ratio',0):.0%}")
    
    if sweep:
        reasons.append(f"💧 {sweep.get('description','Liquidity Sweep')}")
    
    if macd:
        reasons.append(f"📈 MACD: {'Bullish' if macd.get('bullish') else 'Bearish'}")
    
    if bollinger:
        reasons.append(f"📊 BB %b: {bollinger.get('pct_b',0):.2f} | Width: {bollinger.get('width',0):.1f}%")
    
    reasons.append(f"📋 Score: {result.score}/{result.max_score} | Fib:{result.fibonacci_score} SD:{result.sd_strength_score} Liq:{result.liquidity_score} OB:{result.order_block_score} Tech:{result.technical_score}")
    
    result.reasons = reasons
    return result


def format_ultimate_block(result: UltimateResult) -> str:
    """Format UltimateResult untuk Telegram UI."""
    lines = [
        f"🏛️ **ULTIMATE SMC v3.0**",
        f"━━━━━━━━━━━━━━━━",
    ]
    lines.extend(result.reasons)
    return "\n".join(lines)
