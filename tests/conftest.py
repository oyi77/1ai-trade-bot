"""Shared pytest fixtures for the TradeBot test suite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradebot.models import Tick

# ---------------------------------------------------------------------------
#  Tick fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tick() -> Tick:
    """Return a single Tick with price 33000.0003 → digit=3."""
    return Tick(
        symbol="R_75",
        price=33000.0003,
        epoch=1_000_000,
        timestamp=datetime.now(UTC),
    )


def tick_sequence(digits: list[int], symbol: str = "R_75") -> list[Tick]:
    """Create an ordered sequence of Ticks with the given last decimal digits."""
    ticks: list[Tick] = []
    for i, d in enumerate(digits):
        price = float(f"33000.000{d}")
        ticks.append(
            Tick(
                symbol=symbol,
                price=price,
                epoch=1_000_000 + i,
                timestamp=datetime.now(UTC),
            )
        )
    return ticks


@pytest.fixture
def sample_ticks_100() -> list[Tick]:
    """Return 100 Ticks with a repeating 3→7 pattern (good for Momen tests)."""
    digits: list[int] = []
    for _ in range(50):
        digits.extend([3, 7])
    return tick_sequence(digits)


# ---------------------------------------------------------------------------
#  Mock client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a MagicMock that behaves like DerivWSClient."""
    client = MagicMock()
    client.get_balance = AsyncMock(return_value=100.0)
    client.get_ticks_history = AsyncMock(
        return_value=[
            Tick(
                symbol="R_75",
                price=float(f"33000.000{d}"),
                epoch=1_000_000 + i,
                timestamp=datetime.now(UTC),
            )
            for i, d in enumerate([3, 7] * 50)
        ]
    )
    client.buy_digit = AsyncMock(
        return_value={"contract_id": 123, "profit": 2.52}
    )
    client.subscribe_ticks = AsyncMock()
    client.close = AsyncMock()
    return client


# ---------------------------------------------------------------------------
#  Settings override fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def override_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Apply common test overrides to settings and return the mapping."""

    def _apply(**kwargs: Any) -> dict[str, Any]:
        for key, value in kwargs.items():
            monkeypatch.setenv(key, str(value))
        # Re-import to pick up new env vars
        from tradebot.config import settings  # noqa: F811

        # Re-read env-loaded settings
        for key, value in kwargs.items():
            if hasattr(settings, key):
                monkeypatch.setattr(settings, key, value)
        return kwargs

    return _apply


# ---------------------------------------------------------------------------
#  Temp DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path) -> str:
    """Return a path to a temporary SQLite database file."""
    db_path = tmp_path / "test_tradebot.db"
    return str(db_path)
