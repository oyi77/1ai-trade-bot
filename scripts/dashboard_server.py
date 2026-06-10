"""
dashboard_server.py — Vilona TradeFX Public Dashboard API

Serves:
- /api/feed — recent signals (channel + user)
- /api/user_activity — user-generated signals with usernames
- /api/stats — win rate, total pips
- /api/mapping — daily mapping data
- / — interactive dashboard HTML

Run: python3 dashboard_server.py --port 8766
"""

from __future__ import annotations
import json, sys, os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

WIB = timezone(timedelta(hours=7))
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data" / "vilona_tradefx"
FEED_FILE = DATA_DIR / "signal_feed.json"
TRADE_HISTORY = PROJECT_DIR / "data" / "trade_history.json"

# Ensure data dir exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Reload feed from signal_feed module
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from signal_feed import get_recent_signals, get_user_signals, get_stats, _load_feed
except ImportError:
    def get_recent_signals(limit=20):
        try:
            data = json.loads(FEED_FILE.read_text()) if FEED_FILE.exists() else {"signals": []}
            return data["signals"][-limit:]
        except: return []
    
    def get_user_signals(limit=20):
        sigs = get_recent_signals(500)
        user_sigs = [s for s in sigs if s.get("source") == "user-generate"]
        return user_sigs[-limit:]
    
    def get_stats():
        try:
            data = json.loads(FEED_FILE.read_text()) if FEED_FILE.exists() else {"stats": {}}
            return data.get("stats", {})
        except: return {}
    
    def _load_feed():
        try:
            return json.loads(FEED_FILE.read_text()) if FEED_FILE.exists() else {"signals": [], "stats": {}}
        except: return {"signals": [], "stats": {}}


def get_backtest_data():
    """Load backtest results from saved JSON files."""
    result = {"xauusd": None, "grid": [], "per_engine": None}
    try:
        bt_file = DATA_DIR / "backtest_xauusd_3m.json"
        if bt_file.exists():
            raw = json.loads(bt_file.read_text())
            result["xauusd"] = {
                "symbol": raw.get("config", {}).get("symbol", "XAUUSD"),
                "period": f"{raw.get('config',{}).get('start','?')} → {raw.get('config',{}).get('end','?')}",
                "signals": raw.get("results", {}).get("total_signals", 0),
                "trades": raw.get("results", {}).get("trades", 0),
                "wins": raw.get("results", {}).get("wins", 0),
                "losses": raw.get("results", {}).get("losses", 0),
                "winrate": raw.get("results", {}).get("winrate", 0),
                "total_pips": raw.get("results", {}).get("total_pips", 0),
                "avg_win": raw.get("results", {}).get("avg_win", 0),
                "avg_loss": raw.get("results", {}).get("avg_loss", 0),
                "config": raw.get("config", {})
            }
    except: pass

    try:
        grid_file = DATA_DIR / "backtest_grid2.json"
        if grid_file.exists():
            result["grid"] = json.loads(grid_file.read_text())
    except: pass

    try:
        pe_file = DATA_DIR / "backtest_per_engine.json"
        if pe_file.exists():
            result["per_engine"] = json.loads(pe_file.read_text())
    except: pass

    return result


def get_donor_list():
    """Get list of donors with their usernames and payment info."""
    conn = None
    try:
        import sqlite3
        db_path = DATA_DIR / "members.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # Donors with their paid payments
        c.execute("""
            SELECT m.chat_id, m.nama, m.username, m.tier, m.joined_at,
                   COALESCE(p.amount, 0) as donation_amount,
                   p.paid_at, p.merchant_ref
            FROM members m
            LEFT JOIN payment_orders p ON m.chat_id = p.chat_id AND p.status = 'paid'
            WHERE m.tier = 'donor' AND m.status = 'paid'
            ORDER BY p.paid_at DESC
        """)
        rows = c.fetchall()
        donors = []
        for r in rows:
            d = dict(r)
            # Use nama or chat_id as display name
            display_name = d.get("nama", "") or f"User-{d['chat_id'][:8]}"
            if d.get("username"):
                display_name = f"@{d['username']}"
            donors.append({
                "chat_id": d["chat_id"],
                "display_name": display_name,
                "amount": float(d.get("donation_amount", 0) or 0),
                "paid_at": str(d.get("paid_at", "") or ""),
                "joined_at": str(d.get("joined_at", "") or "")
            })
        return donors
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        if conn:
            conn.close()


