"""
Public Dashboard Helper Functions — Ported from scripts/dashboard_server.py

Pure data-shaping functions over local JSON files and SQLite.
Sync, FastAPI-compatible. No business logic changes from pre-unification
behavior — these are a mechanical port of the original implementations.

Functions:
    get_backtest_data      — Load XAUUSD / grid / per-engine backtest results
    get_donor_list         — Subscriber members + their paid amounts from members.db
    get_daily_analyze_stats — Per-day analyze request counts from quota_cache/
    get_trade_stats        — Trade history summary stats
    get_daily_mapping      — Latest daily mapping date
    get_today_trades       — Today's filtered trades from trade_history.json
    get_transparency_data  — Members, revenue, costs for LP transparency page
    get_fuel_stats         — AI Fuel donation progress (composes from above)
    save_fuel_report       — Persist a manual transfer report (dedup by chat_id)

Run: not invoked directly. Consumed by tradebot/web/server.py routes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger("tradebot.web.public")

# Paths — kept aligned with old scripts/dashboard_server.py where possible.
# PROJECT_DIR = the tradebot repo root (so .parent.parent of tradebot/web/).
WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
TRADE_HISTORY = PROJECT_DIR / "data" / "trade_history.json"
# members.db lives at the repo root (post-2025 reorganization), not inside
# data/vilona_tradefx/ as the old scripts/dashboard_server.py assumed.
MEMBERS_DB = PROJECT_DIR / "members.db"


def _resolve_members_db() -> Path:
    """Return the first existing members.db location.

    Tries repo root first (current schema location), then falls back to
    data/vilona_tradefx/ (legacy location from the pre-unification server).
    """
    if MEMBERS_DB.exists():
        return MEMBERS_DB
    legacy = DATA_DIR / "members.db"
    if legacy.exists():
        return legacy
    return MEMBERS_DB  # default to current location; caller will handle error


def _resolve_payment_db() -> Path:
    """Return members.db with actual payment data (legacy takes priority)."""
    legacy = DATA_DIR / "members.db"
    if legacy.exists():
        return legacy
    return MEMBERS_DB


def get_backtest_data() -> dict:
    """Load backtest results from saved JSON files.

    Returns dict with keys: xauusd (dict|None), grid (list), per_engine (dict|None).
    """
    result: dict = {"xauusd": None, "grid": [], "per_engine": None}
    try:
        bt_file = DATA_DIR / "backtest_xauusd_3m.json"
        if bt_file.exists():
            raw = json.loads(bt_file.read_text())
            result["xauusd"] = {
                "symbol": raw.get("config", {}).get("symbol", "XAUUSD"),
                "period": (
                    f"{raw.get('config', {}).get('start', '?')} → "
                    f"{raw.get('config', {}).get('end', '?')}"
                ),
                "signals": raw.get("results", {}).get("total_signals", 0),
                "trades": raw.get("results", {}).get("trades", 0),
                "wins": raw.get("results", {}).get("wins", 0),
                "losses": raw.get("results", {}).get("losses", 0),
                "winrate": raw.get("results", {}).get("winrate", 0),
                "total_pips": raw.get("results", {}).get("total_pips", 0),
                "avg_win": raw.get("results", {}).get("avg_win", 0),
                "avg_loss": raw.get("results", {}).get("avg_loss", 0),
                "config": raw.get("config", {}),
            }
    except Exception as exc:
        LOG.warning("Failed to load backtest_xauusd_3m.json: %s", exc)

    try:
        grid_file = DATA_DIR / "backtest_grid2.json"
        if grid_file.exists():
            result["grid"] = json.loads(grid_file.read_text())
    except Exception as exc:
        LOG.warning("Failed to load backtest_grid2.json: %s", exc)

    try:
        pe_file = DATA_DIR / "backtest_per_engine.json"
        if pe_file.exists():
            result["per_engine"] = json.loads(pe_file.read_text())
    except Exception as exc:
        LOG.warning("Failed to load backtest_per_engine.json: %s", exc)

    return result


def get_donor_list() -> list:
    """Get list of all paying members (donors/supporters).

    Returns list with display_name, amount, paid_at.
    """
    db_path = _resolve_payment_db()
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            """
            SELECT m.chat_id, m.nama, m.username, m.tier, m.joined_at,
                   COALESCE(SUM(p.amount), 0) as tripay_amount,
                   MAX(p.paid_at) as tripay_paid_at,
                   m.payment_ref
            FROM members m
            LEFT JOIN payment_orders p ON m.chat_id = p.chat_id
                AND p.status = 'paid' AND p.amount > 0
            WHERE m.status = 'paid'
              AND m.tier NOT IN ('free', 'trial', 'expired')
            GROUP BY m.chat_id
            ORDER BY m.tier = 'elite' DESC, m.tier = 'pro' DESC, tripay_amount DESC
            """
        )
        rows = c.fetchall()
        # Midtrans tier pricing (matches midtrans_service.py TIER_PRICES)
        TIER_AMOUNT = {"pro": 50000, "elite": 150000, "lifetime": 500000, "premium": 50000, "donor": 15000, "vip": 500000}
        donors = []
        for r in rows:
            d = dict(r)
            display_name = d.get("nama", "") or d.get("username", "") or f"User-{d['chat_id'][:8]}"
            if d.get("username") and not d.get("nama"):
                display_name = f"@{d['username']}"
            tier = d.get("tier", "pro")
            # Prefer actual Tripay amount, fall back to Midtrans tier pricing
            tripay_amt = float(d.get("tripay_amount", 0) or 0)
            midtrans_amt = TIER_AMOUNT.get(tier, 0) if d.get("payment_ref") else 0
            amount = max(tripay_amt, midtrans_amt)  # use whichever has data
            paid_at = str(d.get("tripay_paid_at", "") or "")
            donors.append(
                {
                    "chat_id": d["chat_id"],
                    "display_name": display_name,
                    "amount": amount,
                    "paid_at": paid_at,
                    "tier": tier,
                }
            )
        return donors
    except Exception as exc:
        LOG.warning("get_donor_list failed: %s", exc)
        return [{"error": str(exc)}]
    finally:
        if conn is not None:
            conn.close()


def get_daily_analyze_stats() -> dict:
    """Count analyze requests per day from quota_cache files.

    Returns dict with daily list, total_all_time, today, yesterday.
    """
    quota_dir = DATA_DIR / "quota_cache"
    daily: dict = {}
    try:
        if quota_dir.exists():
            for f in quota_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    date = data.get("date", "")
                    used = data.get("used", 0)
                    if date:
                        daily[date] = daily.get(date, 0) + used
                except Exception as exc:
                    LOG.debug("Skipping quota file %s: %s", f, exc)
    except Exception as exc:
        LOG.warning("Failed to scan quota_cache: %s", exc)

    sorted_daily = sorted(daily.items(), reverse=True)
    total_all_time = sum(daily.values())
    today_str = datetime.now(WIB).strftime("%Y-%m-%d")
    yesterday_str = (datetime.now(WIB) - timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "daily": [{"date": d, "requests": c} for d, c in sorted_daily],
        "total_all_time": total_all_time,
        "today": daily.get(today_str, 0),
        "yesterday": daily.get(yesterday_str, 0),
    }


def get_trade_stats() -> dict:
    """Load stats from trade_history.json.

    Returns zero-shape on missing/corrupt file.
    """
    try:
        if TRADE_HISTORY.exists():
            data = json.loads(TRADE_HISTORY.read_text())
            stats: dict = data.get("stats", {})
            return stats
    except Exception as exc:
        LOG.warning("get_trade_stats failed: %s", exc)
    return {
        "total": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "total_pips": 0.0,
        "win_rate": 0,
        "best_win_pips": 0,
        "worst_loss_pips": 0,
    }


def get_daily_mapping() -> dict:
    """Get latest daily mapping data."""
    mapping_file = DATA_DIR / ".last_mapping_date"
    try:
        if mapping_file.exists():
            date_str = mapping_file.read_text().strip()
            return {"date": date_str, "status": "posted"}
    except Exception as exc:
        LOG.warning("get_daily_mapping failed: %s", exc)
    return {"date": "N/A", "status": "not_posted"}


def get_today_trades() -> list:
    """Get today's trades for stats. Filters by WIB date from open_time."""
    today_iso = datetime.now(WIB).strftime("%Y-%m-%d")
    try:
        if TRADE_HISTORY.exists():
            all_data = json.loads(TRADE_HISTORY.read_text())
            trades = all_data.get("trades", [])
            return [
                t for t in trades
                if str(t.get("open_time", t.get("close_time", "")))[:10] == today_iso
            ]
    except Exception as exc:
        LOG.warning("get_today_trades failed: %s", exc)
    return []


