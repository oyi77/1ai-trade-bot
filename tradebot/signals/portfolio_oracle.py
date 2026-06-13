"""
Portfolio Oracle — Multi-asset portfolio engine for Stockity turbo assets.

Selects the best asset to trade at any given time using:
- Time-of-day session awareness (forex session rotation)
- Asset volatility ranking (higher = better edge for turbo)
- WR tier (tier 1 = POWER-X/GBPSGD, tier 2 = CADSEK/CHFNOK, tier 3 = rest)
- Consecutive trade filter (same asset ≤3x in a row)
- Optimal parameters (win, thr) per asset for direction picking
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

LOG = logging.getLogger("tradebot.signals.portfolio_oracle")

# ── Turbo Asset Definitions ────────────────────────────────────────────────
# Parameters derived from backtest grid search (win = lookback, thr = threshold).
# wr is backtest win-rate, payout is the platform payout fraction.

ASSET_TIERS: dict[str, list[dict[str, Any]]] = {
    "tier1": [
        {"ric": "POWER-X",     "win": 15, "thr": 0.55, "payout": 0.83, "wr": 62.6},
        {"ric": "GBPSGD",      "win": 12, "thr": 0.55, "payout": 0.82, "wr": 59.8},
    ],
    "tier2": [
        {"ric": "CADSEK",      "win": 3,  "thr": 0.50, "payout": 0.80, "wr": 60.4},
        {"ric": "CHFNOK",      "win": 3,  "thr": 0.50, "payout": 0.80, "wr": 58.7},
        {"ric": "BCHUSD-OTC",  "win": 3,  "thr": 0.50, "payout": 0.75, "wr": 58.9},
        {"ric": "ETHUSD-OTC",  "win": 3,  "thr": 0.50, "payout": 0.75, "wr": 57.2},
        {"ric": "EURGBP",      "win": 3,  "thr": 0.50, "payout": 0.78, "wr": 57.0},
        {"ric": "AUDNZD",      "win": 3,  "thr": 0.50, "payout": 0.78, "wr": 56.5},
    ],
    "tier3": [
        {"ric": "USDSEK",      "win": 3,  "thr": 0.50, "payout": 0.78, "wr": 56.2},
        {"ric": "EURUSD",      "win": 3,  "thr": 0.55, "payout": 0.80, "wr": 55.8},
        {"ric": "USDJPY",      "win": 3,  "thr": 0.55, "payout": 0.80, "wr": 55.5},
        {"ric": "BTCUSD-OTC",  "win": 3,  "thr": 0.50, "payout": 0.75, "wr": 55.5},
        {"ric": "GBPUSD",      "win": 3,  "thr": 0.55, "payout": 0.80, "wr": 55.2},
        {"ric": "USDCHF",      "win": 3,  "thr": 0.55, "payout": 0.80, "wr": 55.0},
        {"ric": "USDCAD",      "win": 3,  "thr": 0.55, "payout": 0.78, "wr": 54.7},
        {"ric": "AUDUSD",      "win": 3,  "thr": 0.55, "payout": 0.78, "wr": 54.5},
        {"ric": "NZDUSD",      "win": 3,  "thr": 0.55, "payout": 0.78, "wr": 54.3},
        {"ric": "EURJPY",      "win": 3,  "thr": 0.50, "payout": 0.78, "wr": 54.0},
        {"ric": "GBPJPY",      "win": 3,  "thr": 0.50, "payout": 0.78, "wr": 53.8},
        {"ric": "EURCHF",      "win": 3,  "thr": 0.55, "payout": 0.78, "wr": 53.6},
        {"ric": "GBPAUD",      "win": 3,  "thr": 0.50, "payout": 0.78, "wr": 53.3},
        {"ric": "AUDJPY",      "win": 3,  "thr": 0.55, "payout": 0.78, "wr": 53.1},
        {"ric": "EURAUD",      "win": 3,  "thr": 0.50, "payout": 0.78, "wr": 52.8},
    ],
}

ALL_ASSETS: list[dict[str, Any]] = [
    a for tier in ASSET_TIERS.values() for a in tier
]

SESSION_ASSETS: dict[str, list[str]] = {
    # Asian session (00:00-09:00 UTC) — JPY, AUD, NZD, SGD pairs peak
    "asia": [
        "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
        "AUDUSD", "AUDNZD", "NZDUSD",
        "EURAUD", "GBPAUD", "GBPSGD",
        "EURGBP",  # cross-active during Asia too
    ],
    # London session (07:00-16:00 UTC) — EUR, GBP, CHF, SEK, NOK pairs peak
    "london": [
        "EURUSD", "GBPUSD", "EURGBP", "EURCHF",
        "USDCHF", "USDSEK",
        "CADSEK", "CHFNOK", "EURJPY", "GBPJPY",
        "GBPAUD", "EURAUD",
    ],
    # US/New York session (12:00-21:00 UTC) — USD pairs peak
    "us": [
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
        "USDCAD", "USDSEK",
        "AUDUSD", "NZDUSD", "GBPJPY", "EURJPY",
    ],
}

# Assets active 24/7 regardless of session
ALWAYS_ON_RICS: set[str] = {
    "POWER-X", "ETHUSD-OTC", "BCHUSD-OTC", "BTCUSD-OTC",
}


def _get_active_session(hour_utc: int) -> str | None:
    """Return the active trading session label for a given UTC hour."""
    if 0 <= hour_utc < 7:
        return "asia"
    if 7 <= hour_utc < 12:
        return "london"
    if 12 <= hour_utc < 16:
        return "overlap"  # London+NY overlap — both sessions active
    if 16 <= hour_utc < 21:
        return "us"
    # 21-23: late US / rollover — use US still
    return "us"


def _session_volatility_bonus(ric: str, hour_utc: int) -> float:
    """Return a volatility multiplier [0.8-1.5] based on session fit."""
    session = _get_active_session(hour_utc)
    if session == "overlap":
        # London+NY overlap: all session assets get boosted vol
        bonus = 1.0
        for s in ("london", "us"):
            if ric in SESSION_ASSETS.get(s, []):
                bonus = max(bonus, 1.4)
        return bonus
    if session and ric in SESSION_ASSETS.get(session, []):
        return 1.4 if session in ("london", "us") else 1.25
    return 0.9  # slight penalty for off-session


def _get_tier_weight(ric: str) -> int:
    """Return tier priority weight: tier1=3, tier2=2, tier3=1."""
    for tier_name, assets in ASSET_TIERS.items():
        for a in assets:
            if a["ric"] == ric:
                return {"tier1": 3, "tier2": 2, "tier3": 1}.get(tier_name, 1)
    return 1


def _ric_to_asset(ric: str) -> dict[str, Any] | None:
    """Look up an asset definition by RIC."""
    for a in ALL_ASSETS:
        if a["ric"] == ric:
            return a
    return None


# ── Portfolio Oracle ───────────────────────────────────────────────────────

class PortfolioOracle:
    """Session-aware portfolio engine that selects the best turbo asset.

    Scoring criteria (in order of importance):
    1. Session volatility bonus (assets at their peak hours score highest)
    2. WR tier weight (tier1 > tier2 > tier3)
    3. Random jitter (avoid deterministic cycles)
    """

    def __init__(self) -> None:
        self._last_asset_rics: list[str] = []

    def get_best_asset(self, hour_utc: int | None = None) -> dict[str, Any] | None:
        """Pick the best turbo asset to trade right now.

        Args:
            hour_utc: UTC hour override (default: current UTC hour).

        Returns:
            Dict with keys: ric, direction_picker_params, duration, action,
            or None if no eligible asset.
        """
        if hour_utc is None:
            hour_utc = datetime.now(UTC).hour

        eligible = self.get_asset_by_session(hour_utc)
        if not eligible:
            LOG.warning("No eligible assets at hour_utc=%d", hour_utc)
            return None

        # Score each eligible asset
        scored: list[tuple[float, dict[str, Any]]] = []
        for asset in eligible:
            ric = asset["ric"]

            # Filter consecutive repeats
            if not self.can_trade_asset(ric):
                continue

            # Volatility bonus from session fit
            vol_bonus = _session_volatility_bonus(ric, hour_utc)

            # WR tier weight
            tier_weight = _get_tier_weight(ric)

            # Normalise WR [0-1] for finer ranking within tiers
            wr_score = asset["wr"] / 100.0

            # Small random jitter (±5%) to break deterministic ties
            jitter = 1.0 + random.uniform(-0.05, 0.05)

            score = vol_bonus * tier_weight * wr_score * jitter
            scored.append((score, asset))

        if not scored:
            LOG.warning("All assets filtered out by consecutive check")
            return None

        # Descending by score
        scored.sort(key=lambda x: -x[0])
        best = scored[0][1]

        # Build direction-picker params
        direction_picker_params = {
            "lookback": best["win"],
            "threshold": best["thr"],
            "payout": best["payout"],
        }

        result: dict[str, Any] = {
            "ric": best["ric"],
            "wr": best["wr"],
            "win": best["win"],
            "thr": best["thr"],
            "payout": best["payout"],
            "direction_picker_params": direction_picker_params,
            "duration": 60,  # 1m turbo
            "action": "turbo",
        }

        # Track consecutive usage
        self._track_trade(best["ric"])

        return result

    def get_best_asset_for_now(self) -> dict[str, Any] | None:
        """Convenience wrapper — calls get_best_asset with current UTC time."""
        return self.get_best_asset()

    def get_asset_by_session(self, hour_utc: int) -> list[dict[str, Any]]:
        """Return all tradeable assets for the given UTC hour, session-sorted.

        Assets that match the current session come first, followed by always-on
        assets, then off-session assets in descending WR order.
        """
        session = _get_active_session(hour_utc)
        session_rics: set[str] = set()
        if session and session != "overlap":
            session_rics = set(SESSION_ASSETS.get(session, []))
        elif session == "overlap":
            # Both London and US session assets
            session_rics = set(SESSION_ASSETS.get("london", [])) | set(SESSION_ASSETS.get("us", []))

        candidates = []
        for a in ALL_ASSETS:
            ric = a["ric"]
            # Always-on assets are always eligible
            if ric in ALWAYS_ON_RICS or ric in session_rics:
                candidates.append(a)

        # Sort: session-matching first (higher WR first), then always-on, then others by WR
        def _sort_key(a: dict[str, Any]) -> tuple[int, float]:
            ric = a["ric"]
            if ric in session_rics:
                return (0, -a["wr"])
            if ric in ALWAYS_ON_RICS:
                return (1, -a["wr"])
            return (2, -a["wr"])

        candidates.sort(key=_sort_key)
        return candidates

    def can_trade_asset(self, ric: str) -> bool:
        """Check if the asset is not in consecutive-repeat penalty.

        Prevents trading the same asset more than 3 times in a row.
        """
        count = self._last_asset_rics.count(ric)
        return count < 3

    def _track_trade(self, ric: str) -> None:
        """Record a trade on an asset for consecutive-repeat tracking."""
        self._last_asset_rics.append(ric)
        # Keep only the last 5 entries (more than we need for 3x check)
        if len(self._last_asset_rics) > 5:
            self._last_asset_rics.pop(0)

    @property
    def last_traded_rics(self) -> Sequence[str]:
        """Return the recent trade history (RICs only)."""
        return list(self._last_asset_rics)

    def reset_tracker(self) -> None:
        """Clear consecutive-trade tracking."""
        self._last_asset_rics.clear()


# ── Module-level helper ────────────────────────────────────────────────────

def get_best_asset_for_now() -> dict[str, Any] | None:
    """One-shot convenience: create an oracle, pick best asset, return it."""
    return PortfolioOracle().get_best_asset_for_now()
