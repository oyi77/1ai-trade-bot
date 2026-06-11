"""
Web Admin Dashboard — FastAPI + Jinja2 + Tailwind CSS

Public routes (no auth):
  /                    302 → /landing
  /landing             Marketing landing page
  /dashboard           302 → /dashboard/en or /dashboard/id (Accept-Language)
  /dashboard/en        Public signal dashboard (English)
  /dashboard/id        Public signal dashboard (Indonesian)
  /dashboard/bilingual Public signal dashboard (bilingual)
  /signals             Public signals feed
  /api/feed            Recent signals (channel + user)
  /api/user_activity   User-generated signals
  /api/feed/stats      Feed stats (win rate, total pips)
  /api/trade_stats     Trade history stats
  /api/mapping         Daily mapping data
  /api/today_trades    Today's trades
  /api/transparency    LP transparency data
  /api/backtest        Backtest results
  /api/donors          Donor list
  /api/daily_analyze   Daily analyze request counts
  /api/daily_recap     Daily trade recap
  /api/fuel/create     Tripay donation payment (POST)
  /api/fuel/stats      AI Fuel donation progress
  /api/fuel/report     Manual transfer report (POST)

Admin routes (session auth required):
  /login               Admin login
  /admin               Dashboard home (revenue, users, signals)
  /admin/plans         Plan management (view/edit pricing)
  /admin/whitelabels   Whitelabel bots (view/edit revenue shares)
  /logout              Clear session

Bridge (admin):
  /api/bridge/*        Signal bridge state
  /health              Health check

Run: python -m tradebot.web.server --port 9090
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from tradebot.bots.stockity.affiliate import (
    get_all_active_whitelabels,
    set_affiliate_rate,
    set_whitelabel_share,
)
from tradebot.config import settings
from tradebot.services.plans import (
    PLAN_DETAILS,
    Plan,
    get_all_plan_prices,
    get_plan_stats,
    get_total_revenue,
    set_plan_price,
)
from tradebot.web.monitoring_api import router as monitoring_router
from tradebot.web.public_dashboard import (
    get_backtest_data as _get_backtest_data,
)
from tradebot.web.public_dashboard import (
    get_daily_analyze_stats as _get_daily_analyze_stats,
)
from tradebot.web.public_dashboard import (
    get_daily_mapping as _get_daily_mapping,
)
from tradebot.web.public_dashboard import (
    get_donor_list as _get_donor_list,
)
from tradebot.web.public_dashboard import (
    get_fuel_stats as _get_fuel_stats,
)
from tradebot.web.public_dashboard import (
    get_today_trades as _get_today_trades,
)
from tradebot.web.public_dashboard import (
    get_trade_stats as _get_trade_stats,
)
from tradebot.web.public_dashboard import (
    get_transparency_data as _get_transparency_data,
)
from tradebot.web.public_dashboard import (
    save_fuel_report as _save_fuel_report,
)

LOG = logging.getLogger("tradebot.web")

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="1ai-trade-bot Admin", version="1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key="tradebot-session-secret-key-change-in-prod",
    max_age=30 * 24 * 60 * 60,
)

# Wire monitoring API router (no auth — internal/loopback only by default)
app.include_router(monitoring_router)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# ═══════════════════════════════════════════════════════════
#  LIVE SNAPSHOT CACHE — populated by worker webhook push
# ═══════════════════════════════════════════════════════════
_live_snapshot: dict = {}
_live_snapshot_lock = threading.Lock()
SNAPSHOT_FALLBACK = {
    "type": "dashboard_snapshot",
    "status": {"state": "connecting", "pair": "XAUUSD", "detail": "Waiting for worker..."},
    "performance": {"win_rate": 0.0, "total_pnl": 0.0},
    "users": {"active": 0, "bot_users": 0},
    "prices": {"XAUUSD": None},
    "uptime_seconds": 0,
    "total_cycles": 0,
}


def _is_admin(user_id: str) -> bool:
    """Check if user_id is in ADMIN_USER_IDS."""
    admin_ids = [uid.strip() for uid in (settings.ADMIN_USER_IDS or "").split(",") if uid.strip()]
    return user_id in admin_ids or "ALL" in [u.upper() for u in admin_ids]


def _require_login(request: Request) -> str | None:
    """Get admin user_id from session. Returns None if not authenticated."""
    user_id = request.session.get("admin_user_id")
    if user_id and _is_admin(user_id):
        return user_id
    return None


def _require_login_or_redirect(request: Request) -> str | RedirectResponse:
    """Get admin user_id from session. If not authenticated, return redirect to login."""
    user_id = _require_login(request)
    if user_id:
        return user_id
    return RedirectResponse(url="/login", status_code=302)


def _check_auth(request: Request) -> str:
    """Check authentication and raise HTTPException with redirect if needed."""
    user_id = _require_login(request)
    if user_id:
        return user_id
    # FastAPI will handle the Location header properly
    raise HTTPException(status_code=302, detail="Unauthorized", headers={"Location": "/login"})


# ── Auth Routes ──────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None):
    """Render login page."""
    if _require_login(request):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, user_id: str = Form(...)):
    """Handle login form submission."""
    user_id = user_id.strip()
    if not user_id.isdigit():
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid user ID format"}
        )

    if not _is_admin(user_id):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Access denied. Not an admin user."}
        )

    request.session["admin_user_id"] = user_id
    return RedirectResponse(url="/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ── Admin Pages ────────────────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    _check_auth(request)
    stats = get_plan_stats()
    revenue = get_total_revenue()
    prices = get_all_plan_prices()
    whitelabels = get_all_active_whitelabels()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "title": "1ai-trade-bot Admin Dashboard",
            "stats": stats,
            "revenue": revenue,
            "prices": prices,
            "plans": PLAN_DETAILS,
            "whitelabels": whitelabels,
            "admin_ids": settings.ADMIN_USER_IDS,
        },
    )


@app.get("/admin/plans", response_class=HTMLResponse)
async def admin_plans_page(request: Request):
    _check_auth(request)
    return templates.TemplateResponse(
        "plans.html",
        {
            "request": request,
            "title": "Plan Management",
            "plans": {p.value: PLAN_DETAILS[p] for p in Plan},
            "prices": get_all_plan_prices(),
        },
    )


@app.get("/admin/whitelabels", response_class=HTMLResponse)
async def admin_whitelabels_page(request: Request):
    _check_auth(request)
    return templates.TemplateResponse(
        "whitelabels.html",
        {
            "request": request,
            "title": "Whitelabel Management",
            "whitelabels": get_all_active_whitelabels(),
        },
    )


@app.get("/admin/monitoring", response_class=HTMLResponse)
async def admin_monitoring_page(request: Request):
    """Real-time system monitoring dashboard (admin only)."""
    _check_auth(request)
    return templates.TemplateResponse(
        "admin_monitoring.html",
        {
            "request": request,
            "title": "System Monitoring",
        },
    )


# ── Public Pages (no auth) ─────────────────────────────────────────────
@app.get("/")
async def root_redirect():
    """Root: redirect to public landing (preserves old scripts/dashboard_server.py behavior)."""
    return RedirectResponse(url="/landing", status_code=302)


@app.get("/landing", response_class=FileResponse)
async def landing_page():
    """Public marketing landing page (served as raw HTML — no Jinja parsing)."""
    return FileResponse(
        path=str(TEMPLATE_DIR / "landing.html"),
        media_type="text/html",
    )


@app.get("/signals", response_class=HTMLResponse)
async def signals_page(request: Request):
    """Public signals feed (alias for /dashboard)."""
    return templates.TemplateResponse(
        "public_dashboard.html",
        {
            "request": request,
            "title": "Vilona TradeFX — AI Signal Dashboard",
        },
    )


@app.get("/dashboard", response_class=RedirectResponse)
async def dashboard_redirect(request: Request):
    """Match old scripts/dashboard_server.py:646-660 Accept-Language redirect."""
    accept_lang = request.headers.get("accept-language", "")
    target = "/dashboard/id" if "id" in accept_lang.lower() else "/dashboard/en"
    return RedirectResponse(url=target, status_code=302)


@app.get("/dashboard/en", response_class=HTMLResponse)
async def dashboard_en(request: Request):
    """Public signal dashboard (English)."""
    return templates.TemplateResponse(
        "public_dashboard_en.html",
        {"request": request, "title": "Vilona TradeFX — AI Signal Dashboard"},
    )


@app.get("/dashboard/id", response_class=HTMLResponse)
async def dashboard_id(request: Request):
    """Public signal dashboard (Indonesian)."""
    return templates.TemplateResponse(
        "public_dashboard_id.html",
        {"request": request, "title": "Vilona TradeFX — Dasbor Sinyal AI"},
    )


@app.get("/dashboard/bilingual", response_class=HTMLResponse)
async def dashboard_bilingual(request: Request):
    """Public signal dashboard (bilingual)."""
    return templates.TemplateResponse(
        "public_dashboard_bilingual.html",
        {"request": request, "title": "Vilona TradeFX — AI Signal Dashboard"},
    )


# ═══════════════════════════════════════════════════════════
#  Webhook Receiver — Worker pushes dashboard_snapshot here
# ═══════════════════════════════════════════════════════════


@app.post("/api/webhook/snapshot")
async def webhook_receive_snapshot(request: Request):
    """Receive dashboard_snapshot from autonomous worker and cache in memory."""
    global _live_snapshot
    try:
        body = await request.json()
        with _live_snapshot_lock:
            _live_snapshot = body
        LOG.debug(
            "Snapshot received: type=%s cycles=%s", body.get("type"), body.get("total_cycles")
        )
        return {"ok": True}
    except Exception as exc:
        LOG.warning("webhook_receive_snapshot parse failed: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)


@app.get("/api/live-snapshot")
async def api_live_snapshot():
    """Serve latest dashboard_snapshot to frontend with fallback."""
    with _live_snapshot_lock:
        snap = dict(_live_snapshot) if _live_snapshot else dict(SNAPSHOT_FALLBACK)
    return snap


# ── Public APIs (no auth) ──────────────────────────────────────────────
@app.get("/api/feed")
async def api_feed():
    """Recent signals (channel + user)."""
    from tradebot.services.signal_service import get_recent_signals

    return {"signals": get_recent_signals(50)}


@app.get("/api/user_activity")
async def api_user_activity():
    """User-generated signals."""
    from tradebot.services.signal_service import get_user_signals

    return {"signals": get_user_signals(50)}


@app.get("/api/feed/stats")
async def api_feed_stats():
    """Public feed stats (renamed from old /api/stats to avoid admin API collision)."""
    from tradebot.services.signal_service import get_stats

    return get_stats()


@app.get("/api/trade_stats")
async def api_trade_stats():
    """Trade history summary stats."""
    return _get_trade_stats()


@app.get("/api/mapping")
async def api_mapping():
    """Daily mapping data."""
    return _get_daily_mapping()


@app.get("/api/today_trades")
async def api_today_trades():
    """Today's trades."""
    return {"trades": _get_today_trades()}