def get_daily_analyze_stats():
    """Count analyze requests per day from quota_cache files."""
    quota_dir = DATA_DIR / "quota_cache"
    daily = {}
    try:
        for f in quota_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                date = data.get("date", "")
                used = data.get("used", 0)
                if date:
                    daily[date] = daily.get(date, 0) + used
            except: pass
    except: pass
    # Sort by date desc
    sorted_daily = sorted(daily.items(), reverse=True)
    total_all_time = sum(daily.values())
    return {
        "daily": [{"date": d, "requests": c} for d, c in sorted_daily],
        "total_all_time": total_all_time,
        "today": daily.get(datetime.now(WIB).strftime("%Y-%m-%d"), 0),
        "yesterday": daily.get((datetime.now(WIB) - timedelta(days=1)).strftime("%Y-%m-%d"), 0)
    }


def get_trade_stats():
    """Load stats from trade_history.json."""
    try:
        if TRADE_HISTORY.exists():
            data = json.loads(TRADE_HISTORY.read_text())
            return data.get("stats", {})
    except: pass
    return {"total": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "total_pips": 0.0, "win_rate": 0, "best_win_pips": 0, "worst_loss_pips": 0}


def get_daily_mapping():
    """Get latest daily mapping data."""
    mapping_file = DATA_DIR / ".last_mapping_date"
    try:
        if mapping_file.exists():
            date_str = mapping_file.read_text().strip()
            return {"date": date_str, "status": "posted"}
    except: pass
    return {"date": "N/A", "status": "not_posted"}


def get_today_trades():
    """Get today's trades for stats."""
    today = datetime.now(WIB).strftime("%Y%m%d")
    try:
        if TRADE_HISTORY.exists():
            all_data = json.loads(TRADE_HISTORY.read_text())
            trades = all_data.get("trades", [])
            return [t for t in trades 
                   if str(t.get("date","") or t.get("timestamp",""))[:8] == today]
    except: pass
    return []