def get_transparency_data() -> dict:
    """Get transparency data for LP: members, revenue, costs.

    Returns dict with member counts, revenue, donation totals, trade stats,
    server cost breakdown, and weekly revenue trend.
    """
    db_path = _resolve_payment_db()
    conn = None
    total_members = 0
    total_donors = 0
    total_pro = 0
    total_free = 0
    paid_count = 0
    total_revenue: float = 0
    donation_count = 0
    donation_total: float = 0
    tier_breakdown: dict = {}
    revenue_trend: list = []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM members")
        total_members = c.fetchone()[0]

        c.execute("SELECT tier, status, COUNT(*) as cnt FROM members GROUP BY tier, status")
        for r in c.fetchall():
            tier_breakdown[f"{r['tier']}_{r['status']}"] = r["cnt"]

        c.execute("SELECT COUNT(*) FROM members WHERE tier='donor'")
        total_donors = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM members WHERE tier='pro' AND status='paid' AND tags NOT LIKE '%test%'")
        total_pro = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM members WHERE tier='starter' AND status='trial'")
        total_free = c.fetchone()[0]

        c.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_orders WHERE status='paid'"
        )
        row = c.fetchone()
        paid_count = row[0]
        total_revenue = float(row[1]) if row[1] else 0

        c.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_orders "
            "WHERE status='paid' AND product_key IN ('vtfx-subscribe','donor')"
        )
        drow = c.fetchone()
        donation_count = drow[0]
        donation_total = float(drow[1]) if drow[1] else 0

        # If manually activated donors exist without payment records, estimate
        c.execute("SELECT COUNT(*) FROM members WHERE tier='donor' AND status='paid' AND tags NOT LIKE '%test%'")
        manual_donors = c.fetchone()[0]
        if manual_donors > donation_count:
            estimated_from_manual = (manual_donors - donation_count) * 50000  # legacy estimate
            if donation_total == 0:
                donation_total = float(estimated_from_manual)
            donation_count = manual_donors

        # Weekly revenue trend
        week_ago = (datetime.now(WIB) - timedelta(days=7)).isoformat()
        c.execute(
            """
            SELECT DATE(paid_at) as day, COUNT(*), COALESCE(SUM(amount),0)
            FROM payment_orders
            WHERE status='paid' AND paid_at >= ?
            GROUP BY DATE(paid_at)
            ORDER BY day DESC
            """,
            (week_ago,),
        )
        for r in c.fetchall():
            revenue_trend.append(
                {"date": r[0], "count": r[1], "amount": float(r[2]) if r[2] else 0}
            )
    except Exception as exc:
        LOG.warning("get_transparency_data SQLite query failed: %s", exc)
    finally:
        if conn is not None:
            conn.close()

    # Feed stats
    try:
        from tradebot.services.signal_service import _load_feed

        feed = _load_feed()
        feed_stats = feed.get("stats", {})
        signals_total = feed_stats.get("total", 0)
    except Exception as exc:
        LOG.debug("signal_feed unavailable: %s", exc)
        signals_total = 0

    # Trade stats
    trade_stats = get_trade_stats()

    # Today's active users (from quota cache)
    today_str = datetime.now(WIB).strftime("%Y-%m-%d")
    active_users = 0
    try:
        quota_dir = DATA_DIR / "quota_cache"
        if quota_dir.exists():
            for f in quota_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    if data.get("date") == today_str:
                        active_users += 1
                except Exception as exc:
                    LOG.debug("Skipping quota file %s: %s", f, exc)
    except Exception as exc:
        LOG.debug("quota scan failed: %s", exc)

    return {
        "total_members": total_members,
        "total_donors": total_donors,
        "total_pro": total_pro,
        "total_free": total_free,
        "tier_breakdown": tier_breakdown,
        "paid_transactions": paid_count,
        "total_revenue_idr": total_revenue,
        "donation_total": donation_total,
        "donation_transactions": donation_count,
        "signals_total": signals_total,
        "active_users_today": active_users,
        "revenue_trend": revenue_trend,
        "trade_total": trade_stats.get("total", 0),
        "trade_wins": trade_stats.get("wins", 0),
        "trade_losses": trade_stats.get("losses", 0),
        "trade_pips": trade_stats.get("total_pips", 0),
        "server_cost_monthly": 450,
        "server_cost_idr": 450 * 16350,
        "api_breakdown": {
            "deepseek_api": 150,
            "openai_api": 120,
            "claude_api": 80,
            "gpu_server": 80,
            "domain_hosting": 20,
        },
    }