@app.get("/api/transparency")
async def api_transparency():
    """LP transparency data."""
    return _get_transparency_data()


@app.get("/api/backtest")
async def api_backtest():
    """Backtest results (XAUUSD, grid, per-engine)."""
    return _get_backtest_data()


@app.get("/api/donors")
async def api_donors():
    """Donor list."""
    return {"donors": _get_donor_list()}


@app.get("/api/daily_analyze")
async def api_daily_analyze():
    """Per-day analyze request counts."""
    return _get_daily_analyze_stats()


@app.get("/api/daily_recap")
async def api_daily_recap():
    """Daily trade recap from trade_tracker."""
    try:
        from tradebot.services.trade_tracker_service import get_daily_trades

        return get_daily_trades()
    except Exception as exc:
        LOG.warning("api_daily_recap failed: %s", exc)
        return {"error": str(exc), "trades": [], "total_signals": 0}


@app.get("/api/fuel/stats")
async def api_fuel_stats():
    """AI Fuel donation progress."""
    return _get_fuel_stats()


@app.post("/api/fuel/create")
async def api_fuel_create(
    amount: int,
    chat_id: str = "web",
    username: str = "Guest",
):
    """Create a Tripay donation payment for AI Fuel."""
    if amount < 10000:
        return JSONResponse({"success": False, "error": "Minimum donasi Rp10.000"}, status_code=400)
    try:
        from tradebot.services.payment import create_tripay_payment

        result = await create_tripay_payment(
            chat_id=chat_id, username=username, tier="donor", amount=amount
        )
        if result.get("success"):
            return result
        return JSONResponse(result, status_code=500)
    except Exception as exc:
        LOG.warning("api_fuel_create failed: %s", exc)
        return JSONResponse(
            {"success": False, "error": f"Tripay error: {str(exc)[:200]}"},
            status_code=500,
        )


