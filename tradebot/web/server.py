"""
Web Admin Dashboard — FastAPI + Jinja2 + Tailwind CSS

Serves:
  /                    Dashboard home (revenue, users, signals)
  /plans               Plan management (view/edit pricing)
  /whitelabels         Whitelabel bots (view/edit revenue shares)
  /affiliates          Affiliate management (view/edit commission rates)
  /signals             Live signal feed
  /api/...             REST API for all data

Run: python -m tradebot.web.server --port 9090
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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

LOG = logging.getLogger("tradebot.web")

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="1ai-trade-bot Admin", version="1.0")
app.add_middleware(SessionMiddleware, secret_key="tradebot-session-secret-key-change-in-prod", max_age=30*24*60*60)
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))



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
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid user ID format"})

    if not _is_admin(user_id):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Access denied. Not an admin user."})

    request.session["admin_user_id"] = user_id
    return RedirectResponse(url="/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

# ── Pages ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    _check_auth(request)
    stats = get_plan_stats()
    revenue = get_total_revenue()
    prices = get_all_plan_prices()
    whitelabels = get_all_active_whitelabels()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "title": "1ai-trade-bot Dashboard",
        "stats": stats,
        "revenue": revenue,
        "prices": prices,
        "plans": PLAN_DETAILS,
        "whitelabels": whitelabels,
        "admin_ids": settings.ADMIN_USER_IDS,
    })


@app.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request):
    _check_auth(request)
    return templates.TemplateResponse("plans.html", {
        "request": request,
        "title": "Plan Management",
        "plans": {p.value: PLAN_DETAILS[p] for p in Plan},
        "prices": get_all_plan_prices(),
    })


@app.get("/whitelabels", response_class=HTMLResponse)
async def whitelabels_page(request: Request):
    _check_auth(request)
    return templates.TemplateResponse("whitelabels.html", {
        "request": request,
        "title": "Whitelabel Management",
        "whitelabels": get_all_active_whitelabels(),
    })


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
    return {"whitelabels": [
        {
            "owner": wl.owner_user_id,
            "username": wl.bot_username,
            "name": wl.custom_name,
            "share": wl.revenue_share,
            "active": wl.active,
        }
        for wl in get_all_active_whitelabels()
    ]}


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
    return check_all()
