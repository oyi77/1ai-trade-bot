"""
Real-time Stockity Blitz Signal Engine.

Analyzes live tick data (bid/ask), spread, tick momentum, and crowd
majority opinion to generate CALL/PUT/HOLD signals for binary options.

Market Edges:
  1. Spread Contraction/Expansion:
     - Spread < median + rate up → CALL momentum
     - Spread > 1.5x median + rate reversed → PUT reversal
  2. Tick Momentum (3-bar):
     - Last 3 ticks all up → CALL
     - Last 3 ticks all down → PUT
     - 2 up / 1 down → HOLD (conflict)
  3. Majority Opinion Contrarian:
     - >65% users CALL → PUT (retail wrong)
     - >65% users PUT → CALL
"""

from __future__ import annotations

import logging
from collections import deque
from statistics import median
from typing import Any

LOG = logging.getLogger("tradebot.signals.stockity_engine")


def _extract_mid(tick: dict[str, Any]) -> float:
    """Extract representative mid-price from a tick."""
    bid = tick.get("bid")
    ask = tick.get("ask")
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return float(tick.get("rate", 0))


class StockityBlitzEngine:
    """Real-time blitz signal engine using tick data + majority opinion.

    Stores a rolling window of ticks and evaluates three independent
    signal strategies, returning a combined verdict.
    """

    def __init__(self, window: int = 10) -> None:
        self._ticks: deque[dict[str, Any]] = deque(maxlen=window)
        self._majority_opinion: dict[str, Any] | None = None

    # ── Data ingestion ──────────────────────────────────────────────────

    def push_tick(self, tick: dict[str, Any]) -> None:
        """Ingest a live tick."""
        self._ticks.append(tick)

    def set_majority_opinion(self, opinion: dict[str, Any]) -> None:
        """Ingest majority opinion from asset topic phx_reply."""
        self._majority_opinion = opinion

    # ── Edge 1: Spread Contraction/Expansion ────────────────────────────

    def _spread_strategy(self) -> tuple[str | None, str]:
        """Evaluate spread-based momentum/reversal edge.

        Returns: (direction: "CALL"|"PUT"|None, label).
        """
        if len(self._ticks) < 3:
            return None, "insufficient_ticks"

        recent = list(self._ticks)
        spreads = []
        for t in recent:
            bid = t.get("bid")
            ask = t.get("ask")
            if bid is not None and ask is not None:
                spreads.append(ask - bid)
        if len(spreads) < 3:
            return None, "no_spread_data"

        spread_median = median(spreads)
        latest_spread = spreads[-1]

        # Rate direction from last two ticks
        r0 = _extract_mid(recent[-1])
        r1 = _extract_mid(recent[-2])
        rate_up = r0 > r1

        # Edge 1a: Spread contraction + rate up → CALL momentum
        if latest_spread < spread_median and rate_up:
            return "CALL", f"spread_contraction({latest_spread:.4f}<{spread_median:.4f})"

        # Edge 1b: Spread expansion (>1.5x median) + rate reversed → PUT reversal
        if latest_spread > spread_median * 1.5 and not rate_up:
            return "PUT", f"spread_expansion({latest_spread:.4f}>1.5x{spread_median:.4f})"

        return None, f"spread_neutral({latest_spread:.4f}/med={spread_median:.4f})"

    # ── Edge 2: Tick Momentum (3-bar) ───────────────────────────────────

    def _momentum_strategy(self) -> tuple[str | None, str]:
        """Evaluate 3-bar tick momentum edge.

        Returns: (direction: "CALL"|"PUT"|None, label).
        """
        if len(self._ticks) < 4:
            return None, "insufficient_ticks"

        recent = list(self._ticks)[-4:]  # need 4 to get 3 deltas
        mids = [_extract_mid(t) for t in recent]
        deltas = [mids[i] - mids[i - 1] for i in range(1, len(mids))]

        up_count = sum(1 for d in deltas if d > 0)
        down_count = sum(1 for d in deltas if d < 0)

        if up_count == 3:
            return "CALL", "3_up_momentum"
        if down_count == 3:
            return "PUT", "3_down_momentum"
        if up_count == 2 and down_count == 1:
            return "HOLD", "conflict_2up_1down"
        if down_count == 2 and up_count == 1:
            return "HOLD", "conflict_2down_1up"
        return None, "mixed_momentum"

    # ── Edge 3: Majority Opinion Contrarian ─────────────────────────────

    def _contrarian_strategy(self) -> tuple[str | None, str]:
        """Evaluate majority opinion contrarian edge.

        Returns: (direction: "CALL"|"PUT"|None, label).
        """
        opinion = self._majority_opinion
        if not opinion:
            return None, "no_opinion_data"

        call_pct = opinion.get("call_percent", 0) or 0
        put_pct = opinion.get("put_percent", 0) or 0

        if call_pct > 65:
            return "PUT", f"contrarian({call_pct:.0f}%_call)"
        if put_pct > 65:
            return "CALL", f"contrarian({put_pct:.0f}%_put)"
        return None, f"majority_balanced({call_pct:.0f}/{put_pct:.0f})"

    # ── Consensus ───────────────────────────────────────────────────────

    def evaluate(self) -> dict[str, Any]:
        """Evaluate all strategies and return combined signal.

        Returns:
            dict with keys:
              - direction: "CALL" | "PUT" | "HOLD" | None
              - strategies: list of (name, direction, label) per strategy
              - tick_count: int
              - majority_opinion: dict or None
        """
        spread_dir, spread_label = self._spread_strategy()
        momentum_dir, momentum_label = self._momentum_strategy()
        contrarian_dir, contrarian_label = self._contrarian_strategy()

        strategies = [
            ("spread", spread_dir, spread_label),
            ("momentum", momentum_dir, momentum_label),
            ("contrarian", contrarian_dir, contrarian_label),
        ]

        # Weighted vote: spread and momentum get 2x, contrarian 1x
        votes: dict[str, float] = {}
        weights = {"spread": 2.0, "momentum": 2.0, "contrarian": 1.0}

        for name, direction, _label in strategies:
            if direction in ("CALL", "PUT"):
                w = weights.get(name, 1.0)
                votes[direction] = votes.get(direction, 0) + w

        direction: str | None = None
        if votes.get("CALL", 0) > votes.get("PUT", 0):
            direction = "CALL"
        elif votes.get("PUT", 0) > votes.get("CALL", 0):
            direction = "PUT"
        elif votes:
            direction = "HOLD"  # tie

        return {
            "direction": direction,
            "strategies": [
                {"name": n, "direction": d, "label": label} for n, d, label in strategies
            ],
            "vote_weights": votes,
            "tick_count": len(self._ticks),
            "majority_opinion": self._majority_opinion,
        }

