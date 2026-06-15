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
  /api/donors          Subscriber list
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

import json
import logging
import threading
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
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
from tradebot.web.bridge_api import router as bridge_router
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
from tradebot.web.tracking_api import router as tracking_router
from tradebot.api.trade_webhook import router as trade_webhook_router

LOG = logging.getLogger("tradebot.web")

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
app = FastAPI(title="1ai-trade-bot Admin", version="1.0")
app.add_middleware(
    SessionMiddleware,
    secret_key="tradebot-session-secret-key-change-in-prod",
    max_age=30 * 24 * 60 * 60,
)

# Wire monitoring API router (no auth — internal/loopback only by default)
app.include_router(monitoring_router)
# Wire bridge API router (MT5 EA signal polling)
app.include_router(bridge_router)
app.include_router(tracking_router)  # FB Ads tracking pixel
app.include_router(trade_webhook_router)  # Vilona EA trade-close webhook
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


# ── Auth Routes (EXCLUSIVE FOR ADMINS — Public dashboard is strictly read-only) ──

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

# ── EA Download (key-prefilled .set bundle) ──
EA_BINARY_PATH = Path(__file__).resolve().parent.parent.parent / "ea" / "VilonaTradeFX_EA.ex5"


@app.get("/download/ea")
async def download_ea_static():
    """Static EA binary download (no key — for anonymous visitors)."""
    if not EA_BINARY_PATH.exists():
        return FileResponse(status_code=404)
    return FileResponse(
        path=str(EA_BINARY_PATH),
        media_type="application/octet-stream",
        filename="VilonaTradeFX_EA.ex5",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/ea/download/{key}")
async def download_ea_with_key(key: str):
    """EA download with prefilled license key.

    Returns ZIP with EA binary + .set file containing the key.
    User extracts both to MQL5/Experts/ → Load preset → key auto-filled.

    Usage:
        /ea/download/VT-A1B2C3D4E5F6
    """
    from tradebot.web.bridge_api import EA_BINARY_PATH, _validate_key

    is_valid, _ = _validate_key(key)
    if not is_valid:
        return {"error": "invalid_api_key"}

    if not EA_BINARY_PATH.exists():
        return {"error": "EA binary not found"}

    binary_data = EA_BINARY_PATH.read_bytes()
    set_content = (
        "[Experts]\n"
        f"API_Key={key}\n"
    )

    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("VilonaTradeFX_EA.ex5", binary_data)
        zf.writestr("VilonaTradeFX_EA.set", set_content)

    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="VilonaTradeFX_EA_{key[:8]}.zip"',
        },
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


# ── Backward compat: old /id and /en routes (bridge proxies these) ──


@app.get("/en", response_class=RedirectResponse)
async def compat_dashboard_en():
    return RedirectResponse(url="/dashboard/en", status_code=302)


@app.get("/id", response_class=RedirectResponse)
async def compat_dashboard_id():
    return RedirectResponse(url="/dashboard/id", status_code=302)


@app.get("/dashboard/en", response_class=HTMLResponse)
async def dashboard_en():
    """Public signal dashboard (English) — pure static HTML."""
    return HTMLResponse(TEMPLATE_DIR.joinpath("public_dashboard_en.html").read_text(encoding="utf-8"))


@app.get("/dashboard/id", response_class=HTMLResponse)
async def dashboard_id():
    """Public signal dashboard (Indonesian) — pure static HTML."""
    return HTMLResponse(TEMPLATE_DIR.joinpath("public_dashboard_id.html").read_text(encoding="utf-8"))


@app.get("/dashboard/bilingual", response_class=HTMLResponse)
async def dashboard_bilingual():
    """Public signal dashboard (bilingual) — pure static HTML."""
    return HTMLResponse(TEMPLATE_DIR.joinpath("public_dashboard_bilingual.html").read_text(encoding="utf-8"))


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


@app.post("/api/webhook/tripay")
async def webhook_tripay(request: Request):
    """Tripay payment callback webhook.

    Idempotent handler: only upgrades on first PAID event.
    Records the payment to payment_orders for accounting.
    """
    from tradebot.services.payment import PaymentService

    try:
        body = await request.body()
        data = json.loads(body) if body else {}

        callback_signature = request.headers.get("X-Callback-Signature", "")
        if not callback_signature:
            return JSONResponse({"success": False, "error": "Missing signature"}, status_code=400)

        svc = PaymentService()
        raw_data = body.decode() if isinstance(body, bytes) else str(body)
        if not svc.verify_tripay_callback(raw_data, callback_signature):
            return JSONResponse({"success": False, "error": "Invalid signature"}, status_code=403)

        merchant_ref = data.get("merchant_ref", "")
        status = data.get("status", "")
        if not merchant_ref or status != "PAID":
            return {"success": True, "skipped": True}

        import sqlite3
        from pathlib import Path as _Path

        from tradebot.services.members_service import upgrade_tier

        # Parse merchant_ref: new=VTFX-pro-12345678-ts, old=VTFX-12345678-ts
        parts = merchant_ref.split("-")
        if len(parts) >= 4 and parts[1] in ("pro", "elite", "lifetime"):
            tier = parts[1]
            chat_id = parts[2]
        elif len(parts) >= 3:
            tier = "pro"  # legacy fallback
            chat_id = parts[1]
        else:
            tier = "pro"
            chat_id = ""
        db_path = _Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx" / "members.db"

        # deduplicate: skip if already paid
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT status FROM payment_orders WHERE merchant_ref = ?",
                (merchant_ref,),
            ).fetchone()
            if existing and existing["status"] == "paid":
                conn.close()
                return {"success": True, "duplicate": True}
        except Exception as e:
            LOG.warning("Silent exception caught: %s", e)

        amount = data.get("amount", 0)
        paid_at = data.get("paid_at") or __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()

        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT OR REPLACE INTO payment_orders (merchant_ref, chat_id, amount, status, paid_at) "
                "VALUES (?, ?, ?, 'paid', ?)",
                (merchant_ref, chat_id, amount, paid_at),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            LOG.warning("payment_orders insert failed: %s", exc)

        if chat_id:
            # Map tier → days
            tier_days = {"pro": 30, "elite": 30, "lifetime": 9999}
            days = tier_days.get(tier, 30)
            upgrade_tier(chat_id, tier, days, merchant_ref)
            LOG.info("Tripay payment PAID: %s → user %s (%s tier)", merchant_ref, chat_id, tier)

        return {"success": True}
    except Exception as exc:
        LOG.error("Tripay webhook error: %s", exc)
        return JSONResponse({"success": False, "error": str(exc)[:200]}, status_code=500)



