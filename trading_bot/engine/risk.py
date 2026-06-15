"""Risk management — position sizing, exposure limits, drawdown control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_bot.providers.base import Order, Position

# ---------------------------------------------------------------------------
#  Risk configuration
# ---------------------------------------------------------------------------


@dataclass
class RiskConfig:
    """Parameters that control risk behaviour.

    Attributes:
        max_risk_per_trade_pct: Maximum percentage of balance risked per
            trade (e.g. ``1.0`` = 1%).
        max_drawdown_pct: Maximum allowed drawdown before halting
            (e.g. ``20.0`` = 20%).
        max_open_positions: Maximum number of concurrently open positions.
            ``0`` means unlimited.
        max_position_size_pct: Maximum position size as percentage of
            balance (e.g. ``10.0`` = 10%).
        max_exposure_per_symbol_pct: Maximum exposure to a single symbol
            as percentage of balance.
        kelly_fraction: Fraction of full Kelly to use (``0.0``–``1.0``).
            ``0.0`` disables Kelly sizing.
    """

    max_risk_per_trade_pct: float = 1.0
    max_drawdown_pct: float = 20.0
    max_open_positions: int = 5
    max_position_size_pct: float = 10.0
    max_exposure_per_symbol_pct: float = 30.0
    kelly_fraction: float = 0.0


# ---------------------------------------------------------------------------
#  Risk manager
# ---------------------------------------------------------------------------


class RiskManager:
    """Validates orders and calculates position sizing.

    All methods return a ``RiskResult`` with a boolean ``approved`` flag
    and a human-readable ``reason`` when rejected.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self._config = config or RiskConfig()

    @property
    def config(self) -> RiskConfig:
        return self._config

    # ── position sizing ───────────────────────────────────────────────

    def calculate_position_size(
        self,
        balance: float,
        price: float,
        stop_loss_pips: float = 0.0,
        win_rate: float = 0.0,
        avg_win: float = 0.0,
        avg_loss: float = 0.0,
    ) -> float:
        """Calculate position size based on risk parameters.

        Uses fixed-fractional sizing by default.  When ``kelly_fraction``
        > 0 and win/loss stats are provided, applies fractional Kelly.
        """
        cfg = self._config

        # Kelly sizing.
        if cfg.kelly_fraction > 0 and win_rate > 0 and avg_win > 0 and avg_loss > 0:
            kelly_pct = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
            kelly_pct = max(0.0, kelly_pct) * cfg.kelly_fraction
            size_by_risk = balance * kelly_pct
        else:
            size_by_risk = balance * (cfg.max_risk_per_trade_pct / 100.0)

        # Cap by max position size.
        max_size = balance * (cfg.max_position_size_pct / 100.0)
        size = min(size_by_risk, max_size)

        # Convert to units.
        if price > 0 and stop_loss_pips > 0:
            # Fixed fractional: risk / (stop distance * pip value).
            stop_distance = stop_loss_pips * 0.0001 if price < 10 else stop_loss_pips * 0.01
            if stop_distance > 0:
                size = min(size, size_by_risk / max(stop_distance, 1e-10))

        return max(0.0, size)

    # ── order validation ──────────────────────────────────────────────

    def validate_order(
        self,
        order: Order,
        positions: list[Position],
        balance: float,
    ) -> tuple[bool, str]:
        """Check whether an order is allowed under current risk rules.

        Returns:
            A tuple of ``(approved, reason)``.
        """
        cfg = self._config

        # Max open positions.
        if cfg.max_open_positions > 0 and len(positions) >= cfg.max_open_positions:
            return False, f"max open positions reached ({cfg.max_open_positions})"

        # Symbol exposure.
        symbol_exposure = sum(
            p.quantity * p.current_price
            for p in positions
            if p.symbol == order.symbol
        )
        new_exposure = order.quantity * (order.price or 0)
        total_exposure = symbol_exposure + new_exposure
        exposure_limit = balance * (cfg.max_exposure_per_symbol_pct / 100.0)
        if total_exposure > exposure_limit:
            return (
                False,
                f"symbol exposure {total_exposure:.2f} exceeds limit {exposure_limit:.2f}",
            )

        return True, "approved"

    # ── drawdown ──────────────────────────────────────────────────────

    def check_drawdown(self, equity: float, peak: float) -> tuple[bool, str]:
        """Check whether current drawdown exceeds the maximum allowed.

        Returns:
            ``(True, "ok")`` if within limits, ``(False, reason)`` if
            drawdown limit is breached.
        """
        cfg = self._config
        if peak <= 0:
            return True, "ok"
        dd_pct = ((peak - equity) / peak) * 100.0
        if dd_pct > cfg.max_drawdown_pct:
            return (
                False,
                f"drawdown {dd_pct:.1f}% exceeds max {cfg.max_drawdown_pct}%",
            )
        return True, "ok"

    # ── helpers ───────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Return current risk config as a dict."""
        return {
            "max_risk_per_trade_pct": self._config.max_risk_per_trade_pct,
            "max_drawdown_pct": self._config.max_drawdown_pct,
            "max_open_positions": self._config.max_open_positions,
            "max_position_size_pct": self._config.max_position_size_pct,
            "max_exposure_per_symbol_pct": self._config.max_exposure_per_symbol_pct,
        }

    def check_position_limits(
        self,
        positions: list[Position],
        balance: float,
    ) -> tuple[bool, str]:
        """Check whether position count limits allow a new trade.

        Returns:
            ``(True, "ok")`` if within limits, ``(False, reason)`` if
            limit is breached.
        """
        cfg = self._config
        if cfg.max_open_positions > 0 and len(positions) >= cfg.max_open_positions:
            return False, f"max open positions reached ({cfg.max_open_positions})"
        return True, "ok"