@app.post("/api/fuel/report")
async def api_fuel_report(chat_id: str):
    """Receive a manual transfer confirmation from the website (dedup by chat_id)."""
    body, status = _save_fuel_report(chat_id)
    return JSONResponse(body, status_code=status)


# ── API ───────────────────────────────────────────────────────────────


@app.get("/api/stats")
async def api_stats(request: Request):
    _check_auth(request)
    return {
        "plans": get_plan_stats(),
        "revenue": get_total_revenue(),
        "prices": get_all_plan_prices(),
    }


@app.get("/api/whitelabels")
async def api_whitelabels(request: Request):
    _check_auth(request)
    return {
        "whitelabels": [
            {
                "owner": wl.owner_user_id,
                "username": wl.bot_username,
                "name": wl.custom_name,
                "share": wl.revenue_share,
                "active": wl.active,
            }
            for wl in get_all_active_whitelabels()
        ]
    }


@app.post("/api/plan/set")
async def api_set_plan(
    request: Request,
    plan: str = Form(...),
    price: int = Form(...),
    admin_id: str = Form(""),
):
    if not _is_admin(admin_id):
        _check_auth(request)
    try:
        p = Plan(plan)
    except ValueError:
        raise HTTPException(400, f"Invalid plan: {plan}")
    set_plan_price(p, price)
    return JSONResponse({"ok": True, "plan": plan, "price": price})


