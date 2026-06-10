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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

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
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _admin_gate(request: Request) -> None:
    """Check admin access from query param, header, or localhost."""
    admin_ids = [uid.strip() for uid in (settings.ADMIN_USER_IDS or "").split(",") if uid.strip()]
    if "ALL" in [u.upper() for u in admin_ids]:
        return  # Anyone is admin

    # Check localhost first (development)
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return

    # Check query param or header (production with explicit admin ID)
    admin_id = request.query_params.get("admin_id") or request.headers.get("X-Admin-ID")
    if admin_id and _is_admin(admin_id):
        return

    # Deny all others
    raise HTTPException(403, "Admin access required. Use from localhost or pass ?admin_id=YOUR_ID or X-Admin-ID header.")


def _is_admin(user_id: str) -> bool:
    admin_ids = [uid.strip() for uid in (settings.ADMIN_USER_IDS or "").split(",") if uid.strip()]
    return user_id in admin_ids or "ALL" in [u.upper() for u in admin_ids]


# ── Pages ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    _admin_gate(request)
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
    _admin_gate(request)
    return templates.TemplateResponse("plans.html", {
        "request": request,
        "title": "Plan Management",
        "plans": {p.value: PLAN_DETAILS[p] for p in Plan},
        "prices": get_all_plan_prices(),
    })


@app.get("/whitelabels", response_class=HTMLResponse)
async def whitelabels_page(request: Request):
    _admin_gate(request)
    return templates.TemplateResponse("whitelabels.html", {
        "request": request,
        "title": "Whitelabel Management",
        "whitelabels": get_all_active_whitelabels(),
    })


# ── API ───────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats(request: Request):
    _admin_gate(request)
    return {
        "plans": get_plan_stats(),
        "revenue": get_total_revenue(),
        "prices": get_all_plan_prices(),
    }


@app.get("/api/whitelabels")
async def api_whitelabels(request: Request):
    _admin_gate(request)
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
        _admin_gate(request)
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
        _admin_gate(request)
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
        _admin_gate(request)
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
