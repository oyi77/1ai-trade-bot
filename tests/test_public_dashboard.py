"""Tests for the public dashboard routes and helper functions.

Covers:
  - All new public page routes (no auth required)
  - All new public API endpoints (no auth required)
  - Public helper functions in tradebot.web.public_dashboard
  - Admin route move: /admin now serves the dashboard, / is a public redirect
  - Accept-Language i18n redirect at /dashboard
  - Fuel donation flow (create + report + dedup)
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Required env for settings to instantiate
os.environ.setdefault("DERIV_APP_ID", "test_app_id")
os.environ.setdefault("DERIV_PAT_TOKEN", "test_pat_token")
os.environ.setdefault("ADMIN_USER_IDS", "123456789")
os.environ.setdefault("BROKER_DRY_RUN", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

from tradebot.web import public_dashboard  # noqa: E402
from tradebot.web.public_dashboard import (  # noqa: E402
    get_backtest_data,
    get_daily_analyze_stats,
    get_daily_mapping,
    get_donor_list,
    get_fuel_stats,
    get_today_trades,
    get_trade_stats,
    save_fuel_report,
)
from tradebot.web.server import app  # noqa: E402


def _client() -> TestClient:
    return TestClient(app)


# ═══════════════════════════════════════════
#  Helper function unit tests
# ═══════════════════════════════════════════


class TestPublicDashboardHelpers(unittest.TestCase):
    """Direct unit tests for the helper functions in public_dashboard.py."""

    def test_get_trade_stats_no_file(self):
        path = Path("/nonexistent/trade_history.json")
        with patch.object(public_dashboard, "TRADE_HISTORY", path):
            stats = get_trade_stats()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["wins"], 0)
        self.assertIn("win_rate", stats)

    def test_get_trade_stats_with_file(self):
        with patch.object(
            public_dashboard,
            "TRADE_HISTORY",
            Path(__file__).resolve().parent / "_fixtures" / "trade_history.json",
        ):
            stats = get_trade_stats()
        self.assertIsInstance(stats, dict)
        self.assertIn("total", stats)
        self.assertIn("total_pips", stats)

    def test_get_daily_mapping_no_file(self):
        with patch.object(public_dashboard, "DATA_DIR", Path("/nonexistent/dir")):
            result = get_daily_mapping()
        self.assertEqual(result, {"date": "N/A", "status": "not_posted"})

    def test_get_today_trades_empty(self):
        path = Path("/nonexistent/trade_history.json")
        with patch.object(public_dashboard, "TRADE_HISTORY", path):
            trades = get_today_trades()
        self.assertEqual(trades, [])

    def test_get_fuel_stats_shape(self):
        stats = get_fuel_stats()
        self.assertIn("monthly_cost", stats)
        self.assertIn("collected", stats)
        self.assertIn("donors", stats)
        self.assertIn("percent", stats)
        self.assertIn("shortfall", stats)
        self.assertIn("status", stats)
        self.assertIn(stats["status"], ("critical", "low", "medium", "healthy"))

    def test_get_daily_analyze_stats_returns_shape(self):
        result = get_daily_analyze_stats()
        self.assertIn("daily", result)
        self.assertIn("total_all_time", result)
        self.assertIn("today", result)
        self.assertIn("yesterday", result)
        self.assertIsInstance(result["daily"], list)

    def test_get_backtest_data_returns_shape(self):
        result = get_backtest_data()
        self.assertIn("xauusd", result)
        self.assertIn("grid", result)
        self.assertIn("per_engine", result)

    def test_save_fuel_report_empty_chat_id(self):
        body, status = save_fuel_report("")
        self.assertEqual(status, 400)
        self.assertFalse(body["success"])

    def test_save_fuel_report_dedup(self, tmp_path=None):
        """Writing the same chat_id twice returns the duplicate message."""
        import tempfile

        with (
            tempfile.TemporaryDirectory() as td,
            patch.object(public_dashboard, "DATA_DIR", Path(td)),
        ):
            chat_id = "test_dedup_001"
            body1, status1 = save_fuel_report(chat_id)
            self.assertEqual(status1, 200, msg=body1)
            self.assertTrue(body1["success"])
            self.assertNotIn("sebelumnya", body1["message"])

            body2, status2 = save_fuel_report(chat_id)
            self.assertEqual(status2, 200)
            self.assertTrue(body2["success"])
            self.assertIn("sebelumnya", body2["message"])

    def test_get_donor_list_handles_missing_or_empty_db(self):
        result = get_donor_list()
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)


# ═══════════════════════════════════════════
#  Public page route tests (no auth)
# ═══════════════════════════════════════════


class TestPublicPageRoutes(unittest.TestCase):
    """All public pages must be reachable without authentication."""

    def setUp(self) -> None:
        self.client = _client()

    def test_root_redirects_to_landing(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/landing")

    def test_landing_page(self):
        r = self.client.get("/landing")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("Vilona", r.text or "")

    def test_signals_page(self):
        r = self.client.get("/signals")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_dashboard_en_page(self):
        r = self.client.get("/dashboard/en")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_dashboard_id_page(self):
        r = self.client.get("/dashboard/id")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_dashboard_bilingual_page(self):
        r = self.client.get("/dashboard/bilingual")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

    def test_dashboard_redirect_accepts_en(self):
        r = self.client.get(
            "/dashboard",
            headers={"Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/dashboard/en")

    def test_dashboard_redirect_accepts_id(self):
        r = self.client.get(
            "/dashboard",
            headers={"Accept-Language": "id-ID,id;q=0.9"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/dashboard/id")

    def test_dashboard_redirect_no_header_defaults_en(self):
        r = self.client.get("/dashboard", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/dashboard/en")

    def test_login_page_no_auth(self):
        r = self.client.get("/login")
        self.assertEqual(r.status_code, 200)


# ═══════════════════════════════════════════
#  Public API endpoint tests (no auth)
# ═══════════════════════════════════════════


class TestPublicApiRoutes(unittest.TestCase):
    """All public APIs must be reachable without authentication."""

    def setUp(self) -> None:
        self.client = _client()

    def test_api_feed(self):
        r = self.client.get("/api/feed")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("signals", data)
        self.assertIsInstance(data["signals"], list)

    def test_api_user_activity(self):
        r = self.client.get("/api/user_activity")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("signals", data)

    def test_api_feed_stats(self):
        r = self.client.get("/api/feed/stats")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, dict)

    def test_api_trade_stats(self):
        r = self.client.get("/api/trade_stats")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, dict)

    def test_api_mapping(self):
        r = self.client.get("/api/mapping")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("date", data)
        self.assertIn("status", data)

    def test_api_today_trades(self):
        r = self.client.get("/api/today_trades")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("trades", data)
        self.assertIsInstance(data["trades"], list)

    def test_api_transparency(self):
        r = self.client.get("/api/transparency")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("total_members", data)
        self.assertIn("total_revenue_idr", data)
        self.assertIn("server_cost_idr", data)
        self.assertIn("api_breakdown", data)

    def test_api_backtest(self):
        r = self.client.get("/api/backtest")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("xauusd", data)
        self.assertIn("grid", data)
        self.assertIn("per_engine", data)

    def test_api_donors(self):
        r = self.client.get("/api/donors")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("donors", data)
        self.assertIsInstance(data["donors"], list)

    def test_api_daily_analyze(self):
        r = self.client.get("/api/daily_analyze")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("daily", data)
        self.assertIn("today", data)
        self.assertIn("yesterday", data)

    def test_api_daily_recap(self):
        r = self.client.get("/api/daily_recap")
        self.assertIn(r.status_code, (200, 500))
        if r.status_code == 200:
            data = r.json()
            self.assertIn("trades", data)

    def test_api_fuel_stats(self):
        r = self.client.get("/api/fuel/stats")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("monthly_cost", data)
        self.assertIn("percent", data)
        self.assertIn("status", data)

    def test_api_fuel_create_too_small(self):
        r = self.client.post(
            "/api/fuel/create",
            params={"amount": 5000, "chat_id": "test", "username": "tester"},
        )
        self.assertEqual(r.status_code, 400)
        data = r.json()
        self.assertFalse(data.get("success"))

    def test_api_fuel_create_missing_amount(self):
        r = self.client.post(
            "/api/fuel/create", params={"chat_id": "test", "username": "tester"}
        )
        self.assertEqual(r.status_code, 422)

    def test_api_fuel_report(self):
        """Smoke test the report endpoint — accepts chat_id as a query param."""
        chat_id = f"test_chat_{int(datetime.now().timestamp())}"
        r = self.client.post(f"/api/fuel/report?chat_id={chat_id}")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["success"])


# ═══════════════════════════════════════════
#  Admin route move tests
# ═══════════════════════════════════════════


class TestAdminRouteMove(unittest.TestCase):
    """Admin dashboard moved from / to /admin. Old / now redirects to /landing."""

    def setUp(self) -> None:
        self.client = _client()

    def test_admin_requires_auth(self):
        r = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_admin_plans_requires_auth(self):
        r = self.client.get("/admin/plans", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_admin_whitelabels_requires_auth(self):
        r = self.client.get("/admin/whitelabels", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_admin_login_then_admin_dashboard(self):
        """End-to-end: log in, then admin pages should work."""
        from tradebot.config import settings as _s
        # Use a real admin ID from the project's .env (overrides test default)
        admin_id = _s.ADMIN_USER_IDS.split(",")[0].strip()
        c = TestClient(app)
        r = c.post("/login", data={"user_id": admin_id}, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        # Login redirects to /, which is now the public landing redirect
        self.assertEqual(r.headers["location"], "/")

        # / now redirects to /landing (public), not /admin
        r = c.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/landing")

        # Admin pages are reached at /admin/* explicitly
        r = c.get("/admin", follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])

        r = c.get("/admin/plans", follow_redirects=False)
        self.assertEqual(r.status_code, 200)

        r = c.get("/admin/whitelabels", follow_redirects=False)
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