@app.post("/api/whitelabel/share")
async def api_set_share(
    request: Request,
    user_id: str = Form(...),
    share: float = Form(...),
    admin_id: str = Form(""),
):
    if not _is_admin(admin_id):
        _check_auth(request)
    set_whitelabel_share(user_id, share)
    return JSONResponse({"ok": True, "user_id": user_id, "share": share})


@app.post("/api/affiliate/rate")
async def api_set_affiliate_rate(
    request: Request,
    user_id: str = Form(...),
    rate: float = Form(...),
    admin_id: str = Form(""),
):
    if not _is_admin(admin_id):
        _check_auth(request)
    set_affiliate_rate(user_id, rate)
    return JSONResponse({"ok": True, "user_id": user_id, "rate": rate})


# ── Bridge API (merged from tradebot/services/bridge_server.py) ──────

_bridge_state: dict = {}


def set_bridge_state(state: dict) -> None:
    """Update bridge state from external signal pipelines."""
    _bridge_state.update(state)


@app.get("/api/bridge/signal")
async def bridge_signal():
    return _bridge_state.get("signal", {"status": "no_signal"})


@app.get("/api/bridge/status")
async def bridge_status():
    return {
        "status": "ok",
        "engine_count": len(_bridge_state.get("engines", [])),
        "connected": _bridge_state.get("connected", False),
    }


@app.get("/api/bridge/balance")
async def bridge_balance():
    return _bridge_state.get("balance", {"balance": None})


# ── Health ─────────────────────────────────────────────────────────


@app.get("/health")
async def health_check():
    from tradebot.services.health import check_all

    report = await check_all()
    return report
