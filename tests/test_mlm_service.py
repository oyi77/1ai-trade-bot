"""Tests for tradebot/services/mlm_service.py — MLM tree, commissions, claims."""

from __future__ import annotations

import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from tradebot.services import mlm_service as mlm
from tradebot.storage.repository import SQLiteRepository
from tradebot.storage.sqlite import SQLiteStorage


class _RowStore(SQLiteStorage):
    """SQLiteStorage returning sqlite3.Row objects so dict(row) works."""

    def conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path))
        c.row_factory = sqlite3.Row
        return c


class _RowRepo(SQLiteRepository):
    """SQLiteRepository returning Rows so service dict(row) calls work."""

    def __init__(self, tmp_path):
        db_path = tmp_path / "test_mlm.db"
        self._store = _RowStore(db_path)


class TestMLMService:
    """MLM tree registration, commission distribution, claims, and formatting."""

    # ── Fixture ──────────────────────────────────────────────────────────

    @pytest.fixture(autouse=True)
    def _patch_repo(self, tmp_path):
        """Replace get_repo with a Row-returning SQLite repo on a temp db."""
        repo = _RowRepo(tmp_path)
        with patch("tradebot.services.mlm_service.get_repo", return_value=repo):
            mlm.init_tables()
            yield

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _insert_user(user_id: str, upline_id: str = "", level: int = 0):
        mlm._storage().execute(
            "INSERT INTO mlm_tree (user_id, upline_id, level, joined_at) VALUES (?, ?, ?, ?)",
            (user_id, upline_id, level, int(time.time())),
        )

    @staticmethod
    def _build_chain(depth: int):
        """chain: root -> user_l1 -> ... -> user_l{depth}; returns leaf (payer)."""
        prev = "root"
        TestMLMService._insert_user(prev, "", 0)
        for i in range(1, depth + 1):
            uid = f"user_l{i}"
            TestMLMService._insert_user(uid, prev, i)
            prev = uid
        return prev

    # ── register_user ────────────────────────────────────────────────────

    def test_register_root(self):
        """register_user() without referral creates a root-level user."""
        result = mlm.register_user("alice")
        assert result == {"user_id": "alice", "upline_id": "", "level": 0}

        row = mlm._storage().fetchone(
            "SELECT user_id, upline_id, level FROM mlm_tree WHERE user_id=?", ("alice",)
        )
        assert row is not None
        assert row[0] == "alice"
        assert row[1] == ""
        assert row[2] == 0

    def test_register_with_referral(self):
        """register_user() with valid referral code sets upline and level."""
        mlm.register_user("referrer")

        mock_affiliate = MagicMock()
        mock_affiliate.user_id = "referrer"

        with patch(
            "tradebot.bots.stockity.affiliate.get_affiliate_by_code",
            return_value=mock_affiliate,
        ):
            result = mlm.register_user("bob", referral_code="REF123")

        assert result["user_id"] == "bob"
        assert result["upline_id"] == "referrer"
        assert result["level"] == 1

        row = mlm._storage().fetchone(
            "SELECT user_id, upline_id, level FROM mlm_tree WHERE user_id=?", ("bob",)
        )
        assert row[1] == "referrer"
        assert row[2] == 1

    def test_register_idempotent(self):
        """register_user() returns existing record when user already registered."""
        mlm.register_user("alice")
        result = mlm.register_user("alice")
        assert result["user_id"] == "alice"

    def test_register_referral_bad_code(self):
        """register_user() with unknown referral code falls back to root."""
        mlm.register_user("root")

        with patch(
            "tradebot.bots.stockity.affiliate.get_affiliate_by_code",
            return_value=None,
        ):
            result = mlm.register_user("charlie", referral_code="BADCODE")

        assert result["upline_id"] == ""
        assert result["level"] == 0

    # ── record_commission ───────────────────────────────────────────────

    def test_commission_single_level(self):
        """record_commission distributes 30% pool to direct upline."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )

        commissions = mlm.record_commission("payer", 1_000_000, "test", "ref1")

        # 30% of 1M = 300k pool. Level 1 gets 40% of pool = 120k.
        assert len(commissions) == 1
        assert commissions[0]["user_id"] == "root"
        assert commissions[0]["amount"] == 120_000

        bal = mlm.get_balance("root")
        assert bal["total_earned"] == 120_000

    def test_commission_platform_never_below_seventy(self):
        """Platform always keeps >= 70% of payment, regardless of tree depth."""
        leaf = self._build_chain(10)

        commissions = mlm.record_commission(leaf, 1_000_000, "test", "ref2")

        total_distributed = sum(c["amount"] for c in commissions)
        assert total_distributed <= 300_000
        platform_share = 1_000_000 - total_distributed
        assert platform_share >= 700_000

    def test_commission_deep_tree(self):
        """record_commission handles 10-level tree correctly."""
        leaf = self._build_chain(10)
        commissions = mlm.record_commission(leaf, 1_000_000, "test", "ref3")

        # 10 upline levels (user_l9 through user_l1 + root)
        assert len(commissions) == 10
        user_ids = {c["user_id"] for c in commissions}
        for i in range(1, 10):
            assert f"user_l{i}" in user_ids, f"user_l{i} missing"
        assert "root" in user_ids

    def test_commission_pool_does_not_exceed_thirty_percent(self):
        """Total commissions never exceed 30% of the payment."""
        leaf = self._build_chain(5)
        commissions = mlm.record_commission(leaf, 500_000, "test", "ref4")
        total = sum(c["amount"] for c in commissions)
        pool_max = int(500_000 * mlm.MLM_POOL_SHARE)  # 150_000
        assert total <= pool_max

    def test_commission_no_tree(self):
        """record_commission with no tree returns empty list, 100% to platform."""
        commissions = mlm.record_commission("nobody", 1_000_000, "test", "ref5")
        assert commissions == []

    def test_commission_levels_match_distribution(self):
        """Verify level-by-level pool distribution rates are applied."""
        # chain: root -> user_l1 -> user_l2 -> user_l3 (payer)
        leaf = self._build_chain(3)
        commissions = mlm.record_commission(leaf, 1_000_000, "test", "ref6")

        # payer=user_l3, upline=user_l2 -> level 0 (40% of pool)
        # user_l2's upline=user_l1 -> level 1 (20% of pool)
        # user_l1's upline=root -> level 2 (10% of pool)
        pool = int(1_000_000 * mlm.MLM_POOL_SHARE)
        expected = {
            "user_l2": int(pool * mlm.POOL_DISTRIBUTION[0]),  # 120_000
            "user_l1": int(pool * mlm.POOL_DISTRIBUTION[1]),  # 60_000
            "root":    int(pool * mlm.POOL_DISTRIBUTION[2]),  # 30_000
        }
        for c in commissions:
            exp = expected[c["user_id"]]
            assert c["amount"] == exp, f"{c['user_id']}: expected {exp}, got {c['amount']}"

    # ── get_balance ─────────────────────────────────────────────────────

    def test_balance_zero_for_new_user(self):
        """get_balance() returns zeros for a user with no earnings."""
        mlm.register_user("nobody")
        bal = mlm.get_balance("nobody")
        assert bal["total_earned"] == 0
        assert bal["total_claimed"] == 0
        assert bal["pending_claims"] == 0
        assert bal["available"] == 0
        assert bal["can_claim"] is False

    def test_balance_earned_claimed_available(self):
        """get_balance() reflects earned, claimed, and pending correctly."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 1_000_000, "test", "ref7")

        bal = mlm.get_balance("root")
        assert bal["total_earned"] == 120_000
        assert bal["total_claimed"] == 0
        assert bal["pending_claims"] == 0
        assert bal["available"] == 120_000
        assert bal["can_claim"] is True

    # ── create_claim ────────────────────────────────────────────────────

    def test_create_claim_pending(self):
        """create_claim() creates a pending claim when balance sufficient."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 2_000_000, "test", "ref8")

        result = mlm.create_claim("root", 150_000)
        assert result["success"] is True
        assert result["status"] == "pending"
        assert result["amount"] == 150_000
        assert result["claim_id"].startswith("cl_")

        bal = mlm.get_balance("root")
        assert bal["pending_claims"] == 150_000
        assert bal["available"] == 240_000 - 150_000  # 240k earned - 150k pending

    def test_create_claim_below_minimum(self):
        """create_claim() rejects amounts below MIN_CLAIM_AMOUNT."""
        result = mlm.create_claim("nobody", 50_000)
        assert "error" in result
        assert "Minimal" in result["error"]

    def test_create_claim_insufficient_balance(self):
        """create_claim() rejects when available balance is too low."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 1_000_000, "test", "ref9")

        result = mlm.create_claim("root", 500_000)
        assert "error" in result
        assert "Saldo tidak mencukupi" in result["error"]

    # ── approve_claim / reject_claim ────────────────────────────────────

    def test_approve_claim(self):
        """approve_claim() marks approved and records ledger entry."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 2_000_000, "test", "ref10")

        claim = mlm.create_claim("root", 150_000)
        result = mlm.approve_claim(claim["claim_id"], "admin1")
        assert result["success"] is True
        assert result["status"] == "approved"

        ledger = mlm.get_ledger("root")
        claim_entries = [e for e in ledger if e["type"] == "claim"]
        assert len(claim_entries) == 1
        assert claim_entries[0]["amount"] == 150_000

        bal = mlm.get_balance("root")
        assert bal["total_claimed"] == 150_000

    def test_approve_already_processed(self):
        """approve_claim() returns error for non-pending claim."""
        result = mlm.approve_claim("nonexistent", "admin1")
        assert "error" in result

    def test_reject_claim(self):
        """reject_claim() marks rejected; pending amount released."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 2_000_000, "test", "ref11")

        claim = mlm.create_claim("root", 150_000)
        result = mlm.reject_claim(claim["claim_id"], "admin1", note="Duplicate")
        assert result["success"] is True
        assert result["status"] == "rejected"

        bal = mlm.get_balance("root")
        assert bal["pending_claims"] == 0
        assert bal["available"] == 240_000

    def test_reject_claim_no_note(self):
        """reject_claim() works without a note."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 2_000_000, "test", "ref12")
        claim = mlm.create_claim("root", 150_000)
        result = mlm.reject_claim(claim["claim_id"], "admin1")
        assert result["success"] is True

    # ── get_pending_claims ──────────────────────────────────────────────

    def test_get_pending_claims(self):
        """get_pending_claims() returns pending claims in creation order."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 5_000_000, "test", "ref13")

        c1 = mlm.create_claim("root", 150_000)
        c2 = mlm.create_claim("root", 100_000)

        pending = mlm.get_pending_claims()
        claim_ids = [p["claim_id"] for p in pending]
        assert c1["claim_id"] in claim_ids
        assert c2["claim_id"] in claim_ids
        # oldest first
        assert claim_ids.index(c1["claim_id"]) < claim_ids.index(c2["claim_id"])

    # ── get_downline ────────────────────────────────────────────────────

    def test_get_downline(self):
        """get_downline() returns direct referrals."""
        mlm.register_user("root")
        mlm.register_user("u1")
        mlm.register_user("u2")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id IN ('u1','u2')"
        )

        downline = mlm.get_downline("root")
        assert len(downline) == 2
        uids = {r["user_id"] for r in downline}
        assert uids == {"u1", "u2"}

    def test_get_downline_empty(self):
        """get_downline() returns empty list when no referrals."""
        mlm.register_user("loner")
        assert mlm.get_downline("loner") == []

    # ── get_ledger ──────────────────────────────────────────────────────

    def test_get_ledger(self):
        """get_ledger() returns transaction history newest first."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 1_000_000, "test", "ref14")

        ledger = mlm.get_ledger("root")
        assert len(ledger) == 1
        assert ledger[0]["type"] == "earn"
        assert ledger[0]["amount"] == 120_000

    def test_get_ledger_empty(self):
        """get_ledger() returns empty list for user with no history."""
        mlm.register_user("nobody")
        assert mlm.get_ledger("nobody") == []

    # ── format_balance ──────────────────────────────────────────────────

    def test_format_balance_html(self):
        """format_balance() returns HTML with balance info."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 1_000_000, "test", "ref15")

        html = mlm.format_balance("root")
        assert isinstance(html, str)
        assert "<b>EARNINGS</b>" in html
        assert "Rp120,000" in html
        assert "/claim" in html

    def test_format_balance_cannot_claim(self):
        """format_balance() shows minimal claim notice when below threshold."""
        mlm.register_user("nobody")
        html = mlm.format_balance("nobody")
        assert "Minimal claim" in html
        assert "/claim" not in html

    # ── format_ledger ───────────────────────────────────────────────────

    def test_format_ledger_html(self):
        """format_ledger() returns HTML with transaction history."""
        mlm.register_user("root")
        mlm.register_user("payer")
        mlm._storage().execute(
            "UPDATE mlm_tree SET upline_id='root', level=1 WHERE user_id='payer'"
        )
        mlm.record_commission("payer", 1_000_000, "test", "ref16")

        html = mlm.format_ledger("root")
        assert isinstance(html, str)
        assert "<b>TRANSACTION HISTORY</b>" in html
        assert "+Rp120,000" in html

    def test_format_ledger_empty(self):
        """format_ledger() returns empty message for no history."""
        mlm.register_user("nobody")
        assert mlm.format_ledger("nobody") == "Belum ada transaksi."