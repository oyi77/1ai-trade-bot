"""Portfolio tracker — positions, balance, and P&L across providers."""

from __future__ import annotations

from typing import Any

from trading_bot.providers.base import BaseProvider, Position


class PortfolioTracker:
    """Tracks open positions, account balance, and P&L.

    Can be updated from multiple providers.  Provides a consolidated
    view of the entire portfolio.
    """

    def __init__(self, initial_balance: float = 0.0) -> None:
        self._initial_balance: float = initial_balance
        self._balance: float = initial_balance
        # symbol -> list of positions
        self._positions: dict[str, list[Position]] = {}
        self._closed_positions: list[Position] = []
        self._equity_peak: float = initial_balance

    # ── balance ───────────────────────────────────────────────────────

    async def refresh_balance(self, provider: BaseProvider) -> None:
        """Fetch current balance from a provider."""
        balance = await provider.get_balance()
        self._balance = balance
        if balance > self._equity_peak:
            self._equity_peak = balance

    def set_balance(self, balance: float) -> None:
        """Manually set balance (for testing / initialisation)."""
        self._balance = balance
        if balance > self._equity_peak:
            self._equity_peak = balance

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def initial_balance(self) -> float:
        return self._initial_balance

    # ── positions ─────────────────────────────────────────────────────

    async def refresh_positions(self, provider: BaseProvider) -> None:
        """Pull all open positions from a provider."""
        positions = await provider.get_positions()
        self._positions.clear()
        for pos in positions:
            self._positions.setdefault(pos.symbol, []).append(pos)

    def add_position(self, position: Position) -> None:
        """Track a new position."""
        self._positions.setdefault(position.symbol, []).append(position)

    def close_position(self, symbol: str, position_id: str | None = None) -> Position | None:
        """Remove and return a position by symbol (and optional id).

        If *position_id* is ``None``, closes the oldest position for the
        symbol.
        """
        positions = self._positions.get(symbol, [])
        for i, pos in enumerate(positions):
            pos_id = getattr(pos, "id", None) or str(pos.entry_price)
            if position_id is None or pos_id == position_id:
                closed = positions.pop(i)
                if not positions:
                    del self._positions[symbol]
                self._closed_positions.append(closed)
                return closed
        return None

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Return open positions, optionally filtered by symbol."""
        if symbol is not None:
            return list(self._positions.get(symbol, []))
        result: list[Position] = []
        for positions in self._positions.values():
            result.extend(positions)
        return result

    @property
    def total_positions(self) -> int:
        return sum(len(v) for v in self._positions.values())

    # ── P&L and summary ───────────────────────────────────────────────

    def unrealized_pnl(self) -> float:
        """Sum of unrealised P&L across all open positions."""
        return sum(p.unrealized_pnl for positions in self._positions.values() for p in positions)

    def realized_pnl(self) -> float:
        """Sum of realised P&L across all closed positions."""
        return sum(p.realized_pnl for p in self._closed_positions)

    def total_equity(self) -> float:
        """Current equity = balance + unrealised P&L."""
        return self._balance + self.unrealized_pnl()

    def drawdown(self) -> float:
        """Current drawdown from equity peak, as a positive float."""
        equity = self.total_equity()
        if self._equity_peak <= 0:
            return 0.0
        return max(0.0, self._equity_peak - equity)

    def drawdown_pct(self) -> float:
        """Drawdown as a percentage of equity peak."""
        if self._equity_peak <= 0:
            return 0.0
        return (self.drawdown() / self._equity_peak) * 100.0

    def get_summary(self) -> dict[str, Any]:
        """Return a snapshot of the portfolio state."""
        return {
            "initial_balance": self._initial_balance,
            "balance": self._balance,
            "equity": self.total_equity(),
            "equity_peak": self._equity_peak,
            "unrealized_pnl": self.unrealized_pnl(),
            "realized_pnl": self.realized_pnl(),
            "drawdown": self.drawdown(),
            "drawdown_pct": self.drawdown_pct(),
            "open_positions": self.total_positions,
            "closed_positions": len(self._closed_positions),
        }
