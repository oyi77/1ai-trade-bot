"""
Pillar 4: Strict SOP & Data Gatekeeper.

Enforces the non-negotiable trading SOP:
  - SL 30 pip, TP 60 pip (Clamp: SL 20-35, TP max 100, TP1 60, TP2 90)
  - OHLCV validation: corrupt/empty data → SKIP analysis, wait next candle
  - Killzone routing: forex/metals ONLY London+NY; crypto 24/7
  - Daily Max Loss guard: circuit breaker after 3 losses

Usage:
    from tradebot.engines.data_gate import DataGate, validate_ohlcv

    gate = DataGate()
    if not gate.pass_ohlcv_check(bars):
        log.warning("OHLCV invalid — skipping cycle")
        return

    clamped = gate.clamp_sltp(action="BUY", entry=4125.0, raw_sl=4110.0, raw_tp=4160.0)
"""

import math
import logging
from typing import Optional
from enum import Enum

log = logging.getLogger("datagate")

# ── NON-NEGOTIABLE SL/TP BOUNDS ──
SL_MIN_PIP = 20
SL_MAX_PIP = 35
TP_MAX_PIP = 100
TP1_PIP = 60
TP2_PIP = 90

# ── PIP VALUES PER ASSET CLASS ──
PIP_VALUE = {
    "XAUUSD": 0.10,      # 1 pip = 0.10 for 3-digit gold
    "BTCUSD": 1.0,       # 1 pip = 1.0
    "USOIL": 0.01,       # 1 pip = 0.01
}
PIP_DEFAULT = 0.0001     # standard forex


def pip_size(symbol: str) -> float:
    """Return pip value for symbol. Case-insensitive."""
    return PIP_VALUE.get(symbol.upper(), PIP_DEFAULT)


def pips_to_price(gap: float, symbol: str) -> float:
    """Convert pip count to price difference."""
    return gap * pip_size(symbol)


class DataGateResult(Enum):
    PASS = "pass"
    SKIP_OHLCV_CORRUPT = "skip_ohlcv_corrupt"
    SKIP_OHLCV_STALE = "skip_ohlcv_stale"
    SKIP_WEEKEND_FOREX = "skip_weekend_forex"
    SKIP_KILLZONE = "skip_killzone"
    SKIP_CIRCUIT_BREAKER = "skip_circuit_breaker"
    SKIP_DEDUP = "skip_dedup"


