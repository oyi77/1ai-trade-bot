"""Tests for tradebot/services/accounting.py — Gotong Royong HWM billing."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tradebot.config import settings
from tradebot.services.accounting import (
    AccountingService,
    BillingCycle,
    TradeRecord,
)

WIB = timezone(timedelta(hours=7))

# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════


def _tmp_db() -> str:
    """Create a temporary SQLite DB path."""
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_ledger_")
    os.close(fd)
    return path


def _make_trade(**overrides) -> TradeRecord:
    """Build a TradeRecord with sensible defaults."""
    defaults = {
        "order_id": "order-1",
        "symbol": "BTC/USDT",
        "side": "buy",
        "entry_price": 50000.0,
        "exit_price": 51000.0,
        "amount": 0.1,
        "fee": 0.0,
        "realized_pnl": 0.0,
        "closed_at": "2026-06-10T12:00:00",
        "identifier": "",
    }
    defaults.update(overrides)
    return TradeRecord(**defaults)


# ═══════════════════════════════════════════════════════════════════
#  FILTER VILONA TRADES
# ═══════════════════════════════════════════════════════════════════


class TestFilterVilonaTrades:
    """Trade filtering by Vilona tag identifier."""

    def test_mt5_filters_by_magic_number(self):
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(order_id="1", identifier="7771041", realized_pnl=100.0),
            _make_trade(order_id="2", identifier="101001", realized_pnl=50.0),
            _make_trade(order_id="3", identifier="7771041", realized_pnl=-20.0),
            _make_trade(order_id="4", identifier="", realized_pnl=200.0),
        ]
        filtered = svc.filter_vilona_trades(trades, "mt5")
        assert len(filtered) == 2
        ids = {t.order_id for t in filtered}
        assert ids == {"1", "3"}

    def test_ccxt_filters_by_client_order_id_prefix(self):
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(order_id="1", identifier="vilona_ai_20260610_btc", realized_pnl=100.0),
            _make_trade(order_id="2", identifier="manual_order_123", realized_pnl=50.0),
            _make_trade(order_id="3", identifier="vilona_ai_20260610_eth", realized_pnl=-20.0),
            _make_trade(order_id="4", identifier="", realized_pnl=200.0),
        ]
        filtered = svc.filter_vilona_trades(trades, "ccxt")
        assert len(filtered) == 2
        ids = {t.order_id for t in filtered}
        assert ids == {"1", "3"}

    def test_empty_trades_returns_empty(self):
        svc = AccountingService(db_path=_tmp_db())
        assert svc.filter_vilona_trades([], "mt5") == []
        assert svc.filter_vilona_trades([], "ccxt") == []

    def test_all_vilona_returns_all(self):
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(order_id="a", identifier="7771041"),
            _make_trade(order_id="b", identifier="7771041"),
        ]
        assert len(svc.filter_vilona_trades(trades, "mt5")) == 2


# ═══════════════════════════════════════════════════════════════════
#  P&L CALCULATION
# ═══════════════════════════════════════════════════════════════════


class TestCalculateWeeklyPnl:
    """Weekly P&L from Vilona-tagged trades."""

    def test_pnl_from_realized_values(self):
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(identifier="7771041", realized_pnl=100.0),
            _make_trade(identifier="7771041", realized_pnl=-30.0),
            _make_trade(identifier="7771041", realized_pnl=50.0),
        ]
        pnl = svc.calculate_weekly_pnl(trades, "mt5")
        assert pnl == pytest.approx(120.0)

    def test_pnl_computed_from_entry_exit(self):
        """When realized_pnl is 0, compute from entry/exit/amount."""
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(
                identifier="vilona_ai_test",
                side="buy",
                entry_price=50000.0,
                exit_price=51000.0,
                amount=0.1,
                realized_pnl=0.0,
                fee=0.0,
            ),
        ]
        pnl = svc.calculate_weekly_pnl(trades, "ccxt")
        # (51000-50000) * 0.1 = 100.0
        assert pnl == pytest.approx(100.0)

    def test_pnl_sell_side(self):
        """Sell: (entry - exit) * amount."""
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(
                identifier="vilona_ai_test",
                side="sell",
                entry_price=51000.0,
                exit_price=50000.0,
                amount=0.1,
                realized_pnl=0.0,
                fee=0.0,
            ),
        ]
        pnl = svc.calculate_weekly_pnl(trades, "ccxt")
        # (51000-50000) * 0.1 = 100.0
        assert pnl == pytest.approx(100.0)

    def test_pnl_deducts_fee(self):
        """Fee is subtracted from P&L."""
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(
                identifier="vilona_ai_test",
                side="buy",
                entry_price=50000.0,
                exit_price=51000.0,
                amount=0.1,
                realized_pnl=0.0,
                fee=5.0,
            ),
        ]
        pnl = svc.calculate_weekly_pnl(trades, "ccxt")
        assert pnl == pytest.approx(95.0)

    def test_no_vilona_trades_returns_zero(self):
        svc = AccountingService(db_path=_tmp_db())
        trades = [
            _make_trade(identifier="manual"),
            _make_trade(identifier=""),
        ]
        pnl = svc.calculate_weekly_pnl(trades, "mt5")
        assert pnl == 0.0

    def test_empty_trades_returns_zero(self):
        svc = AccountingService(db_path=_tmp_db())
        assert svc.calculate_weekly_pnl([], "mt5") == 0.0


# ═══════════════════════════════════════════════════════════════════
#  HWM FEE COMPUTATION
# ═══════════════════════════════════════════════════════════════════


class TestComputeFee:
    """High-Water Mark fee computation."""

    def test_profit_above_hwm_charges_20_percent(self):
        svc = AccountingService(db_path=_tmp_db(), fee_rate=0.20)
        fee, hwm_new = svc.compute_fee(hwm_baseline=1000.0, running_equity_before=1000.0, bot_pnl=200.0)
        assert fee == pytest.approx(40.0)
        assert hwm_new == pytest.approx(1200.0)

    def test_loss_no_fee_hwm_unchanged(self):
        svc = AccountingService(db_path=_tmp_db(), fee_rate=0.20)
        fee, hwm_new = svc.compute_fee(hwm_baseline=1000.0, running_equity_before=1000.0, bot_pnl=-50.0)
        assert fee == 0.0
        assert hwm_new == pytest.approx(1000.0)

    def test_partial_recovery_no_fee(self):
        """Recovering from drawdown charges nothing until HWM is broken."""
        svc = AccountingService(db_path=_tmp_db(), fee_rate=0.20)
        # HWM=1500, current equity=1300 (fell from peak), this period +100
        # New equity=1400, still below HWM 1500 → no fee
        fee, hwm_new = svc.compute_fee(hwm_baseline=1500.0, running_equity_before=1300.0, bot_pnl=100.0)
        assert fee == 0.0
        assert hwm_new == pytest.approx(1500.0)

    def test_break_hwm_after_partial_recovery(self):
        """Only the portion above HWM is charged."""
        svc = AccountingService(db_path=_tmp_db(), fee_rate=0.20)
        # HWM=1500, equity=1300, +300 → equity=1600
        # Profit above HWM = 1600 - 1500 = 100. Fee = 20.0
        fee, hwm_new = svc.compute_fee(hwm_baseline=1500.0, running_equity_before=1300.0, bot_pnl=300.0)
        assert fee == pytest.approx(20.0)
        assert hwm_new == pytest.approx(1600.0)

    def test_zero_pnl_no_fee(self):
        svc = AccountingService(db_path=_tmp_db())
        fee, hwm_new = svc.compute_fee(hwm_baseline=1000.0, running_equity_before=1000.0, bot_pnl=0.0)
        assert fee == 0.0
        assert hwm_new == pytest.approx(1000.0)

    def test_small_profit_rounding(self):
        """Fee is rounded to 2 decimal places."""
        svc = AccountingService(db_path=_tmp_db(), fee_rate=0.20)
        fee, hwm_new = svc.compute_fee(hwm_baseline=0.0, running_equity_before=0.0, bot_pnl=0.33)
        assert fee == pytest.approx(0.07)  # 0.066 → 0.07
        assert hwm_new == pytest.approx(0.33)

    def test_new_user_hwm_starts_at_zero(self):
        """New user with no prior ledger gets hwm_baseline=0."""
        svc = AccountingService(db_path=_tmp_db(), fee_rate=0.20)
        fee, hwm_new = svc.compute_fee(hwm_baseline=0.0, running_equity_before=0.0, bot_pnl=500.0)
        assert fee == pytest.approx(100.0)
        assert hwm_new == pytest.approx(500.0)

    def test_negative_equity_never_goes_negative_hwm(self):
        """HWM never drops — it stays at the old peak."""
        svc = AccountingService(db_path=_tmp_db(), fee_rate=0.20)
        fee, hwm_new = svc.compute_fee(hwm_baseline=500.0, running_equity_before=500.0, bot_pnl=-800.0)
        # Equity would be -300, hwm stays at 500.
        assert fee == 0.0
        assert hwm_new == pytest.approx(500.0)


# ═══════════════════════════════════════════════════════════════════
#  BILLING CYCLE INTEGRATION
# ═══════════════════════════════════════════════════════════════════


class TestBillingCycle:
    """Full billing cycle: fetch HWM → calc P&L → compute fee → store."""

    @pytest.mark.asyncio
    async def test_new_user_first_cycle(self):
        """New user generates their first billing cycle."""
        db = _tmp_db()
        svc = AccountingService(db_path=db, fee_rate=0.20)

        trades = [
            _make_trade(identifier="vilona_ai_1", realized_pnl=500.0),
            _make_trade(identifier="vilona_ai_2", realized_pnl=100.0),
            _make_trade(identifier="manual", realized_pnl=9999.0),  # Not Vilona
        ]

        cycle = await svc.generate_billing_cycle(
            chat_id="111",
            platform="ccxt",
            trades=trades,
            period_start="2026-06-08",
            period_end="2026-06-14",
        )

        assert cycle.chat_id == "111"
        assert cycle.platform == "ccxt"
        assert cycle.hwm_baseline == 0.0
        assert cycle.bot_pnl == pytest.approx(600.0)
        assert cycle.hwm_new == pytest.approx(600.0)
        assert cycle.fee_amount == pytest.approx(120.0)
        assert cycle.payment_status == "unpaid"

    @pytest.mark.asyncio
    async def test_second_cycle_hwm_persists(self):
        """HWM carries forward between cycles."""
        db = _tmp_db()
        svc = AccountingService(db_path=db, fee_rate=0.20)

        # Cycle 1: +500, fee=100, HWM=500
        await svc.generate_billing_cycle(
            chat_id="222", platform="mt5",
            trades=[_make_trade(identifier="7771041", realized_pnl=500.0)],
            period_start="2026-06-01", period_end="2026-06-07",
        )

        # Cycle 2: +200 total (+700 from 0), fee only on new break above 500
        await svc.generate_billing_cycle(
            chat_id="222", platform="mt5",
            trades=[_make_trade(identifier="7771041", realized_pnl=200.0)],
            period_start="2026-06-08", period_end="2026-06-14",
        )

        # Verify HWM and fees from ledger (DESC order: ledger[0] = newest)
        ledger = svc.get_ledger("222", "mt5")
        assert len(ledger) == 2
        # Cycle 2 (most recent, ledger[0]): HWM baseline from cycle 1
        assert ledger[0]["hwm_baseline"] == pytest.approx(500.0)
        assert ledger[0]["fee_amount"] == pytest.approx(40.0)  # 20% of 200
        # Cycle 1 (oldest, ledger[1]): new user HWM baseline = 0
        assert ledger[1]["hwm_baseline"] == pytest.approx(0.0)
        assert ledger[1]["fee_amount"] == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_loss_cycle_no_fee(self):
        db = _tmp_db()
        svc = AccountingService(db_path=db, fee_rate=0.20)

        # First: build HWM
        await svc.generate_billing_cycle(
            chat_id="333", platform="ccxt",
            trades=[_make_trade(identifier="vilona_ai_x", realized_pnl=1000.0)],
            period_start="2026-06-01", period_end="2026-06-07",
        )

        # Loss cycle
        cycle = await svc.generate_billing_cycle(
            chat_id="333", platform="ccxt",
            trades=[_make_trade(identifier="vilona_ai_x", realized_pnl=-200.0)],
            period_start="2026-06-08", period_end="2026-06-14",
        )

        assert cycle.fee_amount == 0.0
        assert cycle.hwm_new == pytest.approx(1000.0)  # HWM unchanged

    @pytest.mark.asyncio
    async def test_no_vilona_trades_flat_cycle(self):
        """No tagged trades → zero P&L, no fee, HWM persists."""
        db = _tmp_db()
        svc = AccountingService(db_path=db, fee_rate=0.20)

        # Build HWM first
        await svc.generate_billing_cycle(
            chat_id="444", platform="mt5",
            trades=[_make_trade(identifier="7771041", realized_pnl=800.0)],
            period_start="2026-06-01", period_end="2026-06-07",
        )

        cycle = await svc.generate_billing_cycle(
            chat_id="444", platform="mt5",
            trades=[],  # No trades at all this week
            period_start="2026-06-08", period_end="2026-06-14",
        )

        assert cycle.bot_pnl == 0.0
        assert cycle.fee_amount == 0.0
        assert cycle.hwm_new == pytest.approx(800.0)

    @pytest.mark.asyncio
    async def test_multiple_platforms_independent_hwm(self):
        """MT5 and CCXT have separate HWM tracks."""
        db = _tmp_db()
        svc = AccountingService(db_path=db, fee_rate=0.20)

        await svc.generate_billing_cycle(
            chat_id="555", platform="mt5",
            trades=[_make_trade(identifier="7771041", realized_pnl=500.0)],
            period_start="2026-06-08", period_end="2026-06-14",
        )

        await svc.generate_billing_cycle(
            chat_id="555", platform="ccxt",
            trades=[_make_trade(identifier="vilona_ai_1", realized_pnl=1000.0)],
            period_start="2026-06-08", period_end="2026-06-14",
        )

        mt5_ledger = svc.get_ledger("555", "mt5")
        ccxt_ledger = svc.get_ledger("555", "ccxt")

        assert mt5_ledger[0]["hwm_new"] == pytest.approx(500.0)
        assert ccxt_ledger[0]["hwm_new"] == pytest.approx(1000.0)


# ═══════════════════════════════════════════════════════════════════
#  LEDGER OPERATIONS
# ═══════════════════════════════════════════════════════════════════


class TestLedgerOperations:
    """Read, query, and update ledger records."""

    @pytest.mark.asyncio
    async def test_get_ledger_returns_all(self):
        db = _tmp_db()
        svc = AccountingService(db_path=db)

        await svc.generate_billing_cycle(
            chat_id="123", platform="ccxt",
            trades=[_make_trade(identifier="vilona_ai_1", realized_pnl=100.0)],
            period_start="2026-06-01", period_end="2026-06-07",
        )
        await svc.generate_billing_cycle(
            chat_id="123", platform="ccxt",
            trades=[_make_trade(identifier="vilona_ai_2", realized_pnl=200.0)],
            period_start="2026-06-08", period_end="2026-06-14",
        )

        ledger = svc.get_ledger("123")
        assert len(ledger) == 2

    def test_get_ledger_empty_for_new_user(self):
        db = _tmp_db()
        svc = AccountingService(db_path=db)
        assert svc.get_ledger("nonexistent") == []

    def test_get_unpaid_fees(self):
        db = _tmp_db()
        svc = AccountingService(db_path=db)
        # Can't easily test without async cycle, but schema is tested
        unpaid = svc.get_unpaid_fees()
        assert unpaid == []

    @pytest.mark.asyncio
    async def test_mark_paid(self):
        db = _tmp_db()
        svc = AccountingService(db_path=db)

        await svc.generate_billing_cycle(
            chat_id="789", platform="ccxt",
            trades=[_make_trade(identifier="vilona_ai_x", realized_pnl=100.0)],
            period_start="2026-06-08", period_end="2026-06-14",
        )

        ledger = svc.get_ledger("789")
        assert len(ledger) == 1
        assert ledger[0]["payment_status"] == "unpaid"

        svc.mark_paid(ledger[0]["id"])

        ledger = svc.get_ledger("789")
        assert ledger[0]["payment_status"] == "paid"
        assert ledger[0]["paid_at"] is not None

    @pytest.mark.asyncio
    async def test_idempotent_billing_cycle(self):
        """Same period twice overwrites (INSERT OR REPLACE)."""
        db = _tmp_db()
        svc = AccountingService(db_path=db)

        trades1 = [_make_trade(identifier="7771041", realized_pnl=100.0)]
        trades2 = [_make_trade(identifier="7771041", realized_pnl=300.0)]

        await svc.generate_billing_cycle(
            "999", "mt5", trades1, "2026-06-08", "2026-06-14",
        )
        await svc.generate_billing_cycle(
            "999", "mt5", trades2, "2026-06-08", "2026-06-14",
        )

        ledger = svc.get_ledger("999", "mt5")
        assert len(ledger) == 1
        assert ledger[0]["bot_pnl"] == pytest.approx(300.0)


# ═══════════════════════════════════════════════════════════════════
#  SCHEMA MIGRATION
# ═══════════════════════════════════════════════════════════════════


class TestSchemaMigration:
    """The ledger table is auto-created on first use."""

    def test_table_created_on_init(self):
        db = _tmp_db()
        AccountingService(db_path=db)

        conn = sqlite3.connect(db)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vilona_ledger'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    def test_table_has_all_columns(self):
        db = _tmp_db()
        AccountingService(db_path=db)

        conn = sqlite3.connect(db)
        cols = conn.execute("PRAGMA table_info(vilona_ledger)").fetchall()
        conn.close()
        col_names = {c[1] for c in cols}
        expected = {
            "id", "chat_id", "platform", "period_start", "period_end",
            "hwm_baseline", "bot_pnl", "hwm_new", "fee_amount",
            "payment_status", "generated_at", "paid_at",
        }
        assert expected.issubset(col_names)

    def test_unique_constraint_enforced(self):
        db = _tmp_db()
        svc = AccountingService(db_path=db)
        import asyncio
        # Insert two cycles for same (chat_id, platform, period_start)
        asyncio.run(svc.generate_billing_cycle(
            "111", "ccxt",
            [_make_trade(identifier="v_1", realized_pnl=10.0)],
            "2026-06-08", "2026-06-14",
        ))
        asyncio.run(svc.generate_billing_cycle(
            "111", "ccxt",
            [_make_trade(identifier="v_2", realized_pnl=20.0)],
            "2026-06-08", "2026-06-14",
        ))

        conn = sqlite3.connect(db)
        count = conn.execute(
            "SELECT COUNT(*) FROM vilona_ledger WHERE chat_id='111' AND platform='ccxt' AND period_start='2026-06-08'"
        ).fetchone()[0]
        conn.close()
        assert count == 1  # INSERT OR REPLACE merges


# ═══════════════════════════════════════════════════════════════════
#  TRADE TAG INJECTION
# ═══════════════════════════════════════════════════════════════════


class TestTradeTagging:
    """Vilona trade identifiers are injected by TradeExecutor."""

    def test_build_trade_tags_includes_magic_and_comment(self):
        from tradebot.pipeline.trade_executor import TradeExecutor

        tags = TradeExecutor._build_trade_tags("XAUUSD")
        assert "magic" in tags
        assert tags["magic"] == 7771041
        assert "comment" in tags
        assert "vilona_ai_XAUUSD_" in tags["comment"]
        assert "clientOrderId" in tags
        assert tags["clientOrderId"].startswith("vilona_ai_")

    def test_build_trade_tags_unique_per_call(self):
        from tradebot.pipeline.trade_executor import TradeExecutor
        import time

        tags1 = TradeExecutor._build_trade_tags("BTCUSD")
        time.sleep(1.1)  # Ensure timestamp changes (resolution: 1 second)
        tags2 = TradeExecutor._build_trade_tags("BTCUSD")
        # Timestamp differs
        assert tags1["clientOrderId"] != tags2["clientOrderId"]
        assert tags1["comment"] != tags2["comment"]