def get_transparency_data():
    """Get transparency data for LP: members, revenue, costs."""
    conn = None
    try:
        import sqlite3
        db_path = DATA_DIR / "members.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Total members
        c.execute("SELECT COUNT(*) FROM members")
        total_members = c.fetchone()[0]
        
        # Breakdown by tier
        c.execute("SELECT tier, status, COUNT(*) as cnt FROM members GROUP BY tier, status")
        tier_breakdown = {}
        for r in c.fetchall():
            tier_breakdown[f"{r['tier']}_{r['status']}"] = r['cnt']
        
        # Donors
        c.execute("SELECT COUNT(*) FROM members WHERE tier='donor'")
        total_donors = c.fetchone()[0]
        
        # Pro
        c.execute("SELECT COUNT(*) FROM members WHERE tier='pro' AND status='paid'")
        total_pro = c.fetchone()[0]
        
        # Free/trial
        c.execute("SELECT COUNT(*) FROM members WHERE tier='starter' AND status='trial'")
        total_free = c.fetchone()[0]
        
        # Revenue from paid payments
        c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_orders WHERE status='paid'")
        row = c.fetchone()
        paid_count = row[0]
        total_revenue = float(row[1]) if row[1] else 0
        
        # Donation-specific revenue
        c.execute("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM payment_orders WHERE status='paid' AND product_key='donor'")
        drow = c.fetchone()
        donation_count = drow[0]
        donation_total = float(drow[1]) if drow[1] else 0
        
        # ── Also count manually activated donors (no payment record) ──
        c.execute("SELECT COUNT(*) FROM members WHERE tier='donor' AND status='paid'")
        manual_donors = c.fetchone()[0]
        # If there are donors but no payment records, estimate Rp50K per donor
        if manual_donors > donation_count:
            estimated_from_manual = (manual_donors - donation_count) * 50000
            if donation_total == 0:
                donation_total = float(estimated_from_manual)
            donation_count = manual_donors
        
        # Weekly revenue trend (last 7 days)
        week_ago = (datetime.now(WIB) - timedelta(days=7)).isoformat()
        c.execute("""
            SELECT DATE(paid_at) as day, COUNT(*), COALESCE(SUM(amount),0) 
            FROM payment_orders 
            WHERE status='paid' AND paid_at >= ? 
            GROUP BY DATE(paid_at)
            ORDER BY day DESC
        """, (week_ago,))
        revenue_trend = []
        for r in c.fetchall():
            revenue_trend.append({
                "date": r[0], 
                "count": r[1], 
                "amount": float(r[2]) if r[2] else 0
            })
    except:
        total_members = 0
        total_donors = 0
        total_pro = 0
        total_free = 0
        paid_count = 0
        total_revenue = 0
        donation_count = 0
        donation_total = 0
        tier_breakdown = {}
        revenue_trend = []
    finally:
        if conn:
            conn.close()
    
    # Feed stats
    try:
        feed = _load_feed()
        feed_stats = feed.get("stats", {})
        signals_total = feed_stats.get("total", 0)
    except:
        signals_total = 0
    
    # Trade stats
    try:
        if TRADE_HISTORY.exists():
            th = json.loads(TRADE_HISTORY.read_text())
            trade_stats = th.get("stats", {})
        else:
            trade_stats = {}
    except:
        trade_stats = {}
    
    # Today's active users (from quota cache - unique user files with today's date)
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
                except: pass
    except: pass
    
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
            "domain_hosting": 20
        }
    }


# ═══════════════════════════════════════════
#  FUEL STATS HELPER
# ═══════════════════════════════════════════

def get_fuel_stats() -> dict:
    """Get AI Fuel donation stats for dashboard display."""
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
            "percent": pct,
            "shortfall": shortfall,
            "status": "critical" if pct < 25 else ("low" if pct < 50 else ("medium" if pct < 75 else "healthy")),
        }
    except:
        return {"monthly_cost": 7357500, "collected": 0, "donors": 0, "percent": 0, "shortfall": 7357500, "status": "critical"}


