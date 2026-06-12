"""
crt_tbs.py — Candle Range Theory / Time-Based Strategy Engine

Migrated from: scripts/crt_tbs_engine.py
Conforms to: tradebot.engines.base.Engine interface

Asian session range breakout with liquidity sweep detection during
London/NY killzones.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from tradebot.config.settings import settings
from tradebot.engines.base import Engine
from tradebot.exceptions import SignalError
from tradebot.models import Signal, SignalGrade, SignalSource, Tick

LOG = logging.getLogger(__name__)

UTC = UTC

# ── Enums ───────────────────────────────────────────────────────────


class Killzone(Enum):
    NONE = 0
    LONDON = 1
    NEW_YORK = 2
    LONDON_CLOSE = 3


class SweepQuality(Enum):
    NONE = 0
    WEAK = 1
    MODERATE = 2
    STRONG = 3
    PERFECT = 4


class CRTSignalGrade(Enum):
    A_PLUS = 5
    A = 4
    B = 3
    C = 2
    D = 1


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
class CRTSweepSignal:
    """Detected manipulation sweep beyond Asian range."""
    detected: bool = False
    direction: str = ""
    sweep_price: float = 0.0
    quality: SweepQuality = SweepQuality.NONE
    depth_pips: float = 0.0
    candle_rejection: bool = False
    volume_spike: bool = False
    quick_reversal: bool = False
    price_closed_inside: bool = False


@dataclass
class CRTConfirmation:
    """Multi-factor CRT/TBS confirmation scoring."""
    range_valid: bool = False
    in_killzone: bool = False
    sweep_detected: bool = False
    sweep_quality: SweepQuality = SweepQuality.NONE
    price_closed_inside: bool = False
    htf_bias_aligned: bool = False
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
            self.sweep_quality.value,
            int(self.price_closed_inside) * 2,
            int(self.htf_bias_aligned) * 2,
            int(self.candle_rejection) * 1,
            int(self.volume_confirmed) * 1,
            int(self.atr_filter_passed) * 1,
            int(self.not_overextended) * 1,
        ])

    @property
    def grade(self) -> CRTSignalGrade:
        s = self.score
        if s >= 14:
            return CRTSignalGrade.A_PLUS
        if s >= 11:
            return CRTSignalGrade.A
        if s >= 8:
            return CRTSignalGrade.B
        if s >= 5:
            return CRTSignalGrade.C
        return CRTSignalGrade.D


# ── Helpers ────────────────────────────────────────────────────────


def _pip_size(symbol: str = "XAUUSD") -> float:
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


def _ticks_to_ohlcv(ticks: list[Tick]) -> list[dict]:
    """Convert ticks to OHLCV-like bar dicts."""
    bars: list[dict] = []
    for t in ticks:
        bars.append({
            "open": t.price, "high": t.price,
            "low": t.price, "close": t.price,
            "volume": 1, "timestamp": t.epoch,
        })
    return bars


def get_current_killzone(utc_hour: int) -> Killzone:
    wib_hour = (utc_hour + 7) % 24
    if 7 <= wib_hour < 9:
        return Killzone.LONDON
    if 13 <= wib_hour < 15:
        return Killzone.NEW_YORK
    if 15 <= wib_hour < 17:
        return Killzone.LONDON_CLOSE
    return Killzone.NONE


def calculate_asian_range(ohlcv: list[dict], target_date_utc: str = "") -> AsianRange:
    """Calculate Asian session range (00:00-06:00 UTC) from OHLCV data."""
    if not ohlcv or len(ohlcv) < 6:
        return AsianRange(valid=False)

    try:
        if not target_date_utc:
            target_date_utc = datetime.now(UTC).strftime("%Y-%m-%d")

        asian_start = f"{target_date_utc}T00:00:00+00:00"
        asian_end = f"{target_date_utc}T06:00:00+00:00"

        asian_bars: list[dict] = []
        for b in ohlcv:
            ts = b.get("timestamp", b.get("t", ""))
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, UTC)
            elif isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                continue
            iso = dt.isoformat()[:19] + "+00:00"
            if asian_start <= iso <= asian_end:
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
            high=range_high, low=range_low, mid=(range_high + range_low) / 2,
            size_pips=_to_pips(range_size), date=target_date_utc, valid=True,
        )
    except Exception:
        return AsianRange(valid=False)


def detect_crt_sweep(
    ohlcv: list[dict], asian_range: AsianRange, symbol: str = "XAUUSD",
    min_sweep_pips: float = 3.0, max_sweep_pips: float = 50.0,
) -> CRTSweepSignal:
    """Detect manipulation sweep beyond the Asian range."""
    if not asian_range.valid or not ohlcv:
        return CRTSweepSignal()

    recent = ohlcv[-20:]
    if len(recent) < 3:
        return CRTSweepSignal()

    sweep_low: CRTSweepSignal | None = None
    sweep_high: CRTSweepSignal | None = None
    last_close = float(recent[-1].get("close", recent[-1].get("c", 0)))

    for b in recent:
        h = float(b.get("high", b.get("h", 0)))
        low = float(b.get("low", b.get("l", 0)))
        c = float(b.get("close", b.get("c", 0)))

        if low < asian_range.low and not sweep_low:
            depth = _to_pips(asian_range.low - low, symbol)
            if min_sweep_pips <= depth <= max_sweep_pips and c > asian_range.low:
                sweep_low = CRTSweepSignal(
                    detected=True, direction="BUY", sweep_price=low,
                    depth_pips=depth, candle_rejection=True,
                )

        if h > asian_range.high and not sweep_high:
            depth = _to_pips(h - asian_range.high, symbol)
            if min_sweep_pips <= depth <= max_sweep_pips and c < asian_range.high:
                sweep_high = CRTSweepSignal(
                    detected=True, direction="SELL", sweep_price=h,
                    depth_pips=depth, candle_rejection=True,
                )

    if sweep_low and sweep_high:
        sweep = sweep_high if sweep_high.depth_pips > sweep_low.depth_pips else sweep_low
    else:
        sweep = sweep_low or sweep_high

    if not sweep or not sweep.detected:
        return CRTSweepSignal()

    if sweep.depth_pips < 5:
        sweep.quality = SweepQuality.WEAK
    elif sweep.depth_pips < 10:
        sweep.quality = SweepQuality.MODERATE
    elif sweep.depth_pips < 20:
        sweep.quality = SweepQuality.STRONG
    else:
        sweep.quality = SweepQuality.PERFECT

    sweep.price_closed_inside = (asian_range.low <= last_close <= asian_range.high)
    return sweep


def _ema(series: list[float], period: int) -> list[float]:
    """Simple EMA calculation."""
    if len(series) < period:
        return [0.0] * len(series)
    multiplier = 2.0 / (period + 1)
    ema = [sum(series[:period]) / period]
    for price in series[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return [0.0] * (period - 1) + ema


# ── Engine ─────────────────────────────────────────────────────────


class CRTTBSEngine(Engine):
    """CRT/TBS Engine — Candle Range Theory + Time-Based Strategy.

    Uses Asian session range as objective reference. Detects liquidity
    sweeps (manipulation) during London/NY killzones.
    """

    def __init__(self) -> None:
        self._min_range_pips: float = float(getattr(settings, "CRT_MIN_RANGE_PIPS", 20.0))
        self._max_range_pips: float = float(getattr(settings, "CRT_MAX_RANGE_PIPS", 100.0))
        self._min_sweep_pips: float = float(getattr(settings, "CRT_MIN_SWEEP_PIPS", 3.0))
        self._min_quality: str = str(getattr(settings, "CRT_MIN_QUALITY", "B"))

    @property
    def name(self) -> str:
        return "crt_tbs"

    async def analyze(self, ticks: list[Tick]) -> Signal | None:
        """Analyze ticks for CRT/TBS signal."""
        if not ticks or len(ticks) < 20:
            LOG.debug("CRT: insufficient ticks")
            return None

        try:
            ohlcv = _ticks_to_ohlcv(ticks)
            current_price = ticks[-1].price

            # 1. Current killzone check
            now_utc = datetime.now(UTC)
            kz = get_current_killzone(now_utc.hour)
            if kz == Killzone.NONE:
                LOG.debug("CRT: outside killzone (UTC %d:00)", now_utc.hour)
                return None

            # 2. Asian range
            target_date = now_utc.strftime("%Y-%m-%d")
            asian = calculate_asian_range(ohlcv, target_date)
            if not asian.valid:
                return None
            if asian.size_pips < self._min_range_pips or asian.size_pips > self._max_range_pips:
                LOG.debug("CRT: range size %s pips outside bounds", asian.size_pips)
                return None

            # 3. Sweep detection
            sweep = detect_crt_sweep(ohlcv, asian, min_sweep_pips=self._min_sweep_pips)
            if not sweep.detected:
                return None

            # 4. HTF Bias (EMA50)
            htf_bias_aligned = False
            htf_direction = "NEUTRAL"
            if len(ohlcv) >= 50:
                try:
                    closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv]
                    ema50 = _ema(closes, 50)
                    if len(ema50) >= 10 and ema50[-1] > 0:
                        slope = ema50[-1] - ema50[-10]
                        if slope > 0:
                            htf_direction = "BULLISH"
                            htf_bias_aligned = (sweep.direction == "BUY")
                        elif slope < 0:
                            htf_direction = "BEARISH"
                            htf_bias_aligned = (sweep.direction == "SELL")
                except Exception as e:
                    LOG.debug("HTF bias alignment failed: %s", e)

            # 5. Build confirmation
            conf = CRTConfirmation(
                range_valid=asian.valid,
                in_killzone=True,
                sweep_detected=sweep.detected,
                sweep_quality=sweep.quality,
                price_closed_inside=sweep.price_closed_inside,
                htf_bias_aligned=htf_bias_aligned,
                candle_rejection=sweep.candle_rejection,
                atr_filter_passed=True,
                not_overextended=sweep.depth_pips < 50,
            )

            # 6. Quality filter
            min_grade_map = {
                "A+": CRTSignalGrade.A_PLUS, "A": CRTSignalGrade.A,
                "B": CRTSignalGrade.B, "C": CRTSignalGrade.C, "D": CRTSignalGrade.D,
            }
            required = min_grade_map.get(self._min_quality, CRTSignalGrade.B)
            if conf.grade.value < required.value:
                return None

            signal_direction = "CALL" if sweep.direction == "BUY" else "PUT"
            conf_pct = min(1.0, conf.score / 16.0)

            kz_name = {Killzone.LONDON: "London", Killzone.NEW_YORK: "NY", Killzone.LONDON_CLOSE: "LC"}.get(kz, "?")  # noqa: E501

            return Signal(
                symbol="XAUUSD",
                direction=signal_direction,
                predicted_digit=int(current_price * 10) % 10,
                confidence=conf_pct,
                source=SignalSource.MOMEN,
                grade=SignalGrade.STRONG if conf_pct >= 0.7 else (
                    SignalGrade.MODERATE if conf_pct >= 0.5 else SignalGrade.WEAK
                ),
                metadata={
                    "engine": self.name,
                    "crt_score": conf.score,
                    "grade_label": conf.grade.name,
                    "sweep_direction": sweep.direction,
                    "sweep_depth_pips": sweep.depth_pips,
                    "sweep_quality": sweep.quality.name,
                    "asian_range_high": asian.high,
                    "asian_range_low": asian.low,
                    "asian_range_size": asian.size_pips,
                    "killzone": kz_name,
                    "htf_direction": htf_direction,
                    "htf_aligned": htf_bias_aligned,
                    "price_closed_inside": sweep.price_closed_inside,
                },
            )
        except Exception as exc:
            LOG.warning("CRT/TBS engine error: %s", exc)
            raise SignalError("CRT/TBS analysis failed", details={"error": str(exc)}) from exc