@app.get("/api/live-snapshot")
async def api_live_snapshot():
    """Serve latest dashboard_snapshot to frontend with live-data fallback.

    Priority: cached worker push → live computation from real data.
    """
    with _live_snapshot_lock:
        if _live_snapshot:
            return dict(_live_snapshot)

    # No worker snapshot — compute live from real data sources
    from datetime import datetime, timezone, timedelta

    from tradebot.services.signal_service import get_stats as _signal_stats

    WIB = timezone(timedelta(hours=7))
    today_str = datetime.now(WIB).strftime("%Y-%m-%d")

    # Trade performance
    trade_stats = _get_trade_stats()
    total_trades = trade_stats.get("total", 0)
    wins = trade_stats.get("wins", 0)
    losses = trade_stats.get("losses", 0)
    win_rate = round(trade_stats.get("win_rate", 0), 1)
    total_pips = round(trade_stats.get("total_pips", 0), 1)
    total_pnl = round(trade_stats.get("total_profit_usd", 0), 1)

    # Active users today (quota_cache files with today's date)
    quota_dir = DATA_DIR / "quota_cache"
    active_today = 0
    bot_users = 0
    try:
        if quota_dir.exists():
            for f in quota_dir.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    if data.get("date") == today_str:
                        active_today += 1
                except Exception:
                    pass
            bot_users = len(list(quota_dir.glob("*.json")))
    except Exception:
        pass

    # Member counts from transparency
    transparency = _get_transparency_data()
    tier_bd = transparency.get("tier_breakdown", {})

    # Last XAUUSD price from trade history
    xauusd_price = None
    try:
        th_file = PROJECT_DIR / "data" / "trade_history.json"
        if th_file.exists():
            th = json.loads(th_file.read_text())
            trades_list = th.get("trades", [])
            if trades_list:
                last_trade = trades_list[-1]
                xauusd_price = last_trade.get("close_price") or last_trade.get("entry_price")
    except Exception:
        pass

    # Signal stats
    sig_stats = _signal_stats()

    return {
        "type": "dashboard_snapshot",
        "status": {
            "state": "live",
            "pair": "XAUUSD",
            "detail": f"{total_trades} trades · {active_today} active today",
        },
        "performance": {
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "total_pips": total_pips,
        },
        "users": {
            "active": active_today,
            "bot_users": bot_users,
            "total_paid": tier_bd.get("elite_paid", 0)
            + tier_bd.get("pro_paid", 0)
            + tier_bd.get("donor_paid", 0),
            "total_members": transparency.get("total_members", 0),
            "tiers": {
                "elite": tier_bd.get("elite_paid", 0),
                "pro": tier_bd.get("pro_paid", 0),
                "donor": tier_bd.get("donor_paid", 0),
            },
        },
        "prices": {"XAUUSD": xauusd_price},
        "uptime_seconds": 0,
        "total_cycles": total_trades,
        "signal_stats": {
            "total": sig_stats.get("total", 0),
            "tp": sig_stats.get("tp", 0),
            "sl": sig_stats.get("sl", 0),
        },
    }


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
    """Subscriber list."""
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
    """Create a Tripay subscription payment with tier based on amount."""
    if amount < 50000:
        return JSONResponse({"success": False, "error": "Minimum subscribe Rp50.000"}, status_code=400)
    # Map amount to tier
    if amount >= 500000:
        tier = "lifetime"
    elif amount >= 150000:
        tier = "elite"
    else:
        tier = "pro"
    try:
        from tradebot.services.payment import create_tripay_payment

        result = await create_tripay_payment(
            chat_id=chat_id, username=username, tier=tier, amount=amount
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


@app.get("/api/create-payment")
async def api_create_payment(amount: int, method: str = "QRIS2"):
    """Bridge endpoint for landing page — create payment with tier auto-detection."""
    if amount < 50000:
        return JSONResponse({"error": "Minimum Rp50.000"}, status_code=400)
    if amount >= 500000:
        tier = "lifetime"
    elif amount >= 150000:
        tier = "elite"
    else:
        tier = "pro"
    try:
        from tradebot.services.payment import create_tripay_payment
        result = await create_tripay_payment(
            chat_id="web", username="LP-Visitor", tier=tier, amount=amount, method=method
        )
        if result.get("success"):
            return result
        return JSONResponse(result, status_code=500)
    except Exception as exc:
        LOG.warning("api_create_payment failed: %s", exc)
        return JSONResponse({"error": str(exc)[:200]}, status_code=500)


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


# ── Health ─────────────────────────────────────────────────────────


@app.get("/health")
async def health_check():
    from tradebot.services.health import check_all

    report = await check_all()
    return report