# ═══════════════════════════════════════════
#  HTML DASHBOARD
# ═══════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Vilona TradeFX — AI Signal Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0e0;font-family:'Inter',-apple-system,sans-serif;min-height:100vh}
.header{background:linear-gradient(135deg,#1a1a2e,#16213e);padding:24px 32px;border-bottom:1px solid #2a2a4a}
.header h1{font-size:28px;font-weight:800;background:linear-gradient(90deg,#00d4aa,#00b4d8);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header .sub{color:#888;font-size:14px;margin-top:4px}
.grid{display:grid;grid-template-columns:1fr 300px;gap:24px;padding:24px 32px;max-width:1400px;margin:0 auto}
.main{min-width:0}
.sidebar{display:flex;flex-direction:column;gap:20px}
.card{background:#12121a;border:1px solid #1e1e30;border-radius:12px;padding:20px}
.card h3{font-size:16px;font-weight:700;margin-bottom:12px;color:#fff}
.signal-card{background:#12121a;border-left:3px solid #00d4aa;border-radius:8px;padding:16px;margin-bottom:12px}
.signal-card.sell{border-left-color:#ff4757}
.signal-card .sig-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.signal-card .sig-action{font-weight:800;font-size:18px}
.signal-card .sig-action.buy{color:#00d4aa}
.signal-card .sig-action.sell{color:#ff4757}
.signal-card .sig-symbol{color:#888;font-size:14px}
.signal-card .sig-prices{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:13px;margin:8px 0}
.signal-card .sig-meta{color:#666;font-size:12px;border-top:1px solid #1e1e30;padding-top:8px;margin-top:8px}
.signal-card .sig-engines{font-size:12px;color:#aaa;margin-top:6px}
.signal-card .sig-source{font-size:11px;color:#555;margin-top:4px;font-style:italic}
.stat-box{text-align:center;padding:12px}
.stat-box .value{font-size:28px;font-weight:800}
.stat-box .value.green{color:#00d4aa}
.stat-box .value.red{color:#ff4757}
.stat-box .label{color:#666;font-size:12px;margin-top:4px}
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stats-row-2{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}
.activity-item{padding:8px 0;border-bottom:1px solid #1a1a2a;font-size:13px}
.activity-item:last-child{border-bottom:none}
.activity-item .user{color:#00b4d8;font-weight:600}
.activity-item .action{font-weight:700}
.activity-item .time{color:#555;font-size:11px}
.empty-state{text-align:center;color:#555;padding:40px 20px;font-size:14px}
.refresh-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.refresh-bar .auto{color:#00d4aa;font-size:12px}
.badge{background:#1a1a2e;padding:2px 8px;border-radius:12px;font-size:11px}
.badge.buy{color:#00d4aa;border:1px solid #00d4aa33}
.badge.sell{color:#ff4757;border:1px solid #ff475733}
.engines-grid{font-size:11px;color:#777;display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}
.engines-grid span{background:#0a0a14;padding:2px 6px;border-radius:4px}
.footer{text-align:center;padding:32px;color:#444;font-size:12px}
@media(max-width:768px){.grid{grid-template-columns:1fr;padding:16px}}
</style>
</head>
<body>
<div class="header">
    <h1>⚡ Vilona TradeFX Dashboard</h1>
    <div class="sub">AI-Powered Signal Analytics — Transparent, Real-Time, Community-Driven</div>
</div>

<div class="grid">
    <div class="main">
        <!-- Stats Row -->
        <div class="card" style="margin-bottom:24px">
            <h3>📊 Performance Overview</h3>
            <div class="stats-row" id="stats-row">
                <div class="stat-box"><div class="value green" id="stat-winrate">--</div><div class="label">Win Rate</div></div>
                <div class="stat-box"><div class="value" id="stat-total">--</div><div class="label">Total Sinyal</div></div>
                <div class="stat-box"><div class="value green" id="stat-pips">--</div><div class="label">Total Pips</div></div>
                <div class="stat-box"><div class="value" id="stat-today">--</div><div class="label">Sinyal Hari Ini</div></div>
            </div>
        </div>

        <!-- Live Signal Feed -->
        <div class="card">
            <div class="refresh-bar">
                <h3>📡 Live Signal Feed</h3>
                <span class="auto">● Auto-refresh 30s</span>
            </div>
            <div id="signal-feed">
                <div class="empty-state">Loading signals...</div>
            </div>
        </div>
    </div>

    <div class="sidebar">
        <!-- User Activity Feed -->
        <div class="card">
            <h3>👥 User Activity</h3>
            <div id="user-activity">
                <div class="empty-state">Loading...</div>
            </div>
        </div>

        <!-- Daily Mapping Status -->
        <div class="card">
            <h3>📐 Daily Mapping</h3>
            <div id="mapping-status">
                <div class="empty-state">Loading...</div>
            </div>
        </div>

        <!-- Donation CTA -->
        <div class="card" style="background:linear-gradient(135deg,#0d2818,#0a1a1a);border-color:#00d4aa33">
            <h3 style="color:#00d4aa">💚 Isi Bensin AI</h3>
            <p style="font-size:13px;color:#aaa;margin-bottom:12px">Server ini berjalan 24/7 dengan biaya API & GPU yang besar. Dukung agar tetap hidup.</p>
            <a href="https://t.me/berkahkaryaforexbotbot" target="_blank" 
               style="display:block;text-align:center;background:#00d4aa;color:#000;padding:10px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px">
               ⚡ Isi Bahan Bakar AI
            </a>
        </div>
    </div>
</div>

<div class="footer">
    <div style="margin-bottom:16px;display:flex;flex-wrap:wrap;justify-content:center;gap:12px">
        <a href="https://t.me/berkahkaryaforexbotbot" target="_blank"
           style="display:inline-flex;align-items:center;gap:8px;padding:10px 24px;background:linear-gradient(135deg,#10B981,#06B6D4);color:#fff;border-radius:12px;text-decoration:none;font-weight:600;font-size:14px">
            ⚡ ISI BAHAN BAKAR AI
        </a>
        <a href="https://t.me/berkahkaryaforexbotbot" target="_blank"
           style="display:inline-flex;align-items:center;gap:8px;padding:10px 24px;background:#1F2937;color:#fff;border-radius:12px;text-decoration:none;font-weight:600;font-size:14px">
            📥 DOWNLOAD EA MT5
        </a>
    </div>
    Vilona TradeFX © 2026 · AI-Driven Trading Signals · DYOR · Not Financial Advice
</div>

<script>
function esc(s) { const d = document.createElement('div'); d.textContent = s||''; return d.innerHTML; }
const API = '/api';

async function loadFeed() {
    try {
        const [feedRes, userRes, statsRes, tradeRes] = await Promise.all([
            fetch(API + '/feed'),
            fetch(API + '/user_activity'),
            fetch(API + '/stats'),
            fetch(API + '/trade_stats'),
        ]);
        const feed = await feedRes.json();
        const users = await userRes.json();
        const stats = await statsRes.json();
        const tradeStats = await tradeRes.json();
        
        renderStats(tradeStats, feed);
        renderSignals(feed.signals || []);
        renderUserActivity(users.signals || []);
        renderMapping();
    } catch(e) {
        console.error('Feed load error:', e);
    }
}

function renderStats(ts, feed) {
    const total = ts.total || 0;
    const wins = ts.wins || 0;
    const losses = ts.losses || 0;
    const wr = total > 0 ? Math.round((wins / (wins + losses || 1)) * 100) : 0;
    const pips = ts.total_pips || 0;
    const todaySigs = (feed.signals || []).filter(s => s.timestamp && s.timestamp.startsWith(new Date().toISOString().slice(0,10))).length;
    
    document.getElementById('stat-winrate').textContent = wr + '%';
    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-pips').textContent = (pips > 0 ? '+' : '') + (pips||0).toFixed(1);
    document.getElementById('stat-today').textContent = todaySigs;
}

function renderSignals(signals) {
    const el = document.getElementById('signal-feed');
    if (!signals.length) {
        el.innerHTML = '<div class="empty-state">🔍 Belum ada sinyal. Auto-scanner aktif 07:00-23:00 WIB.</div>';
        return;
    }
    el.innerHTML = signals.slice().reverse().map(s => {
        const isBuy = s.direction === 'BUY';
        const cls = isBuy ? 'signal-card' : 'signal-card sell';
        const actionCls = isBuy ? 'sig-action buy' : 'sig-action sell';
        const emoji = isBuy ? '🟢' : '🔴';
        const ts = (s.timestamp || '').replace('T', ' ').slice(0, 16);
        const engines = s.engines || {};
        const engineKeys = Object.keys(engines);
        const source = s.source === 'user-generate' 
            ? `👤 Generated by @${esc(s.source_user || 'anon')}`
            : '🤖 Vilona AI Auto-Scanner';
        const outcome = s.status !== 'pending' 
            ? `<span style="color:${s.status==='tp'?'#00d4aa':'#ff4757'}">${esc(s.status).toUpperCase()}</span>`
            : '<span style="color:#888">PENDING</span>';
        
        return `<div class="${cls}">
            <div class="sig-header">
                <span class="${actionCls}">${emoji} ${esc(s.direction)} ${esc(s.symbol)}</span>
                <span class="sig-symbol">${outcome} · ${esc(ts)}</span>
            </div>
            <div class="sig-prices">
                <div>Entry: ${esc(s.entry)}</div>
                <div>SL: ${esc(s.sl)}</div>
                <div>TP: ${esc(s.tp)}</div>
            </div>
            <div class="sig-engines">
                ${engineKeys.length ? '<div class="engines-grid">' + engineKeys.map(k => 
                    `<span>${esc(k)}: ${typeof engines[k]==='object' ? (engines[k].confidence*100||0).toFixed(0)+'%' : esc(engines[k])}</span>`
                ).join('') + '</div>' : ''}
            </div>
            <div class="sig-meta">
                RR 1:${esc(s.rr_ratio || '?')} · Conf: ${((s.confidence||0)*100).toFixed(0)}% · Grade: ${esc(s.grade || 'N/A')}
            </div>
            <div class="sig-source">${source}</div>
        </div>`;
    }).join('');
}

function renderUserActivity(signals) {
    const el = document.getElementById('user-activity');
    if (!signals.length) {
        el.innerHTML = '<div class="empty-state" style="font-size:12px">Belum ada user generate sinyal hari ini.</div>';
        return;
    }
    el.innerHTML = signals.slice().reverse().slice(0, 10).map(s => {
        const isBuy = s.direction === 'BUY';
        const emoji = isBuy ? '🟢' : '🔴';
        return `<div class="activity-item">
            <span class="user">@${esc(s.source_user || 'anon')}</span>
            <span class="action" style="color:${isBuy?'#00d4aa':'#ff4757'}">${emoji} ${esc(s.direction)}</span>
            <span style="color:#888">${esc(s.symbol)}</span>
            <span class="time">${esc((s.timestamp||'').slice(11,16))}</span>
        </div>`;
    }).join('');
}

function renderMapping() {
    const el = document.getElementById('mapping-status');
    const today = new Date().toISOString().slice(0,10);
    el.innerHTML = `
        <div style="font-size:13px;color:#aaa;margin-bottom:8px">📅 ${today}</div>
        <div style="font-size:13px">
            🏷 <b>XAUUSD</b> — S/R levels updated daily 10:00 WIB<br>
            🏷 <b>BTCUSD</b> — Key swing zones<br>
            🏷 <b>USOIL</b> — Support/Resistance
        </div>
        <div style="font-size:11px;color:#555;margin-top:8px">📐 Posted to channel @vilonaaichanel</div>
    `;
}

// Initial load
loadFeed();
// Auto-refresh every 30s
setInterval(loadFeed, 30000);
</script>
</body>
</html>"""


# HTML template dir
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
_dash_html_cache = None

def _get_dashboard_html() -> str:
    global _dash_html_cache
    html_path = TEMPLATE_DIR / "dashboard.html"
    if _dash_html_cache and html_path.exists():
        return _dash_html_cache
    try:
        if html_path.exists():
            _dash_html_cache = html_path.read_text(encoding='utf-8')
            return _dash_html_cache
    except:
        pass
    return "<!-- Loading dashboard... -->"


# ═══════════════════════════════════════════
#  API HANDLER
# ═══════════════════════════════════════════

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        
        if path == '/' or path == '/index.html':
            # Auto-redirect based on Accept-Language header
            accept_lang = self.headers.get('Accept-Language', '')
            if 'id' in accept_lang.lower():
                self._redirect('/id')
            else:
                self._redirect('/en')
        elif path == '/id' or path == '/en':
            # Server-side translated Tailwind dashboard
            template = 'dashboard_id.html' if path == '/id' else 'dashboard_en.html'
            html_path = Path(__file__).resolve().parent / 'templates' / template
            try:
                page = html_path.read_text(encoding='utf-8')
                self._serve_html(page)
            except FileNotFoundError:
                self._serve_json({"error": "page not found"}, 404)
        elif path == '/api/feed':
            self._serve_json({"signals": get_recent_signals(50)})
        elif path == '/api/user_activity':
            self._serve_json({"signals": get_user_signals(50)})
        elif path == '/api/stats':
            feed_data = _load_feed()
            self._serve_json(feed_data.get("stats", {}))
        elif path == '/api/trade_stats':
            self._serve_json(get_trade_stats())
        elif path == '/api/mapping':
            self._serve_json(get_daily_mapping())
        elif path == '/api/today_trades':
            self._serve_json({"trades": get_today_trades()})
        elif path == '/api/transparency':
            self._serve_json(get_transparency_data())
        elif path == '/api/backtest':
            self._serve_json(get_backtest_data())
        elif path == '/api/donors':
            self._serve_json({"donors": get_donor_list()})
        elif path == '/api/daily_analyze':
            self._serve_json(get_daily_analyze_stats())
        elif path == '/api/daily_recap':
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from trade_tracker import get_daily_trades, format_daily_recap
                recap = get_daily_trades()
                self._serve_json(recap)
            except Exception as e:
                self._serve_json({"error": str(e), "trades": [], "total_signals": 0})
        elif path == '/api/fuel/create':
            self._handle_fuel_create()
        elif path == '/api/fuel/stats':
            self._serve_json(get_fuel_stats())
        elif path == '/api/fuel/report':
            self._handle_fuel_report()
        elif path == '/health':
            self._serve_json({"status": "ok", "timestamp": datetime.now(WIB).isoformat()})
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
    
    def _serve_html(self, html: str):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        self.send_header('X-Accel-Expires', '0')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
    
    def _serve_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        # JSON-safe: convert Infinity/NaN to null for JavaScript compatibility
        raw = json.dumps(data, default=str)
        raw = raw.replace(': Infinity', ': null').replace(': -Infinity', ': null').replace(': NaN', ': null')
        self.wfile.write(raw.encode('utf-8'))
    
    def _handle_fuel_create(self):
        """Create Tripay donation payment for AI Fuel."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        amount_str = (params.get('amount', [None]) or [None])[0]
        chat_id = (params.get('chat_id', ['web'] or ['web']))[0]
        username = (params.get('username', ['Guest'] or ['Guest']))[0]

        if not amount_str:
            self._serve_json({"success": False, "error": "Parameter 'amount' diperlukan"}, 400)
            return

        try:
            amount = int(amount_str)
        except ValueError:
            self._serve_json({"success": False, "error": "Amount harus angka"}, 400)
            return

        if amount < 10000:
            self._serve_json({"success": False, "error": "Minimum donasi Rp10.000"}, 400)
            return

        try:
            # Import Tripay module from project root
            sys.path.insert(0, str(PROJECT_DIR))
            from members.payment import create_tripay_payment
            result = create_tripay_payment(
                chat_id=chat_id,
                username=username,
                tier="donor",
                amount=amount,
            )
            if result.get("success"):
                self._serve_json(result)
            else:
                self._serve_json(result, 500)
        except Exception as e:
            self._serve_json({"success": False, "error": f"Tripay error: {str(e)[:200]}"}, 500)

    def _handle_fuel_report(self):
        """Receive manual transfer confirmation from website."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        chat_id = (params.get('chat_id', [None]) or [None])[0]

        if not chat_id:
            self._serve_json({"success": False, "error": "Parameter 'chat_id' diperlukan"}, 400)
            return

        # Save report to file for bot to pick up
        reports = []
        report_path = DATA_DIR / ".fuel_reports.json"
        if report_path.exists():
            try:
                reports = json.loads(report_path.read_text())
            except:
                reports = []

        # Check for duplicate
        existing = [r for r in reports if r.get("chat_id") == chat_id]
        if existing:
            self._serve_json({"success": True, "message": "Laporan sudah diterima sebelumnya."})
            return

        report = {
            "chat_id": chat_id,
            "timestamp": datetime.now(WIB).isoformat(),
            "status": "pending",
        }
        reports.append(report)
        # Atomic write: write to tmp then rename
        import tempfile
        tmp = report_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(reports, indent=2, default=str))
        tmp.rename(report_path)
        self._serve_json({"success": True, "message": "Laporan diterima. Admin akan aktivasi dalam 1x24 jam."})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Silent logging


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Vilona Dashboard API Server')
    parser.add_argument('--port', type=int, default=8766, help='Port to listen on')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    args = parser.parse_args()
    
    server = HTTPServer((args.host, args.port), DashboardHandler)
    print(f"📊 Vilona Dashboard running on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
