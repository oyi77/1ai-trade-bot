"""
crt_tbs_engine.py — Candle Range Theory + Time-Based Strategy
Adapted from lordgaruda/XAU-60 | v1.0

CRT/TBS: Uses Asian session range as objective reference.
Detects liquidity sweeps (manipulation) during London/NY killzones.
Entry when price sweeps beyond range and closes back inside with confirmation.

Works on: forex, crypto, commodities (any asset with 24h or near-24h trading).
Not recommended for: stocks (fixed exchange hours break the range model).
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Constants ──────────────────────────────────────────────────────
WIB = timezone(timedelta(hours=7))
UTC = timezone.utc


# ── Enums ───────────────────────────────────────────────────────────

class Killzone(Enum):
    NONE = 0
    LONDON = 1       # 07:00-09:00 UTC
    NEW_YORK = 2     # 13:00-15:00 UTC
    LONDON_CLOSE = 3 # 15:00-17:00 UTC (optional)


class SweepQuality(Enum):
    NONE = 0
    WEAK = 1         # Small sweep, quick return
    MODERATE = 2     # Decent sweep with momentum
    STRONG = 3       # Strong sweep with displacement
    PERFECT = 4      # Textbook manipulation


class SignalGrade(Enum):
    """Universal signal quality grading (A+ → D)."""
    A_PLUS = 5   # All confirmations aligned — textbook setup
    A = 4        # Strong signal
    B = 3        # Good — minimum for auto-trade
    C = 2        # Acceptable but risky
    D = 1        # Weak — skip


# ── Data Classes ────────────────────────────────────────────────────

@dataclass
class AsianRange:
    """Asian session (00:00-06:00 UTC) price range."""
    high: float = 0.0
    low: float = 0.0
    mid: float = 0.0
    size_pips: float = 0.0
    date: str = ""
    valid: bool = False


@dataclass
class SweepSignal:
    """Detected manipulation sweep beyond Asian range."""
    detected: bool = False
    direction: str = ""       # "BUY" (sweep low → bullish) or "SELL" (sweep high → bearish)
    sweep_price: float = 0.0
    sweep_time: str = ""
    quality: SweepQuality = SweepQuality.NONE
    depth_pips: float = 0.0
    candle_rejection: bool = False  # Wick rejection confirming the sweep
    volume_spike: bool = False
    quick_reversal: bool = False
    price_closed_inside: bool = False  # Close back inside Asian range


@dataclass
class CRTConfirmation:
    """Multi-factor CRT/TBS confirmation scoring."""
    # Core confirmations
    range_valid: bool = False
    in_killzone: bool = False
    sweep_detected: bool = False
    sweep_quality: SweepQuality = SweepQuality.NONE
    price_closed_inside: bool = False  # Price closed back inside Asian range
    htf_bias_aligned: bool = False     # Higher timeframe trend agrees
    candle_rejection: bool = False
    volume_confirmed: bool = False
    atr_filter_passed: bool = False
    not_overextended: bool = False

    @property
    def score(self) -> int:
        return sum([
            int(self.range_valid) * 1,
            int(self.in_killzone) * 1,
            int(self.sweep_detected) * 2,
            self.sweep_quality.value,  # 0-4
            int(self.price_closed_inside) * 2,
            int(self.htf_bias_aligned) * 2,
            int(self.candle_rejection) * 1,
            int(self.volume_confirmed) * 1,
            int(self.atr_filter_passed) * 1,
            int(self.not_overextended) * 1,
        ])

    @property
    def grade(self) -> SignalGrade:
        s = self.score
        if s >= 14: return SignalGrade.A_PLUS
        if s >= 11: return SignalGrade.A
        if s >= 8:  return SignalGrade.B
        if s >= 5:  return SignalGrade.C
        return SignalGrade.D

    @property
    def grade_label(self) -> str:
        return {SignalGrade.A_PLUS: "A+", SignalGrade.A: "A",
                SignalGrade.B: "B", SignalGrade.C: "C", SignalGrade.D: "D"}[self.grade]


# ── Engine ──────────────────────────────────────────────────────────

def _pip_size(symbol: str = "XAUUSD") -> float:
    """Return pip value for the symbol."""
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"):
        return 0.1   # Gold: 1 pip = $0.10
    if s in ("BTCUSD", "BTC"):
        return 1.0   # Bitcoin: 1 pip = $1
    if s.endswith("JPY"):
        return 0.01
    if s in ("USOIL", "OIL", "CL"):
        return 0.01
    return 0.0001    # Default forex


def _to_pips(price_diff: float, symbol: str = "XAUUSD") -> float:
    """Convert price difference to pips."""
    ps = _pip_size(symbol)
    if ps <= 0:
        return 0.0
    return abs(price_diff) / ps


def get_current_killzone(utc_hour: int) -> Killzone:
    """Determine current killzone based on UTC hour."""
    if 7 <= utc_hour < 9:
        return Killzone.LONDON
    if 13 <= utc_hour < 15:
        return Killzone.NEW_YORK
    if 15 <= utc_hour < 17:
        return Killzone.LONDON_CLOSE
    return Killzone.NONE


def calculate_asian_range(ohlcv: list[dict], target_date_utc: str = "") -> AsianRange:
    """
    Calculate Asian session range (00:00-06:00 UTC) from OHLCV data.
    
    Args:
        ohlcv: list of dicts with keys: timestamp (ISO str or datetime), high, low
        target_date_utc: date string "YYYY-MM-DD" for the Asian session (default: today)
    
    Returns:
        AsianRange with high, low, mid, size_pips
    """
    if not ohlcv or len(ohlcv) < 6:
        return AsianRange(valid=False)

    try:
        # Determine target date
        if not target_date_utc:
            now_utc = datetime.now(UTC)
            # Asian session 00-06 UTC; if current UTC < 6, use today; else today's session already passed
            target_date_utc = now_utc.strftime("%Y-%m-%d")

        asian_start = f"{target_date_utc}T00:00:00+00:00"
        asian_end = f"{target_date_utc}T06:00:00+00:00"

        asian_bars = []
        for b in ohlcv:
            ts = b.get("timestamp", b.get("t", ""))
            if isinstance(ts, (int, float)):
                # Unix timestamp
                dt = datetime.fromtimestamp(ts, UTC)
            elif isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            elif hasattr(ts, "isoformat"):
                dt = ts
            else:
                continue
            
            if asian_start <= dt.isoformat()[:19] + "+00:00" <= asian_end:
                asian_bars.append(b)

        if len(asian_bars) < 3:
            return AsianRange(valid=False)

        highs = [float(b.get("high", b.get("h", 0))) for b in asian_bars]
        lows = [float(b.get("low", b.get("l", 0))) for b in asian_bars]
        
        range_high = max(highs)
        range_low = min(lows)
        range_size = range_high - range_low

        if range_size <= 0:
            return AsianRange(valid=False)

        return AsianRange(
            high=range_high,
            low=range_low,
            mid=(range_high + range_low) / 2,
            size_pips=_to_pips(range_size),
            date=target_date_utc,
            valid=True,
        )
    except Exception:
        return AsianRange(valid=False)


def detect_sweep(
    ohlcv: list[dict],
    asian_range: AsianRange,
    symbol: str = "XAUUSD",
    min_sweep_pips: float = 3.0,
    max_sweep_pips: float = 50.0,
    require_rejection: bool = True,
) -> SweepSignal:
    """
    Detect manipulation sweep beyond the Asian range.
    
    A sweep occurs when price moves beyond the Asian range high/low
    and then closes back inside the range (liquidity grab).
    
    Returns SweepSignal with direction, quality, and depth.
    """
    if not asian_range.valid or not ohlcv:
        return SweepSignal()

    recent = ohlcv[-20:]  # Check last 20 bars for sweep activity
    if len(recent) < 3:
        return SweepSignal()

    sweep_low = None   # Price went below range low → bullish sweep
    sweep_high = None  # Price went above range high → bearish sweep
    last_close = float(recent[-1].get("close", recent[-1].get("c", 0)))

    for b in recent:
        h = float(b.get("high", b.get("h", 0)))
        l = float(b.get("low", b.get("l", 0)))
        c = float(b.get("close", b.get("c", 0)))

        if l < asian_range.low and not sweep_low:
            depth = _to_pips(asian_range.low - l, symbol)
            if min_sweep_pips <= depth <= max_sweep_pips:
                # Check for rejection (wick) — close back above range low
                if c > asian_range.low:
                    sweep_low = SweepSignal(
                        detected=True,
                        direction="BUY",  # Sweep low = trap sellers → bullish
                        sweep_price=l,
                        depth_pips=depth,
                        candle_rejection=c > asian_range.low,
                    )

        if h > asian_range.high and not sweep_high:
            depth = _to_pips(h - asian_range.high, symbol)
            if min_sweep_pips <= depth <= max_sweep_pips:
                if c < asian_range.high:
                    sweep_high = SweepSignal(
                        detected=True,
                        direction="SELL",  # Sweep high = trap buyers → bearish
                        sweep_price=h,
                        depth_pips=depth,
                        candle_rejection=c < asian_range.high,
                    )

    # Pick the most recent sweep
    sweep = sweep_low or sweep_high
    if not sweep:
        return SweepSignal()

    # Evaluate sweep quality
    if require_rejection and not sweep.candle_rejection:
        sweep.quality = SweepQuality.WEAK
    elif sweep.depth_pips < 5:
        sweep.quality = SweepQuality.WEAK
    elif sweep.depth_pips < 10:
        sweep.quality = SweepQuality.MODERATE
    elif sweep.depth_pips < 20:
        sweep.quality = SweepQuality.STRONG
    else:
        sweep.quality = SweepQuality.PERFECT

    # Check price closed back inside
    sweep.price_closed_inside = (
        asian_range.low <= last_close <= asian_range.high
    )

    return sweep


def calculate_ema(series: list[float], period: int) -> list[float]:
    """Simple EMA calculation."""
    if len(series) < period:
        return [0.0] * len(series)
    multiplier = 2.0 / (period + 1)
    ema = [sum(series[:period]) / period]
    for price in series[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return [0.0] * (period - 1) + ema


def analyze_crt_setup(
    ohlcv: list[dict],
    symbol: str = "XAUUSD",
    min_range_pips: float = 20.0,
    max_range_pips: float = 100.0,
    min_sweep_pips: float = 3.0,
    min_quality: str = "B",
    use_htf_bias: bool = True,
) -> dict[str, Any]:
    """
    Main CRT/TBS analysis entry point.
    
    Args:
        ohlcv: OHLCV bars (dict with timestamp/open/high/low/close keys)
        symbol: Trading symbol
        min_range_pips: Minimum Asian range size in pips
        max_range_pips: Maximum Asian range size in pips
        min_sweep_pips: Minimum sweep depth in pips
        min_quality: Minimum grade to accept (A+, A, B, C, D)
        use_htf_bias: Use EMA50 to determine higher timeframe bias
    
    Returns:
        dict with: signal (BUY/SELL/HOLD), grade, score, confirmations, asian_range, sweep
    """
    result: dict[str, Any] = {
        "signal": "HOLD",
        "grade": "D",
        "score": 0,
        "grade_label": "D",
        "confirmations": None,
        "asian_range": None,
        "sweep": None,
        "reasoning": "",
    }

    if not ohlcv or len(ohlcv) < 20:
        result["reasoning"] = "Insufficient data (< 20 bars)"
        return result

    # 1. Current killzone
    now_utc = datetime.now(UTC)
    kz = get_current_killzone(now_utc.hour)
    if kz == Killzone.NONE:
        result["reasoning"] = f"Outside killzone (UTC {now_utc.hour}:00)"
        return result

    # 2. Asian range
    target_date = now_utc.strftime("%Y-%m-%d")
    asian = calculate_asian_range(ohlcv, target_date)
    if not asian.valid:
        result["reasoning"] = "Invalid Asian range"
        return result
    if asian.size_pips < min_range_pips:
        result["reasoning"] = f"Range too small ({asian.size_pips:.0f} < {min_range_pips} pips)"
        return result
    if asian.size_pips > max_range_pips:
        result["reasoning"] = f"Range too large ({asian.size_pips:.0f} > {max_range_pips} pips)"
        return result

    # 3. Sweep detection
    sweep = detect_sweep(ohlcv, asian, symbol, min_sweep_pips)
    if not sweep.detected:
        result["reasoning"] = "No sweep detected"
        result["asian_range"] = {"high": asian.high, "low": asian.low, "mid": asian.mid, "size_pips": asian.size_pips}
        return result

    # 4. HTF Bias (EMA50)
    htf_bias_aligned = False
    htf_direction = "NEUTRAL"
    if use_htf_bias and len(ohlcv) >= 50:
        try:
            closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
            ema50 = calculate_ema(closes, 50)
            if ema50 and ema50[-1] > 0:
                # Bias = price vs EMA50
                last_close = closes[-1]
                ema_val = ema50[-1]
                # Also check slope
                if len(ema50) >= 10:
                    slope = ema50[-1] - ema50[-10]
                    if slope > 0:
                        htf_direction = "BULLISH"
                        htf_bias_aligned = (sweep.direction == "BUY")
                    elif slope < 0:
                        htf_direction = "BEARISH"
                        htf_bias_aligned = (sweep.direction == "SELL")
        except Exception:
            pass

    # 5. Build confirmation
    conf = CRTConfirmation(
        range_valid=asian.valid,
        in_killzone=True,
        sweep_detected=sweep.detected,
        sweep_quality=sweep.quality,
        price_closed_inside=sweep.price_closed_inside,
        htf_bias_aligned=htf_bias_aligned,
        candle_rejection=sweep.candle_rejection,
        volume_confirmed=sweep.volume_spike,
        atr_filter_passed=True,  # Simplified — range size already checked
        not_overextended=sweep.depth_pips < 50,
    )

    # 6. Quality filter
    grade = conf.grade
    min_grade_map = {"A+": SignalGrade.A_PLUS, "A": SignalGrade.A,
                     "B": SignalGrade.B, "C": SignalGrade.C, "D": SignalGrade.D}
    required = min_grade_map.get(min_quality, SignalGrade.B)

    if grade.value < required.value:
        result["signal"] = "HOLD"
        result["grade"] = conf.grade_label
        result["score"] = conf.score
        result["confirmations"] = conf
        result["asian_range"] = {"high": asian.high, "low": asian.low, "mid": asian.mid, "size_pips": asian.size_pips}
        result["sweep"] = {"direction": sweep.direction, "depth_pips": sweep.depth_pips,
                           "quality": sweep.quality.name, "rejection": sweep.candle_rejection}
        result["reasoning"] = f"CRT grade {conf.grade_label} < min {min_quality} — skipped"
        return result

    # 7. Generate signal
    result["signal"] = sweep.direction
    result["grade"] = conf.grade_label
    result["score"] = conf.score
    result["grade_label"] = conf.grade_label
    result["confirmations"] = conf
    result["asian_range"] = {"high": asian.high, "low": asian.low, "mid": asian.mid, "size_pips": asian.size_pips}
    result["sweep"] = {"direction": sweep.direction, "depth_pips": sweep.depth_pips,
                       "quality": sweep.quality.name, "rejection": sweep.candle_rejection}
    
    kz_name = {Killzone.LONDON: "London", Killzone.NEW_YORK: "NY", Killzone.LONDON_CLOSE: "London Close"}[kz]
    result["reasoning"] = (
        f"CRT {conf.grade_label} | {kz_name} Killzone | "
        f"Asian range: {asian.size_pips:.0f} pips | "
        f"Sweep {sweep.direction} ({sweep.depth_pips:.0f} pips {sweep.quality.name}) | "
        f"HTF: {htf_direction}{' ✓' if htf_bias_aligned else ''}"
    )

    return result


def grade_to_emoji(grade_label: str) -> str:
    """Convert grade label to emoji."""
    return {"A+": "⭐", "A": "🟢", "B": "🔵", "C": "🟡", "D": "⚪️"}.get(grade_label, "⚪️")


def grade_to_label_id(grade_label: str) -> str:
    """Convert A+/A/B/C/D to Indonesian label."""
    return {"A+": "SANGAT KUAT ⭐", "A": "KUAT 🟢", "B": "BAGUS 🔵",
            "C": "CUKUP 🟡", "D": "LEMAH ⚪️"}.get(grade_label, grade_label)


# ── Integration helpers ─────────────────────────────────────────────

def format_crt_block(crt_result: dict) -> str:
    """Format CRT/TBS result as a Telegram-safe text block."""
    if not crt_result or crt_result.get("signal") == "HOLD":
        if crt_result and crt_result.get("asian_range"):
            ar = crt_result["asian_range"]
            return (
                f"\n━━━━━━━━━━━━━━━━\n"
                f"🏯 <b>CRT/TBS:</b> No setup | Asian range: {ar['size_pips']:.0f} pips\n"
                f"📐 Range: H={ar['high']:.2f} L={ar['low']:.2f} Mid={ar['mid']:.2f}"
            )
        return ""

    ar = crt_result["asian_range"]
    sw = crt_result["sweep"]
    emoji = grade_to_emoji(crt_result["grade_label"])
    sig_emoji = "🟢" if crt_result["signal"] == "BUY" else "🔴"

    block = (
        f"\n━━━━━━━━━━━━━━━━\n"
        f"🏯 <b>CRT/TBS Setup</b> {emoji} {grade_to_label_id(crt_result['grade_label'])}\n"
        f"{sig_emoji} {crt_result['signal']} | Score: {crt_result['score']}/16\n"
        f"📐 Range: {ar['size_pips']:.0f} pips (H={ar['high']:.2f} L={ar['low']:.2f})\n"
        f"🧹 Sweep: {sw['direction']} {sw['depth_pips']:.0f} pips ({sw['quality']})"
    )
    if sw.get("rejection"):
        block += " | Rejection ✓"
    block += f"\n💡 {crt_result['reasoning']}"

    return block


# ── Quick test ──
if __name__ == "__main__":
    from datetime import timedelta
    import random
    random.seed(42)

    # Generate synthetic OHLCV data
    base = 4350.0
    bars = []
    now = datetime.now(UTC)
    
    for i in range(80):
        t = now - timedelta(minutes=15 * (80 - i))
        o = base
        c = base + random.uniform(-5, 5)
        h = max(o, c) + random.uniform(0, 3)
        l = min(o, c) - random.uniform(0, 3)
        bars.append({
            "timestamp": t.isoformat(),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": random.randint(100, 1000),
        })
        base = c

    # Simulate a sweep setup
    # Make the last bar sweep low below "Asian range" and close back inside
    bars[-1]["low"] = 4320.0   # Sweep below range
    bars[-1]["close"] = 4348.0  # Close back inside
    bars[-1]["high"] = 4355.0

    result = analyze_crt_setup(bars, "XAUUSD")
    print(f"Signal: {result['signal']}")
    print(f"Grade: {result['grade_label']} (score: {result['score']})")
    print(f"Reasoning: {result['reasoning']}")
    print()
    print(format_crt_block(result))