class DataGate:
    """Central gatekeeper for signal validation."""

    def __init__(self, daily_loss_limit: int = 3):
        self.daily_losses = 0
        self.daily_loss_limit = daily_loss_limit
        self.last_day = ""
        self.total_skipped = 0
        self.total_passed = 0

    # ── OHLCV Validation ──

    @staticmethod
    def validate_ohlcv(bars: list, min_bars: int = 30) -> DataGateResult:
        """Check OHLCV bars for corruption or staleness.

        Args:
            bars: List of OHLCV dicts with 'open','high','low','close','volume','timestamp'.
            min_bars: Minimum bars required for valid analysis.

        Returns:
            DataGateResult.PASS if valid, SKIP_* otherwise.
        """
        if not bars or len(bars) < min_bars:
            log.warning("OHLCV SKIP: insufficient bars (%d < %d)", len(bars or []), min_bars)
            return DataGateResult.SKIP_OHLCV_CORRUPT

        # Check each bar for validity
        corrupt_count = 0
        for i, bar in enumerate(bars):
            o = bar.get("open", 0)
            h = bar.get("high", 0)
            l = bar.get("low", 0)
            c = bar.get("close", 0)

            # Basic sanity: high >= low, high >= open/close, low <= open/close
            if h < l:
                corrupt_count += 1
                continue
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                corrupt_count += 1
                continue
            # Detect flatline (all 4 prices identical on non-trivial bars)
            if h == l and i > 0 and bars[i-1].get("high", 0) == bars[i-1].get("low", 0):
                corrupt_count += 1

        if corrupt_count > len(bars) * 0.3:  # >30% corrupt
            log.warning("OHLCV SKIP: %d/%d bars corrupt", corrupt_count, len(bars))
            return DataGateResult.SKIP_OHLCV_CORRUPT

        return DataGateResult.PASS

    # ── SL/TP Clamp (THE LAW) ──

    @staticmethod
    def clamp_sltp(action: str, entry: float, raw_sl: float, raw_tp: float,
                   symbol: str = "XAUUSD") -> dict:
        """Enforce SL/TP bounds per SOP. NON-NEGOTIABLE.

        Returns dict with: action, entry, sl, tp, tp1, tp2, clamped (bool).
        """
        ps = pip_size(symbol)
        clamped = False
        result = {
            "action": action.upper(),
            "entry": entry,
            "clamped": False,
        }

        if action.upper() == "BUY":
            # SL below entry, TP above entry
            sl_pips = abs(entry - raw_sl) / ps
            tp_pips = abs(raw_tp - entry) / ps

            # Enforce SL clamp
            if sl_pips < SL_MIN_PIP:
                result["sl"] = round(entry - SL_MIN_PIP * ps, 2)
                clamped = True
            elif sl_pips > SL_MAX_PIP:
                result["sl"] = round(entry - SL_MAX_PIP * ps, 2)
                clamped = True
            else:
                result["sl"] = raw_sl if raw_sl < entry else round(entry - SL_MIN_PIP * ps, 2)

            # Enforce TP clamp
            if tp_pips > TP_MAX_PIP:
                result["tp"] = round(entry + TP_MAX_PIP * ps, 2)
                clamped = True
            elif raw_tp <= entry:
                result["tp"] = round(entry + TP1_PIP * ps, 2)
                clamped = True
            else:
                result["tp"] = raw_tp

            # Sub-targets
            result["tp1"] = round(entry + TP1_PIP * ps, 2)
            result["tp2"] = min(round(entry + TP2_PIP * ps, 2), result["tp"])

        else:  # SELL
            sl_pips = abs(raw_sl - entry) / ps
            tp_pips = abs(entry - raw_tp) / ps

            if sl_pips < SL_MIN_PIP:
                result["sl"] = round(entry + SL_MIN_PIP * ps, 2)
                clamped = True
            elif sl_pips > SL_MAX_PIP:
                result["sl"] = round(entry + SL_MAX_PIP * ps, 2)
                clamped = True
            else:
                result["sl"] = raw_sl if raw_sl > entry else round(entry + SL_MIN_PIP * ps, 2)

            if tp_pips > TP_MAX_PIP:
                result["tp"] = round(entry - TP_MAX_PIP * ps, 2)
                clamped = True
            elif raw_tp >= entry:
                result["tp"] = round(entry - TP1_PIP * ps, 2)
                clamped = True
            else:
                result["tp"] = raw_tp

            result["tp1"] = round(entry - TP1_PIP * ps, 2)
            result["tp2"] = max(round(entry - TP2_PIP * ps, 2), result["tp"])

        result["clamped"] = clamped
        return result

    # ── Killzone Gate ──

    @staticmethod
    def pass_killzone(pair: str, hour: int, is_weekend: bool = False) -> DataGateResult:
        """Check if trading is allowed at this hour for this pair.

        Forex/Metals: only London (14-16 WIB) or NY (19-21 WIB).
        Crypto: always pass (24/7).
        Hour is expected in WIB (UTC+7).
        """
        pair_upper = pair.upper()

        # Crypto — bypass all time gates
        if pair_upper in ("BTCUSD", "ETHUSD", "BTC/USD", "ETH/USD"):
            return DataGateResult.PASS

        # Weekend — forex blocked, crypto already passed above
        if is_weekend:
            return DataGateResult.SKIP_WEEKEND_FOREX

        # London: 14:00-16:00 WIB (07:00-09:00 UTC)
        in_london = 14 <= hour < 17
        # New York: 19:00-21:00 WIB (12:00-14:00 UTC)
        in_ny = 19 <= hour < 22

        if in_london or in_ny:
            return DataGateResult.PASS

        return DataGateResult.SKIP_KILLZONE

    # ── Circuit Breaker ──

    def check_circuit(self, today_str: str, daily_losses: int) -> DataGateResult:
        """Enforce daily max loss limit."""
        if today_str != self.last_day:
            self.daily_losses = 0
            self.last_day = today_str

        self.daily_losses = daily_losses
        if self.daily_losses >= self.daily_loss_limit:
            log.warning("⛔ CIRCUIT BREAKER: %d/%d losses today — STOP",
                       self.daily_losses, self.daily_loss_limit)
            return DataGateResult.SKIP_CIRCUIT_BREAKER

        return DataGateResult.PASS

    # ── Full Pipeline ──

    def evaluate(self, ohlcv_bars: list, pair: str, hour: int,
                 is_weekend: bool, today_str: str, daily_losses: int,
                 min_bars: int = 30) -> DataGateResult:
        """Run all gates in sequence. First failure stops the chain."""
        for check in [
            ("circuit", lambda: self.check_circuit(today_str, daily_losses)),
            ("killzone", lambda: self.pass_killzone(pair, hour, is_weekend)),
            ("ohlcv", lambda: self.validate_ohlcv(ohlcv_bars, min_bars)),
        ]:
            result = check[1]()
            if result != DataGateResult.PASS:
                self.total_skipped += 1
                log.info("⛔ Gate '%s' blocked: %s", check[0], result.value)
                return result

        self.total_passed += 1
        return DataGateResult.PASS

    def snapshot(self) -> dict:
        return {
            "total_passed": self.total_passed,
            "total_skipped": self.total_skipped,
            "daily_losses": self.daily_losses,
            "circuit_breaker": self.daily_losses >= self.daily_loss_limit,
            "sl_bounds": {"min": SL_MIN_PIP, "max": SL_MAX_PIP},
            "tp_bounds": {"max": TP_MAX_PIP, "tp1": TP1_PIP, "tp2": TP2_PIP},
        }


# Global gate instance
GATE = DataGate()
