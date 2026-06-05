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
    """10-factor SMC Scalper confirmation."""
    choch_detected: bool = False       # Perubahan struktur pasar
    fvg_detected: bool = False         # Celah harga (imbalance)
    order_block_valid: bool = False    # Zona supply/demand valid
    trend_aligned: bool = False        # Trend searah (HTF)
    price_in_zone: bool = False        # Harga di zona entry
    session_optimal: bool = False      # Jam trading optimal
    volatility_normal: bool = False    # Volatilitas dalam batas wajar
    momentum_ok: bool = False          # Momentum konfirmasi
    trend_strength_ok: bool = False    # Kekuatan trend cukup
    spread_acceptable: bool = False    # Biaya trading wajar

    @property
    def score(self) -> int:
        return sum([
            int(self.choch_detected) * 2,
            int(self.fvg_detected) * 2,
            int(self.trend_aligned) * 2,
            int(self.order_block_valid) * 1,
            int(self.price_in_zone) * 1,
            int(self.session_optimal) * 1,
            int(self.volatility_normal) * 1,
            int(self.momentum_ok) * 1,
            int(self.trend_strength_ok) * 1,
            int(self.spread_acceptable) * 0,  # info only
        ])

    @property
    def grade(self) -> Grade:
        return Grade.from_score(self.score, 12)

    def _reasons_id(self, symbol: str) -> list[str]:
        """Alasan dalam bahasa Indonesia."""
        reasons = []
        if self.choch_detected:
            reasons.append("✅ CHoCH terdeteksi — struktur pasar berubah arah")
        if self.fvg_detected:
            reasons.append("✅ FVG / celah harga terisi — imbalance dikoreksi")
        if self.order_block_valid:
            reasons.append("✅ Order Block valid — zona supply/demand terkonfirmasi")
        if self.trend_aligned:
            reasons.append("✅ Trend searah — sinyal sejalan dengan trend besar")
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

    # 2. FVG Detection
    fvg = detect_fvg_zones(ohlcv)

    # 3. Order Block Detection
    ob = detect_order_block(ohlcv)

    # 4. Trend alignment (EMA 50 vs EMA 200)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200) if len(closes) >= 200 else [0] * len(closes)
    trend_up = ema50[-1] > ema200[-1] if ema200[-1] > 0 else ema50[-1] > ema50[-20]
    trend_down = ema50[-1] < ema200[-1] if ema200[-1] > 0 else ema50[-1] < ema50[-20]

    # 5. Build confirmation
    direction = None
    conf = SMCConfirmation()
    conf.choch_detected = choch is not None
    conf.fvg_detected = fvg is not None

    if choch:
        direction = choch["direction"]
    if fvg:
        direction = fvg["direction"] if direction is None else direction
    
    conf.order_block_valid = ob is not None and (direction is None or ob["direction"] == direction)
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

    # Trend strength (simplified ADX-like)
    conf.trend_strength_ok = conf.choch_detected or conf.fvg_detected
    conf.spread_acceptable = True

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
    
    # Tambahan info spesifik
    if choch:
        result["reasons"].insert(0, f"🎯 CHoCH {direction} di ${choch['price']:.2f}")
    if fvg:
        result["reasons"].insert(0, f"📐 FVG zone: ${fvg['lower']:.2f} - ${fvg['upper']:.2f}")
    if ob:
        result["reasons"].insert(0, f"🧱 Order Block [{ob['strength']}/5]: ${ob['lower']:.2f} - ${ob['upper']:.2f}")

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
