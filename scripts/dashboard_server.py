"""
Vilona Dashboard Server — Cornix-style realtime trading command center.
Serves public_dashboard_id.html + API from local data files.
Pure Python stdlib. No circular proxy deadlocks.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timezone, timedelta
import json
import os
import sqlite3
import time

PORT = int(os.environ.get("PORT", 8768))
HOST = os.environ.get("HOST", "0.0.0.0")
PROJECT_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = PROJECT_DIR / "tradebot" / "web" / "templates"
DATA_DIR = PROJECT_DIR / "data"
VILONA_DIR = DATA_DIR / "vilona_tradefx"

WIB = timezone(timedelta(hours=7))

# ═══ LIVE SNAPSHOT CACHE — populated by worker webhook push ═══
_live_snapshot: dict = {}
SNAPSHOT_FALLBACK = {
    "type": "dashboard_snapshot",
    "status": {"state": "connecting", "pair": "XAUUSD", "detail": "Waiting for worker..."},
    "performance": {"win_rate": 0.0, "total_pnl": 0.0},
    "users": {"active": 0, "bot_users": 0},
    "prices": {"XAUUSD": None},
    "uptime_seconds": 0,
    "total_cycles": 0,
}

TRADE_LOG_PATH = DATA_DIR / "trade_log.json"
TRADE_HISTORY_PATH = DATA_DIR / "trade_history.json"
FUEL_REPORTS_PATH = VILONA_DIR / ".fuel_reports.json"
MEMBERS_DB_PATH = VILONA_DIR / "members.db"  # Real members DB with 31 members + 17 payment orders
SIGNAL_FEED_PATH = VILONA_DIR / "signal_feed.json"  # Unified feed: channel-auto + user-generate
ENGINE_STATUS_PATH = PROJECT_DIR / "bridges" / "signal_bridge" / "engine_status.json"
BACKTEST_XAU_PATH = VILONA_DIR / "backtest_xauusd_3m.json"
BACKTEST_GRID_PATH = VILONA_DIR / "backtest_grid2.json"
BACKTEST_PER_ENGINE_PATH = VILONA_DIR / "backtest_per_engine.json"

# Load templates once at startup
DASHBOARD_HTML = (TEMPLATE_DIR / "public_dashboard_id.html").read_text(encoding="utf-8")
try:
    DASHBOARD_EN = (TEMPLATE_DIR / "public_dashboard_en.html").read_text(encoding="utf-8")
except FileNotFoundError:
    DASHBOARD_EN = DASHBOARD_HTML


_start_time = time.time()


def _get_trade_stats():
    """Read trade history and return {total, wins, losses, total_pips}."""
    th = _read_json(TRADE_HISTORY_PATH, {"trades": [], "stats": {}})
    trades = th.get("trades", [])
    if not trades and isinstance(th, list):
        trades = th
    wins = sum(1 for t in trades if str(t.get("result", t.get("outcome", ""))).upper() in ("TP", "TP_HIT", "WIN"))
    losses = sum(1 for t in trades if str(t.get("result", t.get("outcome", ""))).upper() in ("SL", "SL_HIT", "LOSS"))
    total_pips = sum(
        float(t.get("pips", 0) or 0)
        for t in trades
        if str(t.get("result", t.get("outcome", ""))).upper() in ("TP", "TP_HIT", "SL", "SL_HIT", "WIN", "LOSS")
    )
    return {"total": len(trades), "wins": wins, "losses": losses, "total_pips": round(total_pips, 1)}


def _read_json(path, default=None):
    """Read JSON file, return default on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _read_sqlite(db_path, query, params=()):
    """Safe SQLite read."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def _get_all_signals():
    """Get combined signals from trade_log (AI engines) + signal_feed (user-generate)."""
    # AI engine signals from trade_log.json
    raw = _read_json(TRADE_LOG_PATH, [])
    engine_signals = raw if isinstance(raw, list) else raw.get("signals", [])
    # Unified feed signals from signal_feed.json (already has direction, source, source_user)
    feed_data = _read_json(SIGNAL_FEED_PATH, {"signals": []})
    feed_signals = feed_data.get("signals", [])
    # Merge: deduplicate by entry+timestamp
    seen = set()
    merged = []
    for s in engine_signals:
        key = f"{s.get('action','')}-{s.get('entry',0)}-{s.get('timestamp','')}"
        if key not in seen:
            seen.add(key)
            merged.append(s)
    for s in feed_signals:
        key = f"{s.get('direction','')}-{s.get('entry',0)}-{s.get('timestamp','')}"
        if key not in seen:
            seen.add(key)
            merged.append(s)
    return merged


def _transform_signal(s):
    """Transform ANY signal format → dashboard JS format."""
    # signal_feed.json format: { direction, symbol, status, timestamp, entry, sl, tp, confidence, rr_ratio, grade, source, source_user }
    # trade_log.json format: { action, symbol, entry, sl, tp1, rr, grade, confidence, timestamp }
    direction = s.get("direction") or s.get("action", "HOLD")
    if isinstance(direction, str):
        direction = direction.upper()
    return {
        "direction": direction,
        "symbol": s.get("symbol", "XAUUSD"),
        "status": s.get("status", "pending"),
        "timestamp": s.get("timestamp", s.get("created_at", "")),
        "entry": s.get("entry", 0),
        "sl": s.get("sl", 0),
        "tp": s.get("tp", s.get("tp1", 0)),
        "rr_ratio": s.get("rr_ratio", s.get("rr", 1.5)),
        "confidence": s.get("confidence", 0.5),
        "source": s.get("source", "ai-scanner"),
        "source_user": s.get("source_user", s.get("username", "")),
        "grade": s.get("grade", "B"),
    }


# ═══ HANDLER ═══

class Handler(BaseHTTPRequestHandler):
    def _html(self, content, code=200):
        data = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, data, code=200):
        raw = json.dumps(data, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    # ═══ API: SIGNAL FEED ═══
    def api_feed(self):
        """Returns transformed signals from ALL sources (engines + user-generate)."""
        all_signals = _get_all_signals()
        transformed = [_transform_signal(s) for s in all_signals[-50:]]
        self._json({"signals": transformed})

    # ═══ API: TRADE STATS ═══
    def api_trade_stats(self):
        """Returns trade execution summary for overview KPI cards."""
        th = _get_trade_history()
        trades = th.get("trades", [])
        if not trades and isinstance(th, list):
            trades = th
        wins = sum(1 for t in trades if str(t.get("result", t.get("outcome", ""))).upper() in ("TP", "TP_HIT", "WIN"))
        losses = sum(1 for t in trades if str(t.get("result", t.get("outcome", ""))).upper() in ("SL", "SL_HIT", "LOSS"))
        total_pips = sum(
            float(t.get("pips", 0) or 0)
            for t in trades
            if str(t.get("result", t.get("outcome", ""))).upper() in ("TP", "TP_HIT", "SL", "SL_HIT", "WIN", "LOSS")
        )
        self._json({
            "total": len(trades),
            "wins": wins,
            "losses": losses,
            "total_pips": round(total_pips, 1),
        })

    # ═══ API: USER ACTIVITY ═══
    def api_user_activity(self):
        """Returns recent signals from ALL sources for the activity stream."""
        all_signals = _get_all_signals()
        transformed = [_transform_signal(s) for s in all_signals[-20:]]
        self._json({"signals": transformed})

    # ═══ API: TRANSPARENCY ═══
    def api_transparency(self):
        """Platform stats for transparency page + community section."""
        all_signals = _get_all_signals()
        signals_count = len(all_signals)
        # Count members from real members.db
        total_members = _read_sqlite(MEMBERS_DB_PATH, "SELECT COUNT(*) as cnt FROM members")
        total_members = total_members[0]["cnt"] if total_members else 0
        # Count donors from payment_orders that are PAID
        donors = _read_sqlite(MEMBERS_DB_PATH,
            "SELECT COUNT(DISTINCT chat_id) as cnt FROM payment_orders WHERE LOWER(status)='paid'")
        total_donors = donors[0]["cnt"] if donors else 0
        # Sum donation from PAID payment_orders
        paid_total = _read_sqlite(MEMBERS_DB_PATH,
            "SELECT COALESCE(SUM(amount), 0) as total FROM payment_orders WHERE LOWER(status)='paid'")
        donation_total = paid_total[0]["total"] if paid_total else 0
        # Count pro/free members
        pro = _read_sqlite(MEMBERS_DB_PATH,
            "SELECT COUNT(*) as cnt FROM members WHERE tier != 'free' AND status='active'")
        total_pro = pro[0]["cnt"] if pro else 0
        total_free = total_members - total_pro
        # Active users today (members with quota_used today)
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        active = _read_sqlite(MEMBERS_DB_PATH,
            "SELECT COUNT(*) as cnt FROM members WHERE quota_date = ?", (today,))
        active_today = active[0]["cnt"] if active else 0

        self._json({
            "signals_total": signals_count,
            "total_members": total_members,
            "total_donors": total_donors,
            "total_free": total_free,
            "total_pro": total_pro,
            "active_users_today": active_today,
            "server_cost_idr": 2500000,
            "donation_total": donation_total,
            "donation_transactions": total_donors,
            "api_breakdown": {
                "deepseek_api": 150, "openai_api": 120,
                "claude_api": 80, "gpu_server": 80, "domain_hosting": 20,
            },
        })

    # ═══ API: BACKTEST ═══
    def api_backtest(self):
        """Real backtest data from local JSON files."""
        xau = _read_json(BACKTEST_XAU_PATH, {})
        grid = _read_json(BACKTEST_GRID_PATH, [])
        pe = _read_json(BACKTEST_PER_ENGINE_PATH, {})
        # Use results object for summary stats
        results = xau.get("results", {}) if isinstance(xau, dict) else {}
        trades_list = xau.get("trades", []) if isinstance(xau, dict) else (xau if isinstance(xau, list) else [])

        xauusd = {
            "period": "Mar-Jun 2026",
            "winrate": results.get("winrate", 0),
            "trades": results.get("trades", len(trades_list)),
            "wins": results.get("wins", 0),
            "losses": results.get("losses", 0),
            "total_pips": results.get("total_pips", 0),
            "avg_win": results.get("avg_win", 0),
            "avg_loss": results.get("avg_loss", 0),
        }

        # Build grid configs
        grid_data = []
        if isinstance(grid, list):
            for g in grid[-8:]:
                grid_data.append({
                    "label": g.get("label", g.get("config", g.get("name", "?"))),
                    "winrate": g.get("winrate", g.get("win_rate", g.get("wr", 0))),
                    "total_pips": g.get("total_pips", g.get("pips", 0)),
                    "wins": g.get("wins", 0),
                    "losses": g.get("losses", 0),
                    "signals": g.get("signals", g.get("trades", g.get("total", 0))),
                })

        self._json({
            "xauusd": xauusd,
            "per_engine": {
                "engines": pe if isinstance(pe, dict) else pe.get("engines", {}),
            },
            "grid": grid_data,
        })

    # ═══ API: DONORS ═══
    def api_donors(self):
        """Subscriber list from real payment_orders."""
        donors_sql = _read_sqlite(MEMBERS_DB_PATH,
            "SELECT m.nama, m.username, po.amount, po.paid_at, m.tier "
            "FROM payment_orders po LEFT JOIN members m ON po.chat_id = m.chat_id "
            "WHERE LOWER(po.status)='paid' ORDER BY po.amount DESC LIMIT 30")
        donors = []
        for r in donors_sql:
            donors.append({
                "display_name": r.get("nama") or r.get("username") or "Anonymous",
                "amount": r.get("amount", 0),
                "paid_at": r.get("paid_at", ""),
                "level": r.get("tier", "supporter"),
            })
        self._json({"donors": donors})

    # ═══ API: DAILY ANALYZE ═══
    def api_daily_analyze(self):
        """Daily analyze request counts from member quota usage."""
        rows = _read_sqlite(
            MEMBERS_DB_PATH,
            "SELECT quota_date as date, COUNT(*) as requests FROM members "
            "WHERE quota_date IS NOT NULL GROUP BY quota_date ORDER BY quota_date DESC LIMIT 30"
        )
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        yesterday = (datetime.now(WIB) - timedelta(days=1)).strftime("%Y-%m-%d")
        daily_rows = [{"date": r["date"], "requests": r["requests"]} for r in rows]
        total_all_time = sum(r["requests"] for r in daily_rows)
        today_count = next((r["requests"] for r in daily_rows if r["date"] == today), 0)
        yesterday_count = next((r["requests"] for r in daily_rows if r["date"] == yesterday), 0)

        self._json({
            "daily": daily_rows,
            "total_all_time": total_all_time,
            "today": today_count,
            "yesterday": yesterday_count,
        })

    # ═══ API: DAILY RECAP ═══
    def api_daily_recap(self):
        """Today's trading recap with trades list."""
        th = _get_trade_history()
        trades = th.get("trades", [])
        if not trades and isinstance(th, list):
            trades = th
        today_str = datetime.now(WIB).strftime("%Y-%m-%d")
        today_trades = [t for t in trades if str(t.get("open_time", t.get("close_time", "")))[:10] == today_str]

        wins = sum(1 for t in today_trades if str(t.get("result", t.get("outcome", ""))).upper() in ("TP", "TP_HIT", "WIN"))
        losses = sum(1 for t in today_trades if str(t.get("result", t.get("outcome", ""))).upper() in ("SL", "SL_HIT", "LOSS"))
        total_pips = sum(float(t.get("pips", 0) or 0) for t in today_trades)
        wr = round(wins / max(wins + losses, 1) * 100, 1)

        formatted_trades = [{
            "outcome": t.get("result", t.get("outcome", "PENDING")),
            "pips": float(t.get("pips", 0) or 0),
            "open_time": t.get("open_time", t.get("timestamp", t.get("close_time", ""))),
            "action": t.get("action", t.get("direction", "?")),
            "symbol": t.get("symbol", "XAUUSD"),
        } for t in today_trades]

        self._json({
            "trades": formatted_trades,
            "total_signals": len(today_trades),
            "wins": wins,
            "losses": losses,
            "win_rate": wr,
            "total_pips": round(total_pips, 1),
            "micro_profit_idr": 0,
        })

    # ═══ API: FUEL STATS ═══
    def api_fuel_stats(self):
        """AI Fuel / donation progress from real payment_orders."""
        paid = _read_sqlite(MEMBERS_DB_PATH,
            "SELECT COALESCE(SUM(amount), 0) as total, COUNT(DISTINCT chat_id) as donors "
            "FROM payment_orders WHERE LOWER(status)='paid'")
        total_raised = paid[0]["total"] if paid else 0
        total_donors = paid[0]["donors"] if paid else 0
        monthly = 7357500
        pct = round(min(100, total_raised / max(monthly, 1) * 100), 1)
        shortfall = max(0, monthly - total_raised)

        self._json({
            "percent": pct,
            "collected": total_raised,
            "shortfall": shortfall,
            "donors": total_donors,
            "monthly_cost": monthly,
        })

    # ═══ API: ENGINE READINGS ═══
    def api_engine_readings(self):
        """Live AI engine readings from bridge status cache. Shows 11 engines across 5 TFs."""
        data = _read_json(ENGINE_STATUS_PATH, {})
        if data and isinstance(data, dict) and data.get("timeframes"):
            self._json(data)
        else:
            # Return empty structure so the JS can show "No engine data"
            self._json({
                "symbol": "XAUUSD",
                "price": 0,
                "timestamp": datetime.now(WIB).isoformat(),
                "timeframes": {},
                "hierarchical": {"verdict": "HOLD", "consensus_score": 0},
                "mtf_alignment": "UNKNOWN",
                "macro_trend": "NEUTRAL",
            })

    # ═══ API: LIVE SNAPSHOT (merged from worker live_status.json + DB trade stats) ═══
    def api_live_snapshot(self):
        """Live dashboard snapshot — webhook priority, then file + DB merge fallback."""
        # Priority: use webhook-pushed _live_snapshot if available
        global _live_snapshot
        if _live_snapshot:
            snap = dict(_live_snapshot)
            if snap.get("uptime_seconds") or snap.get("status"):
                self._json(snap)
                return

        LIVE_STATUS_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'vilona_tradefx', 'live_status.json')
        worker = {}
        try:
            p = os.path.abspath(LIVE_STATUS_PATH)
            if os.path.exists(p):
                with open(p) as f:
                    worker = json.load(f) or {}
        except Exception:
            pass

        ts = _get_trade_stats()
        feeds = _get_all_signals()
        all_signals = feeds if isinstance(feeds, list) else []
        wins = ts.get("wins", 0)
        losses = ts.get("losses", 0)
        total = wins + losses
        wr = round(wins / max(total, 1), 2)

        # Merge: worker uptime/state/price + DB performance/users
        worker_uptime = worker.get("uptime_seconds", 0)
        worker_cycles = worker.get("total_cycles", 0)
        worker_state = worker.get("status", {}).get("state", "analyzing" if total > 0 else "idle")
        worker_detail = worker.get("status", {}).get("detail", "") or f"AI aktif — {len(all_signals)} sinyal (WR {int(wr*100)}%)"
        worker_pair = worker.get("status", {}).get("pair", "XAUUSD")
        worker_price = worker.get("prices", {}).get("XAUUSD", None)
        worker_bot_users = worker.get("users", {}).get("bot_users", 3)

        snapshot = {
            "type": "dashboard_snapshot",
            "status": {
                "state": worker_state,
                "pair": worker_pair,
                "detail": worker_detail,
            },
            "performance": {
                "win_rate": wr,
                "total_pnl": round(ts.get("total_pips", 0) * 10, 2),
            },
            "users": {
                "active": len(all_signals),
                "bot_users": worker_bot_users,
            },
            "prices": {"XAUUSD": worker_price},
            "uptime_seconds": int(worker_uptime if worker_uptime else (time.time() - _start_time)),
            "total_cycles": total + worker_cycles,
        }
        self._json(snapshot)

    # ═══ ROUTING ═══

    def do_GET(self):
        path = self.path.split("?")[0]

        # API routes
        routes = {
            "/api/feed": self.api_feed,
            "/api/trade_stats": self.api_trade_stats,
            "/api/user_activity": self.api_user_activity,
            "/api/live-snapshot": self.api_live_snapshot,
            "/api/transparency": self.api_transparency,
            "/api/backtest": self.api_backtest,
            "/api/donors": self.api_donors,
            "/api/daily_analyze": self.api_daily_analyze,
            "/api/daily_recap": self.api_daily_recap,
            "/api/fuel/stats": self.api_fuel_stats,
            "/api/engine-readings": self.api_engine_readings,
        }
        if path in routes:
            return routes[path]()

        # Page routes
        if path in ("", "/", "/id", "/landing"):
            self._html(DASHBOARD_HTML)
        elif path == "/en":
            self._html(DASHBOARD_EN)
        elif path == "/dashboard":
            lang = self.headers.get("Accept-Language", "")
            target = "/dashboard/id" if "id" in lang.lower() else "/dashboard/en"
            self._redirect(target)
        elif path in ("/dashboard/id", "/signals"):
            self._html(DASHBOARD_HTML)
        elif path == "/dashboard/en":
            self._html(DASHBOARD_EN)
        else:
            self._html(DASHBOARD_HTML)

    def do_POST(self):
        path = self.path.split("?")[0]

        # 🌐 Webhook: receive dashboard_snapshot from autonomous worker
        if path == "/api/webhook/snapshot":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b"{}"
                payload = json.loads(body)
                global _live_snapshot
                _live_snapshot = payload
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)[:200]}, 400)
            return

        if path == "/api/fuel/create":
            # Proxy to bridge which handles real Tripay payment creation
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length) if length else b""
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                url = f"http://127.0.0.1:8765/api/create-payment"
                if qs:
                    url += "?" + qs
                import urllib.request as ur
                req = ur.Request(url, data=body or None, method="POST")
                for k, v in self.headers.items():
                    if k.lower() not in ("host", "content-length", "transfer-encoding"):
                        req.add_header(k, v)
                with ur.urlopen(req, timeout=15) as r:
                    result = json.loads(r.read())
                self._json(result)
            except Exception as e:
                self._json({"success": False, "error": f"Payment gateway offline. Gunakan bot Telegram: @berkahkaryaforexbotbot"})


        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, *args, **kwargs):
        pass


def _get_trade_history():
    return _read_json(TRADE_HISTORY_PATH, {"trades": [], "stats": {}})


if __name__ == "__main__":
    print(f"📊 Vilona Dashboard → http://{HOST}:{PORT}")
    HTTPServer((HOST, PORT), Handler).serve_forever()