def get_fuel_stats() -> dict:
    """Get AI Fuel donation stats for dashboard display.

    Composes from get_transparency_data(). Returns critical/low/medium/healthy
    status based on collected vs monthly_cost percentage.
    """
    try:
        td = get_transparency_data()
        monthly_cost = td.get("server_cost_idr", 7357500)
        collected = td.get("donation_total", 0)
        donors = td.get("total_donors", 0)
        pct = min(100, round((collected / monthly_cost) * 100)) if monthly_cost > 0 else 0
        shortfall = max(0, monthly_cost - collected)
        return {
            "monthly_cost": monthly_cost,
            "collected": collected,
            "donors": donors,
            "donor_count": donors,  # backward compat — handler reads this key
            "percent": pct,
            "shortfall": shortfall,
            "status": (
                "critical"
                if pct < 25
                else "low"
                if pct < 50
                else "medium"
                if pct < 75
                else "healthy"
            ),
        }
    except Exception as exc:
        LOG.warning("get_fuel_stats failed: %s", exc)
        return {
            "monthly_cost": 7357500,
            "collected": 0,
            "donors": 0,
            "percent": 0,
            "shortfall": 7357500,
            "status": "critical",
        }


def save_fuel_report(chat_id: str) -> tuple[dict, int]:
    """Persist a manual transfer report for the given chat_id.

    Dedup: if the same chat_id has already reported, returns the duplicate
    response. Otherwise appends a new pending report (atomic write).

    Returns (response_dict, http_status).
    """
    if not chat_id:
        return {"success": False, "error": "Parameter 'chat_id' diperlukan"}, 400

    _ensure_data_dir()
    report_path = DATA_DIR / ".fuel_reports.json"
    reports: list = []
    if report_path.exists():
        try:
            reports = json.loads(report_path.read_text())
        except Exception as exc:
            LOG.warning("Corrupt .fuel_reports.json, starting fresh: %s", exc)
            reports = []

    existing = [r for r in reports if r.get("chat_id") == chat_id]
    if existing:
        return {"success": True, "message": "Laporan sudah diterima sebelumnya."}, 200

    report = {
        "chat_id": chat_id,
        "timestamp": datetime.now(WIB).isoformat(),
        "status": "pending",
    }
    reports.append(report)

    # Atomic write: write to tmp then rename
    try:
        tmp = report_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(reports, indent=2, default=str))
        tmp.rename(report_path)
    except Exception as exc:
        LOG.error("Failed to write .fuel_reports.json: %s", exc)
        return {"success": False, "error": f"Storage error: {exc}"}, 500

    return {
        "success": True,
        "message": "Laporan diterima. Admin akan aktivasi dalam 1x24 jam.",
    }, 200
