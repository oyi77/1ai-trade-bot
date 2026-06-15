"""Shared pytest fixtures for the TradeBot test suite."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from tradebot.models import Tick
from trading_bot.providers.base import Candle
from trading_bot.providers.paper.paper_trader import PaperTradingProvider

# ---------------------------------------------------------------------------
#  Global encryption key for tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_master_key():
    """Ensure VILONA_MASTER_KEY is set so crypto-dependent code doesn't crash."""
    existing = os.environ.get("VILONA_MASTER_KEY")
    if not existing:
        os.environ["VILONA_MASTER_KEY"] = Fernet.generate_key().decode()
    yield
    if not existing:
        os.environ.pop("VILONA_MASTER_KEY", None)
    # Reset encryptor cache between tests
    from tradebot.security.crypto import reset_encryptor
    reset_encryptor()

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


# ---------------------------------------------------------------------------
#  Paper provider fixtures (trading_bot.providers)
# ---------------------------------------------------------------------------


@pytest.fixture
def paper_provider() -> PaperTradingProvider:
    """Return a fresh PaperTradingProvider with default 10,000 balance."""
    return PaperTradingProvider()


@pytest.fixture
def sample_candles() -> list[Candle]:
    """Return 10 OHLCV candles for EUR/USD at 1h."""
    base = 1.10500
    candles: list[Candle] = []
    for i in range(10):
        o = base + i * 0.0005
        h = o + 0.0003
        low_val = o - 0.0002
        c = o + 0.0001
        candles.append(Candle(
            symbol="EUR/USD",
            timeframe="1h",
            open=o,
            high=h,
            low=low_val,
            close=c,
            volume=1000.0 + i * 100,
            timestamp=datetime(2026, 1, 1, i + 8, 0, 0, tzinfo=UTC),
        ))
    return candles
