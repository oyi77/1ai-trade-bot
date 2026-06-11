"""
Web Dashboard — FastAPI server for trade monitoring + whitelabel management.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Routes:
- GET / → dashboard home (live trades, stats, active whitelabels)
- GET /whitelabels → whitelabel management page
- POST /api/whitelabel/create → create new whitelabel
- POST /api/whitelabel/toggle → toggle feature flags
- GET /api/trades → JSON trade list
- GET /api/stats → JSON live statistics
- GET /health → health check
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse

from agent.database import (
    init_db, get_all_whitelabels, get_whitelabel, create_whitelabel,
    update_whitelabel_features, set_whitelabel_active,
    get_open_trades, get_all_trades, get_stats,
    WIB,
)

LOG = logging.getLogger("agent.web")

app = FastAPI(title="1AI Agent Dashboard", version="1.0.0")


@app.on_event("startup")
async def startup():
    init_db()
    LOG.info("Web dashboard initialized")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "1AI Agent Dashboard", "version": "1.0.0"}


@app.get("/api/stats")
async def api_stats(user_id: str = Query("")):
    stats = get_stats(user_id if user_id else None)
    open_count = len(get_open_trades(user_id if user_id else None))
    stats["open_positions"] = open_count
    return stats


@app.get("/api/trades")
async def api_trades(user_id: str = Query(""), limit: int = Query(50)):
    trades = get_all_trades(user_id if user_id else None, limit)
    return {"trades": trades, "count": len(trades)}


@app.get("/api/open-trades")
async def api_open_trades(user_id: str = Query(""), whitelabel_id: int = Query(0)):
    trades = get_open_trades(
        user_id if user_id else None,
        whitelabel_id if whitelabel_id > 0 else None,
    )
    return {"trades": trades, "count": len(trades)}


@app.post("/api/whitelabel/create")
async def api_create_whitelabel(
    name: str = Form(...),
    bot_token: str = Form(""),
    features: str = Form("ALL"),
):
    result = create_whitelabel(name, bot_token, features)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/whitelabel/features")
async def api_set_features(
    name: str = Form(...),
    features: str = Form("ALL"),
):
    ok = update_whitelabel_features(name, features)
    if not ok:
        return JSONResponse({"error": f"Whitelabel '{name}' not found"}, status_code=404)
    return {"ok": True, "name": name, "features": features}


@app.post("/api/whitelabel/active")
async def api_set_active(
    name: str = Form(...),
    active: bool = Form(True),
):
    ok = set_whitelabel_active(name, active)
    return {"ok": ok, "name": name, "active": active}


@app.get("/whitelabels", response_class=HTMLResponse)
async def whitelabels_page():
    whitelabels = get_all_whitelabels()
    rows = ""
    for wl in whitelabels:
        feats = wl.get("features", "ALL")
        active = "🟢" if wl.get("is_active") else "🔴"
        rows += f"""
        <tr>
            <td>{wl.get('id', '')}</td>
            <td>{wl.get('name', '')}</td>
            <td>{feats}</td>
            <td>{active}</td>
            <td>
                <form method="POST" action="/api/whitelabel/features" style="display:inline">
                    <input type="hidden" name="name" value="{wl.get('name', '')}">
                    <select name="features">
                        <option value="ALL" {"selected" if feats == "ALL" else ""}>ALL</option>
                        <option value="FOREX" {"selected" if feats == "FOREX" else ""}>FOREX</option>
                        <option value="STOCKITY" {"selected" if feats == "STOCKITY" else ""}>STOCKITY</option>
                        <option value="DERIV" {"selected" if feats == "DERIV" else ""}>DERIV</option>
                        <option value="CRYPTO" {"selected" if feats == "CRYPTO" else ""}>CRYPTO</option>
                    </select>
                    <button type="submit">Update</button>
                </form>
            </td>
        </tr>"""

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>1AI Agent — Whitelabel Management</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   background: #0f0f1a; color: #e0e0e0; padding: 20px; }}
            h1 {{ color: #00ff88; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            th, td {{ padding: 10px 15px; text-align: left; border-bottom: 1px solid #2a2a3a; }}
            th {{ background: #1a1a2e; color: #888; font-weight: 600; text-transform: uppercase; font-size: 12px; }}
            tr:hover {{ background: #1a1a2e; }}
            select, button, input {{
                padding: 6px 12px; border-radius: 6px; border: 1px solid #2a2a3a;
                background: #1a1a2e; color: #e0e0e0; font-size: 13px;
            }}
            button {{ background: #00ff88; color: #0f0f1a; font-weight: 600; cursor: pointer; border: none; }}
            button:hover {{ background: #00cc66; }}
            .section {{ margin-bottom: 30px; background: #16162a; padding: 20px; border-radius: 12px; }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }}
            .stat-card {{ background: #1a1a2e; padding: 15px; border-radius: 8px; text-align: center; }}
            .stat-value {{ font-size: 24px; font-weight: bold; color: #00ff88; }}
            .stat-label {{ font-size: 12px; color: #888; margin-top: 5px; }}
            a {{ color: #00ff88; text-decoration: none; }}
            .feature-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
                           background: #2a2a3a; font-size: 11px; margin: 2px; }}
        </style>
    </head>
    <body>
        <h1>⚙️ Whitelabel Management</h1>

        <div class="section">
            <h2>Create Whitelabel</h2>
            <form method="POST" action="/api/whitelabel/create" style="display:flex;gap:10px;flex-wrap:wrap">
                <input type="text" name="name" placeholder="Name (e.g. vilona-tradefx)" required>
                <input type="text" name="bot_token" placeholder="Bot token (optional)">
                <select name="features">
                    <option value="ALL">ALL Markets</option>
                    <option value="FOREX">FOREX Only</option>
                    <option value="STOCKITY">STOCKITY Only</option>
                    <option value="DERIV">DERIV Only</option>
                    <option value="CRYPTO">CRYPTO Only</option>
                    <option value="STOCKITY,DERIV">STOCKITY + DERIV</option>
                    <option value="FOREX,CRYPTO">FOREX + CRYPTO</option>
                </select>
                <button type="submit">Create</button>
            </form>
        </div>

        <div class="section">
            <h2>Whitelabels ({len(whitelabels)})</h2>
            <table>
                <tr><th>ID</th><th>Name</th><th>Features</th><th>Active</th><th>Actions</th></tr>
                {rows}
            </table>
        </div>

        <p><a href="/">← Back to Dashboard</a></p>
    </body>
    </html>
    """)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    stats = get_stats()
    open_trades = get_open_trades()
    recent_trades = get_all_trades(limit=10)
    whitelabels = get_all_whitelabels()

    open_rows = ""
    for t in open_trades[:10]:
        emoji = "🟢" if t.get("direction") == "BUY" else "🔴"
        open_rows += f"""
        <tr>
            <td>{emoji} {t.get('symbol', '')}</td>
            <td>{t.get('direction', '')}</td>
            <td>{t.get('entry_price', 0):.2f}</td>
            <td>{t.get('stop_loss', 0):.2f}</td>
            <td>{t.get('take_profit_1', 0):.2f}</td>
            <td>{t.get('user_id', '')}</td>
        </tr>"""

    recent_rows = ""
    for t in recent_trades[:10]:
        emoji = "✅" if t.get("outcome", "").startswith("TP") else "❌" if t.get("outcome") == "SL_HIT" else "⚪"
        recent_rows += f"""
        <tr>
            <td>{emoji} {t.get('symbol', '')}</td>
            <td>{t.get('direction', '')}</td>
            <td>{t.get('outcome', '')}</td>
            <td>{t.get('pips', 0):+.1f}</td>
            <td>${t.get('profit_usd', 0):+.2f}</td>
            <td>{t.get('user_id', '')}</td>
        </tr>"""

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>1AI Agent Dashboard</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                   background: #0f0f1a; color: #e0e0e0; padding: 20px; }}
            h1 {{ color: #00ff88; margin-bottom: 10px; }}
            h2 {{ color: #ccc; margin: 20px 0 10px; font-size: 16px; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #2a2a3a; font-size: 13px; }}
            th {{ background: #1a1a2e; color: #888; font-weight: 600; text-transform: uppercase; font-size: 11px; }}
            tr:hover {{ background: #1a1a2e; }}
            .section {{ margin-bottom: 20px; background: #16162a; padding: 15px; border-radius: 12px; }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; }}
            .stat-card {{ background: #1a1a2e; padding: 15px; border-radius: 8px; text-align: center; }}
            .stat-value {{ font-size: 22px; font-weight: bold; color: #00ff88; }}
            .stat-label {{ font-size: 11px; color: #888; margin-top: 4px; }}
            .red {{ color: #ff4444 !important; }}
            .green {{ color: #00ff88 !important; }}
            nav {{ display: flex; gap: 15px; margin-bottom: 20px; }}
            nav a {{ color: #888; text-decoration: none; padding: 8px 16px; border-radius: 8px;
                    background: #1a1a2e; font-size: 13px; }}
            nav a:hover {{ background: #2a2a3a; color: #fff; }}
        </style>
    </head>
    <body>
        <nav>
            <a href="/">📊 Dashboard</a>
            <a href="/whitelabels">⚙️ Whitelabels</a>
        </nav>

        <h1>🤖 1AI Agent Dashboard</h1>

        <div class="section">
            <div class="stats">
                <div class="stat-card">
                    <div class="stat-value {'green' if stats.get('win_rate', 0) >= 50 else 'red'}">{stats.get('win_rate', 0):.1f}%</div>
                    <div class="stat-label">Win Rate</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{stats.get('total', 0)}</div>
                    <div class="stat-label">Total Trades</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value green">{stats.get('wins', 0)}</div>
                    <div class="stat-label">Wins</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value red">{stats.get('losses', 0)}</div>
                    <div class="stat-label">Losses</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value green">{stats.get('total_pips', 0):+.1f}</div>
                    <div class="stat-label">Total Pips</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value green">${stats.get('total_profit', 0):+,.2f}</div>
                    <div class="stat-label">P&L</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{len(open_trades)}</div>
                    <div class="stat-label">Open Positions</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{len(whitelabels)}</div>
                    <div class="stat-label">Whitelabels</div>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>🟢 Open Positions ({len(open_trades)})</h2>
            <table>
                <tr><th>Symbol</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP</th><th>User</th></tr>
                {open_rows if open_rows else '<tr><td colspan="6" style="text-align:center;color:#666">No open positions</td></tr>'}
            </table>
        </div>

        <div class="section">
            <h2>📜 Recent Trades</h2>
            <table>
                <tr><th>Symbol</th><th>Dir</th><th>Outcome</th><th>Pips</th><th>P&L</th><th>User</th></tr>
                {recent_rows if recent_rows else '<tr><td colspan="6" style="text-align:center;color:#666">No trades yet</td></tr>'}
            </table>
        </div>
    </body>
    </html>
    """)
