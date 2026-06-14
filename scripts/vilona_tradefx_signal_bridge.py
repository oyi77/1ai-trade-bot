#!/usr/bin/env python3
"""Vilona Trade FX Signal Bridge V2 — Multi-User Commercial Edition.
|- API key authentication + tier-based rate limiting
|- Signal queue per user / per instance (account_id-based)
|- Instance Identity: tracks per {api_key}:{account_id}
|- Broadcast mode: duplicates signals to all instances of a key
|- MT5 Daemon protocol: POST /daemon/register + /trade-status callback
|- Trailing engine: broker-aware, daemon-routed SL modifications
|- Telegram alerts: rejection/error/drift notifications (@vilonatradebot)
|- Reconciliation: 5-min cycle cross-check against broker reality
|- Compatible with VilonaTradeFX_EA.mq5 (Commercial)

Usage: python3 vilona_tradefx_signal_bridge.py --port 8765 --host 0.0.0.0
  EA poll:     GET  /signal?api_key=VT-xxx&account_id=MT5-12345
  Daemon poll: GET  /signal?mode=trailing&daemon_id=xxx
  Bot signal:  POST /signal?api_key=VT-xxx
  Trade status:POST /trade-status  (daemon callback)
  Daemon reg:  POST /daemon/register
  Admin keys:  GET  /admin/keys (localhost only)
  Gen key:     POST /admin/generate-key (localhost only)
  EA download: GET  /download/ea or /ea/download"""
import hashlib, json, queue, sqlite3, time, threading, argparse, logging, os, sys, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from collections import deque, defaultdict
from urllib.parse import urlparse, parse_qs


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Thread-per-request HTTP server — no head-of-line blocking."""
    daemon_threads = True

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bridge")

# ── Config paths ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
TIERS_FILE = os.path.join(PROJECT_DIR, "config", "vilona_tiers.json")
DB_PATH = os.path.join(PROJECT_DIR, "data", "vilona_licenses.db")
KEYS_FILE = os.path.join(PROJECT_DIR, "api_keys.json")  # legacy — for migration only

# ── License DB (SQLite Phase 2) ──
_LICENSES_DB = None          # persistent WAL connection — opened once at startup
_DB_LOCK = threading.Lock()  # serializes all SQLite access
LICENSE_CACHE = {}           # api_key → {"active": bool, "tier_info": dict}
LICENSE_CACHE_TIME = 0
LICENSE_CACHE_TTL = 60       # seconds
ADMIN_SECRET = os.environ.get("VILONA_ADMIN_SECRET", "")
DASHBOARD_DIR = "/var/www/phantomfx-dashboard/dist"

# ── Global state ──
HISTORY = deque(maxlen=500)
PENDING = deque(maxlen=100)        # global pending queue (all users)
PENDING_BY_KEY = defaultdict(lambda: deque(maxlen=50))  # per-user pending
PENDING_BY_INSTANCE = defaultdict(lambda: deque(maxlen=50))  # per-instance queue
LOCK = threading.Lock()
ID_COUNTER = 0
ACKED = set()
ACKED_BY_KEY = defaultdict(set)  # per-account ACK tracking
START_TIME = time.time()
SIGNAL_DEDUP_TTL = 60  # seconds
_signal_dedup_cache = {}  # hash → timestamp

# ── Instance Identity (per account_id per key) ──
INSTANCES = {}  # "{api_key}:{account_id}" → {last_seen, ip, signals_polled, first_seen, label}
MASTER_INSTANCES = defaultdict(dict)  # api_key → {account_id: instance_id}

# ── Rate limiting ──
RATE_COUNTERS = defaultdict(list)  # api_key → [timestamps]

# ── Connected accounts tracker (multi-MT5 support) ──
CONNECTED_ACCOUNTS = {}  # api_key → {last_seen, ip, signals_polled, first_seen, label}

# ── Smart Trailing State ──
TRAIL_CONFIG = defaultdict(lambda: {  # instance_id → trailing config
    "enabled": False,
    "mode": "basic",         # "basic" | "smc-swing" | "off"
    "trail_pips": 15,        # distance behind price
    "breakeven_pips": 10,    # trigger to move SL to entry
    "step_pips": 5,          # min improvement before updating SL
    # Broker-specific (auto-detected or user-configured)
    "account_type": "standard",  # "standard" | "cent" | "pro" | "zero"
    "pip_value": 0.10,       # $ per pip for 0.01 lot (standard=0.10, cent=0.01, pro=0.10)
    "digits": 2,             # decimal places (XAUUSD=2, some brokers=3)
    "spread_buffer": 0.0,    # extra pips to add to breakeven for spread compensation
})
TRAILED_POSITIONS = {}  # instance_id → {signal_id, entry, current_sl, direction, tp, timestamp}
TRAIL_CONFIG_FILE = os.path.join(PROJECT_DIR, "data", "vilona_tradefx", "trailing_config.json")

# Pre-computed broker profiles for auto-detection
BROKER_PROFILES = {
    # account_type:  {pip_value, digits, spread_buffer, tick_size}
    "standard":     {"pip_value": 0.10, "digits": 2, "spread_buffer": 0.0, "tick_size": 0.01},
    "cent":         {"pip_value": 0.01, "digits": 2, "spread_buffer": 0.0, "tick_size": 0.01},
    "pro":          {"pip_value": 0.10, "digits": 2, "spread_buffer": 0.0, "tick_size": 0.01},
    "zero":         {"pip_value": 0.10, "digits": 2, "spread_buffer": 3.0, "tick_size": 0.01},
    "raw":          {"pip_value": 0.10, "digits": 3, "spread_buffer": 0.0, "tick_size": 0.001},
}

_keys_cache = None
_keys_cache_time = 0

# ── MT5 Daemon Registry ──
DAEMONS = {}  # daemon_id → {account_id, api_key, last_seen, hostname, mt5_version, active_ticket}
DAEMON_SL_QUEUE = defaultdict(lambda: deque(maxlen=10))  # daemon_id → pending SL modifications
TRADE_REPORTS = deque(maxlen=200)  # history of /trade-status reports
RECONCILE_LOG = deque(maxlen=100)  # reconciliation reports

# ── Telegram Alert Worker ──
TELEGRAM_BOT_TOKEN = os.environ.get("VILONA_TELEGRAM_BOT_TOKEN", "8809864647:AAHU713FWspyWskwdLjGMhsEo7GqJnS-440")
TELEGRAM_ALERT_CHAT_ID = os.environ.get("VILONA_ALERT_CHAT_ID", "5220170786")
TELEGRAM_QUEUE = queue.Queue()  # thread-safe message queue

# ── License Database (SQLite Phase 2) ──

def init_licenses_db():
    """Called ONCE at bridge startup. Opens persistent WAL connection, creates
    table, migrates legacy api_keys.json if DB is empty."""
    global _LICENSES_DB
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA cache_size=-8000")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            api_key TEXT PRIMARY KEY,
            tier TEXT NOT NULL DEFAULT 'starter',
            label TEXT DEFAULT '',
            rate_limit INTEGER DEFAULT 3,
            rate_window_seconds INTEGER DEFAULT 86400,
            expires TEXT DEFAULT '2026-12-31',
            active INTEGER DEFAULT 1,
            features TEXT DEFAULT '[]'
        )
    """)
    conn.commit()

    # ── Migration: import from api_keys.json if DB is empty ──
    count = conn.execute("SELECT COUNT(*) as n FROM licenses").fetchone()["n"]
    if count == 0 and os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE) as f:
                legacy = json.load(f)
            imported = 0
            for k, v in legacy.get("keys", {}).items():
                conn.execute(
                    "INSERT OR IGNORE INTO licenses VALUES (?,?,?,?,?,?,?,?)",
                    [k, v.get("tier", "starter"), v.get("label", ""),
                     v.get("rate_limit", 3), v.get("rate_window_seconds", 86400),
                     v.get("expires", "2026-12-31"), int(v.get("active", True)),
                     json.dumps(v.get("features", []))]
                )
                imported += 1
            conn.commit()
            log.info(f"📦 Migrated {imported} keys from api_keys.json → SQLite")
            # Also migrate tiers
            tiers = legacy.get("tiers", {})
            if tiers and not os.path.exists(TIERS_FILE):
                os.makedirs(os.path.dirname(TIERS_FILE), exist_ok=True)
                with open(TIERS_FILE, "w") as f:
                    json.dump({"tiers": tiers, "default_tier": legacy.get("default_tier", "starter")}, f, indent=2)
        except Exception as e:
            log.error(f"Migration failed: {e}")

    _LICENSES_DB = conn
    log.info(f"🔐 License DB ready | WAL mode | {count} keys")
    return conn


def _load_tiers():
    """Load tier definitions from vilona_tiers.json. Returns dict with defaults."""
    try:
        if os.path.exists(TIERS_FILE):
            with open(TIERS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "tiers": {
            "starter": {"max_layers": 1, "features": []},
            "pro": {"max_layers": 3, "features": ["trailing", "broadcast"]},
            "elite": {"max_layers": 5, "features": ["trailing", "broadcast", "ea_download"]},
        },
        "default_tier": "starter",
    }


def _read_licenses_from_db():
    """SELECT all active keys from SQLite → {keys: {api_key: {...}}} dict."""
    rows = _LICENSES_DB.execute(
        "SELECT api_key, tier, label, rate_limit, rate_window_seconds, expires, active, features FROM licenses"
    ).fetchall()
    keys = {}
    for r in rows:
        try:
            features = json.loads(r["features"])
        except (json.JSONDecodeError, TypeError):
            features = []
        keys[r["api_key"]] = {
            "tier": r["tier"], "label": r["label"],
            "rate_limit": r["rate_limit"],
            "rate_window_seconds": r["rate_window_seconds"],
            "expires": r["expires"], "active": bool(r["active"]),
            "features": features,
        }
    return {"keys": keys}


def load_keys():
    """Return {keys: {...}, tiers: {...}, default_tier: ...} shape.
    Caches for 60s — reads from SQLite + tiers JSON."""
    global _keys_cache, _keys_cache_time
    now = time.time()
    if _keys_cache is not None and (now - _keys_cache_time) < 60:
        return _keys_cache
    try:
        config = _read_licenses_from_db()
    except Exception as e:
        log.error(f"Failed to load keys from SQLite: {e}")
        config = {"keys": {}}
    tiers = _load_tiers()
    config["tiers"] = tiers.get("tiers", {})
    config["default_tier"] = tiers.get("default_tier", "starter")
    _keys_cache = config
    _keys_cache_time = now
    return config


def gen_id():
    global ID_COUNTER
    ID_COUNTER += 1
    return f"vtfx_{int(time.time()*1000)}_{ID_COUNTER}"


def validate_key(api_key):
    """Returns (valid, tier_info). Fast path: LICENSE_CACHE. Slow path: SQLite."""
    if not api_key:
        return False, None

    now = time.time()
    # Fast path: in-memory cache
    if now - LICENSE_CACHE_TIME < LICENSE_CACHE_TTL:
        cached = LICENSE_CACHE.get(api_key)
        if cached is not None:
            return cached["active"], cached["tier_info"]

    # Slow path: SQLite under lock
    with _DB_LOCK:
        row = _LICENSES_DB.execute(
            "SELECT tier, active, features FROM licenses WHERE api_key = ?",
            [api_key]
        ).fetchone()

    if not row or not row["active"]:
        LICENSE_CACHE[api_key] = {"active": False, "tier_info": None}
        return False, None

    try:
        features = json.loads(row["features"])
    except (json.JSONDecodeError, TypeError):
        features = []

    tiers = _load_tiers()
    tier_name = row["tier"]
    starter = tiers.get("tiers", {}).get("starter", {"max_layers": 1, "features": []})
    tier_info = tiers.get("tiers", {}).get(tier_name, starter)
    tier_info = dict(tier_info)
    tier_info.setdefault("features", features)

    LICENSE_CACHE[api_key] = {"active": True, "tier_info": tier_info}
    return True, tier_info


def bust_license_cache(api_key=None):
    """Invalidate cache entries. Called after admin writes.
    Pass api_key to bust a single key; omit to bust all."""
    global LICENSE_CACHE_TIME
    if api_key:
        LICENSE_CACHE.pop(api_key, None)
    else:
        LICENSE_CACHE.clear()
        LICENSE_CACHE_TIME = 0


def check_rate_limit(api_key):
    """Returns True if request is within rate limit. rate_limit=0 means unlimited."""
    with LOCK:
        config = load_keys()
        key_data = config["keys"].get(api_key, {})
        limit = key_data.get("rate_limit", 3)
        if limit == 0:
            return True  # unlimited
        window = key_data.get("rate_window_seconds", 86400)

        now = time.time()
        timestamps = RATE_COUNTERS[api_key]
        # Clean old entries
        timestamps[:] = [t for t in timestamps if now - t < window]
        if len(timestamps) >= limit:
            return False
        timestamps.append(now)
        return True


class SignalHandler(BaseHTTPRequestHandler):

    def _json(self, data, code=200):
        try:
            body = json.dumps(data, allow_nan=False).encode()
        except (ValueError, TypeError):
            body_str = json.dumps(data)
            body_str = body_str.replace(': Infinity', ': null').replace(': -Infinity', ': null').replace(': NaN', ': null')
            body = body_str.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, code=200):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    _MIME = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".woff2": "font/woff2",
        ".ico": "image/x-icon",
    }

    def _serve_static(self, prefix="/dashboard"):
        """Serve static files from DASHBOARD_DIR for SPA paths.
        - /dashboard/assets/foo.js → file on disk
        - /dashboard, /dashboard/anything → index.html (SPA fallback)"""
        _, params = self._get_params()
        parsed = urlparse(self.path)
        req_path = parsed.path  # keep trailing slash for directory handling

        # Strip the prefix to get the relative file path
        rel = req_path
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
        if not rel or rel == "/":
            rel = "/index.html"

        file_path = os.path.normpath(os.path.join(DASHBOARD_DIR, rel.lstrip("/")))
        # Security: ensure we don't escape DASHBOARD_DIR
        if not file_path.startswith(os.path.normpath(DASHBOARD_DIR)):
            self._json({"error": "forbidden"}, 403)
            return

        if not os.path.isfile(file_path):
            # SPA fallback — all routes serve index.html
            file_path = os.path.join(DASHBOARD_DIR, "index.html")

        try:
            with open(file_path, "rb") as f:
                body = f.read()
            ext = os.path.splitext(file_path)[1].lower()
            content_type = self._MIME.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._json({"error": "not found"}, 404)
        except Exception:
            self._json({"error": "static serve error"}, 500)

    def _get_params(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return parsed.path.rstrip("/"), qs

    def _admin_auth(self, params):
        """Returns True if request is authorized for admin endpoints.
        Checks: 1) query param admin_secret, 2) Authorization Bearer header."""
        if ADMIN_SECRET:
            if params.get("admin_secret", [None])[0] == ADMIN_SECRET:
                return True
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {ADMIN_SECRET}":
                return True
        return False

    def _poll_signal(self, api_key, tier, instance_id=None):
        """Poll signal for a specific user or instance. Priority:
           instance_id → PENDING_BY_INSTANCE[instance_id]
           else       → PENDING_BY_KEY[api_key]
           fallback   → PENDING (global)"""
        with LOCK:
            # Instance-level queue (account_id provided)
            if instance_id:
                if instance_id in PENDING_BY_INSTANCE and PENDING_BY_INSTANCE[instance_id]:
                    sig = PENDING_BY_INSTANCE[instance_id].popleft()
                    log.info(f"Signal delivered (instance): {sig['signal_id']} → {instance_id}")
                    return self._format_signal(sig)

            # Key-level queue (backward compat / fallback)
            if api_key in PENDING_BY_KEY and PENDING_BY_KEY[api_key]:
                sig = PENDING_BY_KEY[api_key].popleft()
                log.info(f"Signal delivered (user): {sig['signal_id']} → {api_key}")
                return self._format_signal(sig)

            # Global fallback
            if not PENDING:
                return self._empty_signal()
            sig = PENDING.popleft()

        log.info(f"Signal delivered (global): {sig['signal_id']} → {api_key}")
        return self._format_signal(sig)

    def _format_signal(self, sig):
        """Format signal for EA consumption. Filters layers by tier."""
        max_layers = 1  # default starter

        config = load_keys()
        api_key = getattr(self, "_current_key", "")
        key_data = config["keys"].get(api_key, {})
        tier_name = key_data.get("tier", "starter")
        tier_info = config["tiers"].get(tier_name, {})
        max_layers = tier_info.get("max_layers", 1)

        layers = sig.get("layers", [])
        if layers and len(layers) > max_layers:
            layers = layers[:max_layers]

        return {
            "signal_id": sig.get("signal_id", ""),
            "symbol": sig.get("symbol", ""),
            "action": sig.get("action", "HOLD"),
            "entry": sig.get("entry", 0),
            "sl": sig.get("sl", 0),
            "tp": sig.get("tp", 0),
            "tp1": sig.get("tp1", sig.get("tp", 0)),
            "tp2": sig.get("tp2", 0),
            "tp3": sig.get("tp3", 0),
            "tp4": sig.get("tp4", 0),
            "risk_percent": sig.get("risk_percent", 1.0),
            "comment": sig.get("comment", "VTFX/AI"),
            "confidence": sig.get("confidence", 0),
            "layers": layers,
            "layer_count": len(layers),
            "tier": tier_name,
            "pending": True,
        }

    def _empty_signal(self):
        return {
            "signal_id": "", "symbol": "", "action": "HOLD",
            "entry": 0, "sl": 0, "tp": 0,
            "tp1": 0, "tp2": 0, "tp3": 0, "tp4": 0,
            "risk_percent": 0, "comment": "", "confidence": 0,
            "layers": [], "layer_count": 0, "tier": "", "pending": False,
        }

    def do_GET(self):
        path, params = self._get_params()
        api_key = params.get("api_key", [""])[0]
        account_id = params.get("account_id", [None])[0]

        if path == "/health":
            self._json({
                "status": "ok",
                "uptime_seconds": int(time.time() - START_TIME),
                "queue_size": len(PENDING),
            })
        elif path == "" or path == "/" or path == "/id" or path == "/en":
            # Proxy ALL landing routes to dashboard server (8768)
            # Dashboard handles: / → redirect, /id → ID page, /en → EN page
            try:
                req = urllib.request.Request(f"http://127.0.0.1:8768{path}")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    page_content = resp.read().decode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page_content.encode())))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(page_content.encode())
            except Exception:
                # Fallback: serve Cornix dashboard directly
                html_path = os.path.join(PROJECT_DIR, "tradebot", "web", "templates", "public_dashboard_id.html")
                try:
                    with open(html_path, "r") as f:
                        page_content = f.read()
                    # Auto-redirect meta tag for root path
                    if path in ("", "/"):
                        page_content = page_content.replace('</head>',
                            '<meta http-equiv="refresh" content="0;url=/id"></head>')
                    # Force language for /id and /en
                    if path in ("/id", "/en"):
                        force_lang = "id" if path == "/id" else "en"
                        page_content = page_content.replace('<html lang="id">', f'<html lang="{force_lang}">')
                        force_script = f'<script>setLang("{force_lang}")</script>'
                        page_content = page_content.replace('</body>', f'{force_script}\n</body>')
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page_content.encode())))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(page_content.encode())
                except FileNotFoundError:
                    self._json({"error": "page not found"}, 404)
        elif path == "/dashboard" or path.startswith("/dashboard/"):
            self._serve_static()
        elif path == "/status":
            with LOCK:
                daemon_count = len([d for d in DAEMONS.values()
                                   if time.time() - d["last_seen"] < 30])
                self._json({
                    "pending": len(PENDING) > 0,
                    "pending_id": PENDING[0]["signal_id"] if PENDING else None,
                    "history_count": len(HISTORY),
                    "last_signal_id": HISTORY[-1]["signal_id"] if HISTORY else None,
                    "daemons_online": daemon_count,
                    "daemons_total": len(DAEMONS),
                    "trade_reports": len(TRADE_REPORTS),
                    "trailing_positions": len(TRAILED_POSITIONS),
                })
        elif path == "/signal" or path == "/signal/pending":
            # Validate API key & rate limit
            is_valid, tier = validate_key(api_key)
            if not is_valid:
                self._json({"error": "invalid_api_key"}, 401)
                return
            if not check_rate_limit(api_key):
                self._json({"error": "rate_limited", "action": "HOLD", "pending": False}, 429)
                return

            self._current_key = api_key
            account_id = params.get("account_id", [None])[0]
            mode = params.get("mode", ["signal"])[0]
            daemon_id = params.get("daemon_id", [None])[0]

            # ── Daemon mode: serve trailing SL updates ──
            if mode == "trailing" and daemon_id:
                with LOCK:
                    DAEMONS[daemon_id]["last_seen"] = time.time()
                    if DAEMON_SL_QUEUE[daemon_id]:
                        sig = DAEMON_SL_QUEUE[daemon_id].popleft()
                        sig["daemon_id"] = daemon_id
                        self._json(sig)
                    else:
                        self._json({"action": "HOLD", "status": "idle",
                                   "pending": False, "daemon_id": daemon_id})
                return

            if account_id:
                # ── Instance Identity mode ──
                instance_id = f"{api_key}:{account_id}"
                is_new = instance_id not in INSTANCES
                # Track instance
                INSTANCES[instance_id] = {
                    "last_seen": time.time(),
                    "ip": self.client_address[0],
                    "signals_polled": INSTANCES.get(instance_id, {}).get("signals_polled", 0) + 1,
                    "first_seen": INSTANCES.get(instance_id, {}).get("first_seen", time.time()),
                    "label": INSTANCES.get(instance_id, {}).get("label", account_id),
                    "api_key": api_key,
                    "account_id": account_id,
                }
                # ── Auto-detect broker params from EA query ──
                if is_new or "account_type" not in TRAIL_CONFIG[instance_id]:
                    acct_type = params.get("account_type", [None])[0]
                    digits_str = params.get("digits", [None])[0]
                    if acct_type and acct_type in BROKER_PROFILES:
                        profile = BROKER_PROFILES[acct_type]
                        for k, v in profile.items():
                            TRAIL_CONFIG[instance_id][k] = v
                        TRAIL_CONFIG[instance_id]["account_type"] = acct_type
                        _save_trail_config()
                        log.info(f"🔍 Auto-detected broker: {instance_id} → {acct_type}"
                                f" (pip={profile['pip_value']}, digits={profile['digits']})")
                    elif digits_str:
                        try:
                            d = int(digits_str)
                            TRAIL_CONFIG[instance_id]["digits"] = d
                            TRAIL_CONFIG[instance_id]["pip_value"] = 0.10 if d == 2 else 0.01
                            _save_trail_config()
                            log.info(f"🔍 Digits detected: {instance_id} → {d}d")
                        except ValueError:
                            pass
                MASTER_INSTANCES[api_key][account_id] = instance_id
                # Seed new instance with latest pending signal from multiple sources
                if is_new:
                    seed_sig = None
                    # Priority 1: global PENDING (shared queue)
                    if PENDING:
                        seed_sig = PENDING[-1]
                    # Priority 2: key-level queue
                    elif api_key in PENDING_BY_KEY and PENDING_BY_KEY[api_key]:
                        seed_sig = PENDING_BY_KEY[api_key][-1]
                    # Priority 3: other instance queues under same key
                    elif api_key in MASTER_INSTANCES:
                        for other_acct in MASTER_INSTANCES[api_key]:
                            other_iid = MASTER_INSTANCES[api_key][other_acct]
                            if other_iid != instance_id and other_iid in PENDING_BY_INSTANCE and PENDING_BY_INSTANCE[other_iid]:
                                seed_sig = PENDING_BY_INSTANCE[other_iid][-1]
                                break
                    # Priority 4: history fallback (latest signal ever seen)
                    if seed_sig is None and HISTORY:
                        seed_sig = HISTORY[-1]
                    if seed_sig is not None:
                        sig_copy = dict(seed_sig)
                        sig_copy["_for_instance"] = instance_id
                        PENDING_BY_INSTANCE[instance_id].append(sig_copy)
                result = self._poll_signal(api_key, tier, instance_id=instance_id)
            else:
                # ── Legacy mode (no account_id) ──
                CONNECTED_ACCOUNTS[api_key] = {
                    "last_seen": time.time(),
                    "ip": self.client_address[0],
                    "signals_polled": CONNECTED_ACCOUNTS.get(api_key, {}).get("signals_polled", 0) + 1,
                    "first_seen": CONNECTED_ACCOUNTS.get(api_key, {}).get("first_seen", time.time()),
                    "label": CONNECTED_ACCOUNTS.get(api_key, {}).get("label", api_key[:12]),
                }
                result = self._poll_signal(api_key, tier)

            self._json(result)


        elif path.startswith("/ack/"):
            signal_id = path.split("/ack/", 1)[1]
            with LOCK:
                ACKED.add(signal_id)
                if api_key:
                    ACKED_BY_KEY[api_key].add(signal_id)
            log.info(f"EA ack: {signal_id} | key={api_key}")
            self._json({"status": "ok", "signal_id": signal_id})

        elif path == "/keys" or path == "/admin/keys":
            # Admin: list keys (SQLite-backed, admin_secret or localhost auth)
            if not self._admin_auth(params):
                self._json({"error": "admin only — provide ?admin_secret= or use localhost"}, 403)
                return
            config = load_keys()
            keys_safe = {
                k: {"tier": v["tier"], "label": v.get("label", ""),
                    "rate_limit": v.get("rate_limit", "?"), "active": v["active"],
                    "expires": v.get("expires", "")}
                for k, v in config["keys"].items()
            }
            self._json({"keys": keys_safe, "tiers": config["tiers"]})

        elif path == "/history":
            # ── Require valid API key ──
            if not validate_key(api_key)[0]:
                self._json({"error": "api_key required"}, 403)
                return
            with LOCK:
                self._json({"count": len(HISTORY), "signals": list(HISTORY)})
        elif path == "/accounts":
            # ── Require valid API key ──
            if not validate_key(api_key)[0]:
                self._json({"error": "api_key required"}, 403)
                return
            with LOCK:
                # List all instances grouped by api_key
                now = time.time()
                instances_data = {}
                for inst_id, inst in INSTANCES.items():
                    instances_data[inst_id] = {
                        **inst,
                        "last_seen_ago_sec": int(now - inst["last_seen"]),
                        "uptime_sec": int(now - inst["first_seen"]),
                        "online": (now - inst["last_seen"]) < 120,
                        "pending_signals": len(PENDING_BY_INSTANCE.get(inst_id, deque())),
                    }

                # Build master_keys grouping
                master_keys = {}
                for inst_id in sorted(INSTANCES.keys()):
                    api_key = inst_id.split(":", 1)[0]
                    if api_key not in master_keys:
                        master_keys[api_key] = {"instance_ids": [], "instance_count": 0}
                    master_keys[api_key]["instance_ids"].append(inst_id)
                    master_keys[api_key]["instance_count"] += 1

                # Legacy CONNECTED_ACCOUNTS (no account_id)
                legacy_accounts = {}
                for key, acc in CONNECTED_ACCOUNTS.items():
                    legacy_accounts[key] = {
                        **acc,
                        "last_seen_ago_sec": int(now - acc["last_seen"]),
                        "uptime_sec": int(now - acc["first_seen"]),
                        "online": (now - acc["last_seen"]) < 120,
                        "signals_acked": len(ACKED_BY_KEY.get(key, set())),
                        "pending_signals": len(PENDING_BY_KEY.get(key, deque())),
                    }

            self._json({
                "total_instances": len(INSTANCES),
                "instances": instances_data,
                "master_keys_count": len(master_keys),
                "master_keys": dict(master_keys),
                "legacy_accounts": legacy_accounts,
                "bridge_uptime_sec": int(now - START_TIME),
                "mode": "instance_broadcast",
            })
        elif path == "/download/ea" or path == "/download/ea.ex5" or path == "/ea/download":
            # ── TIER GATE: require valid API key with pro/elite tier ──
            is_valid, tier_info = validate_key(api_key)
            if not is_valid:
                self._json({"error": "premium only — valid API key required"}, 403)
                return
            key_config = load_keys()
            key_data = key_config["keys"].get(api_key, {})
            if key_data.get("tier", "starter") == "starter":
                self._json({"error": "upgrade to PRO/ELITE to access EA download"}, 403)
                return
            # Serve EA compiled binary for download (no source — misuse prevention)
            ea_path = os.path.join(PROJECT_DIR, "ea", "VilonaTradeFX_EA.ex5")
            try:
                with open(ea_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename=VilonaTradeFX_EA.ex5")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._json({"error": "file not found"}, 404)
        elif path == "/lp" or path == "/lp/":
            # Landing page
            lp_path = "/var/www/phantomfx-lp/index.html"
            try:
                with open(lp_path, "r") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(content.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            except FileNotFoundError:
                self._json({"error": "landing page not found"}, 404)
        elif path == "/track.js":
            # Proxy to tracking API on port 8790
            try:
                req = urllib.request.Request("http://127.0.0.1:8790/track.js")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    content = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self._json({"error": f"tracking proxy: {e}"}, 502)
        elif path.startswith("/api/track/"):
            # Proxy to tracking API on port 8790
            try:
                qs = "&".join(f"{k}={','.join(v)}" for k, v in params.items()) if params else ""
                proxy_path = path + ("?" + qs if qs else "")
                req = urllib.request.Request("http://127.0.0.1:8790" + proxy_path)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    content = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(e.read())
            except Exception as e:
                self._json({"error": f"tracking proxy: {e}"}, 502)
        elif path == "/api/trade-log":
            log_path = os.path.join(PROJECT_DIR, "data", "trade_log.json")
            try:
                with open(log_path) as f:
                    self._json(json.load(f))
            except (FileNotFoundError, json.JSONDecodeError):
                self._json([])
        elif path == "/api/dash-stats":
            stats = {"total": 0, "win_rate": 0, "total_profit": 0, "trades": [], "uptime": "", "ea_count": 0}
            try:
                scripts_dir = os.path.join(PROJECT_DIR, "scripts")
                if scripts_dir not in sys.path:
                    sys.path.insert(0, scripts_dir)
                from trade_tracker import get_stats, get_recent_trades
                s = get_stats()
                stats.update(s)
                stats["trades"] = get_recent_trades(10)
                stats["total_profit"] = s.get("total_profit_usd", 0)
            except Exception:
                pass
            # XAU spot price
            try:
                req = urllib.request.Request("https://api.gold-api.com/price/XAU", headers={"User-Agent": "VilonaBridge/1.0"})
                with urllib.request.urlopen(req, timeout=4) as r:
                    xau = json.loads(r.read())
                stats["xau_price"] = float(xau.get("price", 0))
            except Exception:
                pass
            uptime = int(time.time() - START_TIME)
            h, m = divmod(uptime, 3600)
            mi, s = divmod(m, 60)
            stats["uptime"] = f"{h}h {mi}m"
            with LOCK:
                stats["ea_count"] = len([i for i in INSTANCES.values() if time.time() - i["last_seen"] < 120])
            self._json(stats)
        elif path == "/api/engine-readings":
            eng_path = os.path.join(PROJECT_DIR, "bridges", "signal_bridge", "engine_status.json")
            try:
                with open(eng_path, "r") as f:
                    cached = json.load(f)
                    # Use cached if less than 120s old
                    if time.time() - os.path.getmtime(eng_path) < 120:
                        self._json(cached)
                        return
            except Exception:
                pass
            # Generate fresh MTF matrix — run_engine_consensus fetches all 5 TFs internally
            try:
                from engine_consensus import run_engine_consensus
                result = run_engine_consensus(symbol="XAUUSD")
                # Build dashboard-friendly MTF response
                dashboard_output = {
                    "symbol": result.get("symbol", "XAUUSD"),
                    "price": result.get("price", 0),
                    "timestamp": result.get("timestamp", ""),
                    "timeframes": {},
                    "hierarchical": result.get("hierarchical", {}),
                    "mtf_alignment": result.get("mtf_alignment", "NONE"),
                    "macro_trend": result.get("macro_trend", "NEUTRAL"),
                    "counter_trend_flags": result.get("counter_trend_flags", []),
                }
                from engine_consensus import TIMEFRAMES as _TFS, TF_WEIGHTS as _TFW
                for tf in _TFS:
                    tr = result.get("timeframes", {}).get(tf, {})
                    if tr:
                        dashboard_output["timeframes"][tf] = {
                            "verdict": tr["verdict"],
                            "consensus_pct": tr["consensus_pct"],
                            "buy_count": tr["buy_count"],
                            "sell_count": tr["sell_count"],
                            "total": tr["total"],
                            "engines": tr.get("engines", {}),
                            "weight": _TFW.get(tf, 0),
                        }
                        if "macro" in tr:
                            dashboard_output["timeframes"][tf]["macro"] = tr["macro"]
                        if "structure" in tr:
                            dashboard_output["timeframes"][tf]["structure"] = tr["structure"]
                        if "entry" in tr:
                            dashboard_output["timeframes"][tf]["entry"] = tr["entry"]
                # Save to cache
                try:
                    with open(eng_path, "w") as f:
                        json.dump(dashboard_output, f, indent=2)
                except Exception:
                    pass
                # Also include backwards-compat fields for older dashboard
                active_tf = dashboard_output["timeframes"].get("M15", {})
                dashboard_output["engines"] = active_tf.get("engines", {})
                dashboard_output["verdict"] = dashboard_output["hierarchical"].get("verdict", "HOLD")
                dashboard_output["consensus_pct"] = dashboard_output["hierarchical"].get("consensus_score", 0)
                dashboard_output["buy_count"] = active_tf.get("buy_count", 0)
                dashboard_output["sell_count"] = active_tf.get("sell_count", 0)
                dashboard_output["total"] = active_tf.get("total", 0)
                self._json(dashboard_output)
            except Exception as e:
                log.error(f"/api/engine-readings error: {e}")
                self._json({"error": str(e), "engines": {}, "verdict": "N/A", "timeframes": {}})
        elif path == "/api/news":
            """Fetch latest XAUUSD/news from RSS."""
            try:
                import urllib.request as ur
                import xml.etree.ElementTree as ET
                items = []
                url = "https://finance.yahoo.com/rss/headline?s=GC=F"
                req = ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with ur.urlopen(req, timeout=8) as resp:
                    xml = resp.read()
                    root = ET.fromstring(xml)
                    for item in root.iter("item"):
                        title = item.findtext("title", "")
                        link = item.findtext("link", "")
                        pubdate = item.findtext("pubDate", "")
                        if title:
                            items.append({"title": title, "link": link, "date": pubdate})
                self._json({"items": items[:12]})
            except Exception as e:
                log.error(f"News fetch error: {e}")
                self._json({"items": [], "error": str(e)})
        elif path == "/api/config":
            client_ip = self.client_address[0]
            if client_ip not in ("127.0.0.1", "::1", "localhost"):
                self._text("Forbidden", 403)
                return
            master_key = os.environ.get("BRIDGE_MASTER_KEY", "")
            if not master_key:
                self._text("Not configured", 503)
                return
            self._json({"api_key": master_key})
        elif path == "/trailing":
            # GET: view trailing config | POST: update trailing config
            instance_id = f"{api_key}:{account_id}" if (api_key and account_id) else None
            if not instance_id:
                self._json({"error": "api_key and account_id required"}, 400)
                return
            is_valid, _ = validate_key(api_key)
            if not is_valid:
                self._json({"error": "invalid_api_key"}, 401)
                return

            if self.command == "GET":
                cfg = dict(TRAIL_CONFIG[instance_id])
                pos = TRAILED_POSITIONS.get(instance_id)
                cfg["active_position"] = pos is not None
                if pos:
                    cfg["position_preview"] = {
                        "entry": pos["entry"], "sl": pos["current_sl"],
                        "direction": pos["direction"], "age_sec": int(time.time() - pos["timestamp"])
                    }
                self._json(cfg)
            elif self.command == "POST":
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    self._json({"error": "invalid json"}, 400)
                    return
                with LOCK:
                    if "enabled" in body:
                        TRAIL_CONFIG[instance_id]["enabled"] = bool(body["enabled"])
                    if "mode" in body:
                        TRAIL_CONFIG[instance_id]["mode"] = body["mode"]
                    if "trail_pips" in body:
                        TRAIL_CONFIG[instance_id]["trail_pips"] = int(body["trail_pips"])
                    if "breakeven_pips" in body:
                        TRAIL_CONFIG[instance_id]["breakeven_pips"] = int(body["breakeven_pips"])
                    if "step_pips" in body:
                        TRAIL_CONFIG[instance_id]["step_pips"] = int(body["step_pips"])
                _save_trail_config()
                self._json({"status": "ok", "config": dict(TRAIL_CONFIG[instance_id])})
        elif path == "/api/donations":
            """Return total donations from payment orders."""
            try:
                if PROJECT_DIR not in sys.path:
                    sys.path.insert(0, PROJECT_DIR)
                from members import get_total_donations
                self._json({"total_raised": get_total_donations(), "currency": "IDR"})
            except Exception as e:
                log.error(f"/api/donations error: {e}")
                self._json({"total_raised": 0, "error": str(e)})
        elif path == "/api/create-payment":
            """Create Tripay payment for LP visitor. Params: amount (int), method (str, default QRIS2).
            POST body (optional): {"merchant_ref": "click_id_from_bemob"}"""
            try:
                amount = int(params.get("amount", ["50000"])[0])
                method = params.get("method", ["QRIS2"])[0]
            except ValueError:
                self._json({"error": "Invalid amount"}, 400)
                return

            # Read merchant_ref (BeMob click_id) from POST body if provided
            custom_merchant_ref = None
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length > 0:
                    body_raw = self.rfile.read(content_length)
                    body = json.loads(body_raw.decode())
                    custom_merchant_ref = body.get("merchant_ref", "").strip() or None
            except (json.JSONDecodeError, ValueError, KeyError):
                pass  # No body or invalid JSON → use auto-generated ref

            # Map amount to tier
            if amount >= 500000:
                tier = "lifetime"
            elif amount >= 150000:
                tier = "elite"
            else:
                tier = "pro"

            # Generate web session ID for LP visitors
            import secrets
            session_id = f"web_{int(time.time())}_{secrets.token_hex(4)}"

            try:
                # Add project root to path so members module can be imported
                if PROJECT_DIR not in sys.path:
                    sys.path.insert(0, PROJECT_DIR)
                from members.payment import create_tripay_payment
                result = create_tripay_payment(
                    chat_id=session_id,
                    username="LP_Visitor",
                    tier=tier,
                    method=method,
                    amount=amount,
                    merchant_ref=custom_merchant_ref,
                )
                if "error" in result:
                    self._json({"error": result["error"]}, 500)
                else:
                    self._json(result)
            except Exception as e:
                log.error(f"/api/create-payment error: {e}")
                self._json({"error": str(e)}, 500)
        else:
            # Proxy to dashboard server for all unrecognized paths (API calls + static)
            try:
                proxy_path = path
                if params:
                    qs = "&".join(f"{k}={','.join(v)}" for k,v in params.items())
                    proxy_path += "?" + qs
                req = urllib.request.Request(f"http://127.0.0.1:8768{proxy_path}")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    content = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(content)
            except urllib.error.HTTPError as e:
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(e.read())
            except Exception as e:
                self._json({"error": f"dashboard proxy: {e}"}, 502)

    def _forward_webhook(self, path):
        """Forward webhook calls to payment_webhook on port 8787."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            import urllib.request as ur
            # Forward ALL relevant headers, especially X-Callback-Signature for HMAC
            fwd_headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
            for hdr in ("X-Callback-Signature", "X-Callback-Event", "User-Agent"):
                val = self.headers.get(hdr)
                if val:
                    fwd_headers[hdr] = val
            req = ur.Request(f"http://127.0.0.1:8787{path}",
                             data=body or None,
                             headers=fwd_headers)
            resp = ur.urlopen(req, timeout=30)
            result = resp.read()
            self._json(json.loads(result) if result else {"status": "forwarded"})
        except Exception as e:
            log.error(f"Webhook forward failed: {e}")
            self._json({"error": "webhook_failed", "detail": str(e)}, 500)

    def do_POST(self):
        global _keys_cache  # admin CRUD invalidates load_keys() cache
        path, params = self._get_params()
        api_key = params.get("api_key", [""])[0]
        account_id = params.get("account_id", [None])[0]

        if path == "/api/capi":
            """Facebook CAPI — forward events to Conversions API."""
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return
            event_name = body.get("event_name", "PageView")
            event_data = body.get("event_data", {})
            fb_pixel = os.environ.get("FB_PIXEL_ID", "")
            fb_token = os.environ.get("FB_ACCESS_TOKEN", "")
            if not fb_token:
                self._json({"status": "skipped", "reason": "no FB_ACCESS_TOKEN configured"}, 200)
                return
            if not fb_pixel:
                self._json({"status": "skipped", "reason": "no FB_PIXEL_ID configured"}, 200)
                return
            try:
                import urllib.request as ureq
                capi_url = f"https://graph.facebook.com/v19.0/{fb_pixel}/events?access_token={fb_token}"
                capi_payload = json.dumps({
                    "data": [{
                        "event_name": event_name,
                        "event_time": int(time.time()),
                        "action_source": "website",
                        "event_source_url": body.get("source_url", "https://phantomfx.aitradepulse.com/lp"),
                        "user_data": {
                            "client_ip_address": self.client_address[0],
                            "client_user_agent": self.headers.get("User-Agent", "")
                        },
                        "custom_data": event_data
                    }]
                }).encode()
                req = ureq.Request(capi_url, data=capi_payload, headers={"Content-Type": "application/json"})
                resp = ureq.urlopen(req, timeout=5)
                result = json.loads(resp.read())
                self._json({"status": "sent", "fb_response": result})
            except Exception as e:
                log.error(f"CAPI error: {e}")
                self._json({"status": "error", "detail": str(e)}, 500)

        elif path == "/signal":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            sig_id = gen_id()
            action = data.get("action", "HOLD")
            symbol = data.get("symbol", "XAUUSD")

            signal = {
                "signal_id": sig_id,
                "symbol": symbol,
                "action": action,
                "entry": data.get("entry"),
                "sl": data.get("sl"),
                "tp": data.get("tp"),
                "tp1": data.get("tp1", data.get("tp")),
                "tp2": data.get("tp2"),
                "risk_percent": data.get("risk_percent", 1.0),
                "confidence": data.get("confidence"),
                "rr_ratio": data.get("rr_ratio", 0),
                "comment": data.get("comment", "VTFX/AI"),
                "source": data.get("source", "vtfx"),
                "timestamp": data.get("timestamp"),
                "received_at": time.time(),
                "status": "pending",
                "layers": data.get("layers", []),
                "target_user": data.get("target_user"),
            }

            broadcast_count = 0
            _entry = data.get("entry", 0)
            _sl = data.get("sl", 0)
            _tp = data.get("tp", 0)

            # ── Content-based dedup (60s TTL) ──
            dedup_key = f"{action}|{symbol}|{data.get('entry',0)}|{data.get('sl',0)}|{data.get('tp',0)}"
            dedup_hash = hashlib.sha256(dedup_key.encode()).hexdigest()
            now = time.time()
            with LOCK:
                last_seen = _signal_dedup_cache.get(dedup_hash)
                if last_seen is not None and (now - last_seen) < SIGNAL_DEDUP_TTL:
                    self._json({"error": "duplicate_signal", "signal_id": sig_id, "detail": "Same signal received within 60s"}, 409)
                    return
                _signal_dedup_cache[dedup_hash] = now

            with LOCK:
                HISTORY.append(signal)

                # ── Write to EA signal file for ea_executor.py ──
                try:
                    from pathlib import Path
                    ea_file = Path("/home/openclaw/projects/1ai-trade-bot/data/vilona_tradefx/ea_signal.json")
                    ea_file.parent.mkdir(parents=True, exist_ok=True)
                    ea_file.write_text(json.dumps(signal, indent=2, default=str))
                except Exception:
                    pass

                # ── BROADCAST MODE: duplicate signal to instances / accounts ──
                target = signal.get("target_user")  # optional: single-user routing
                broadcast_api_key = api_key if api_key else None  # scope broadcast to this key if provided

                if target:
                    # Single-user mode — deliver to one specific account
                    PENDING_BY_KEY[target].append(signal)
                    broadcast_count = 1
                elif broadcast_api_key and broadcast_api_key in MASTER_INSTANCES and MASTER_INSTANCES[broadcast_api_key]:
                    # Broadcast to ALL instances under this api_key
                    for acct_id in list(MASTER_INSTANCES[broadcast_api_key].keys()):
                        instance_id = MASTER_INSTANCES[broadcast_api_key][acct_id]
                        acct_signal = dict(signal)
                        acct_signal["_for_instance"] = instance_id
                        PENDING_BY_INSTANCE[instance_id].append(acct_signal)
                        broadcast_count += 1
                        # ── Track for smart trailing ──
                        if action in ("BUY", "SELL") and TRAIL_CONFIG[instance_id]["enabled"]:
                            TRAILED_POSITIONS[instance_id] = {
                                "signal_id": sig_id, "entry": _entry,
                                "current_sl": _sl, "direction": action,
                                "tp": _tp, "timestamp": time.time()
                            }
                    log.info(f"📡 Instance broadcast ({broadcast_api_key}): {broadcast_count} instance(s)")
                    # Also queue to global fallback so newly-connecting instances get it
                    PENDING.append(signal)
                elif broadcast_api_key:
                    # Key has no registered instances — fall back to global
                    PENDING.append(signal)
                    log.info(f"📡 No instances for {broadcast_api_key}, queued global")
                else:
                    # No api_key in POST — broadcast to ALL instances of ALL master keys
                    for mk in list(MASTER_INSTANCES.keys()):
                        for acct_id in list(MASTER_INSTANCES[mk].keys()):
                            instance_id = MASTER_INSTANCES[mk][acct_id]
                            acct_signal = dict(signal)
                            acct_signal["_for_instance"] = instance_id
                            PENDING_BY_INSTANCE[instance_id].append(acct_signal)
                            broadcast_count += 1
                    # Fallback to legacy accounts if no instances
                    if broadcast_count == 0:
                        for key in list(CONNECTED_ACCOUNTS.keys()):
                            acct_signal = dict(signal)
                            acct_signal["_for_account"] = key
                            PENDING_BY_KEY[key].append(acct_signal)
                            broadcast_count += 1
                    if broadcast_count == 0:
                        PENDING.append(signal)
                    log.info(f"📡 Broadcast to {broadcast_count} instance(s)")

            layers_count = len(signal['layers']) if isinstance(signal.get('layers'), list) else 0
            log.info(f"Signal: {sig_id} | {symbol} {action} | layers={layers_count} | broadcast→{broadcast_count}")
            self._json({
                "signal_id": sig_id,
                "status": "queued",
                "broadcast_count": broadcast_count,
                "mode": "broadcast" if broadcast_count > 0 else "queued",
            })

        elif path == "/trailing":
            # POST trailing config (bridge-side handler)
            instance_id = f"{api_key}:{account_id}" if (api_key and account_id) else None
            if not instance_id:
                self._json({"error": "api_key and account_id required"}, 400)
                return
            is_valid, _ = validate_key(api_key)
            if not is_valid:
                self._json({"error": "invalid_api_key"}, 401)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return
            with LOCK:
                if "enabled" in body:
                    TRAIL_CONFIG[instance_id]["enabled"] = bool(body["enabled"])
                if "mode" in body:
                    TRAIL_CONFIG[instance_id]["mode"] = body["mode"]
                if "trail_pips" in body:
                    TRAIL_CONFIG[instance_id]["trail_pips"] = int(body["trail_pips"])
                if "breakeven_pips" in body:
                    TRAIL_CONFIG[instance_id]["breakeven_pips"] = int(body["breakeven_pips"])
                if "step_pips" in body:
                    TRAIL_CONFIG[instance_id]["step_pips"] = int(body["step_pips"])
                # Broker-specific overrides
                if "account_type" in body and body["account_type"] in BROKER_PROFILES:
                    profile = BROKER_PROFILES[body["account_type"]]
                    for k, v in profile.items():
                        TRAIL_CONFIG[instance_id][k] = v
                    TRAIL_CONFIG[instance_id]["account_type"] = body["account_type"]
                if "pip_value" in body:
                    TRAIL_CONFIG[instance_id]["pip_value"] = float(body["pip_value"])
                if "digits" in body:
                    TRAIL_CONFIG[instance_id]["digits"] = int(body["digits"])
                if "spread_buffer" in body:
                    TRAIL_CONFIG[instance_id]["spread_buffer"] = float(body["spread_buffer"])
            _save_trail_config()
            self._json({"status": "ok", "config": dict(TRAIL_CONFIG[instance_id])})

        elif path == "/trade-status":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            daemon_id = body.get("daemon_id", "")
            ticket = body.get("ticket", 0)
            status = body.get("status", "unknown")
            reason = body.get("reason", "")
            actual_sl = body.get("actual_sl") or body.get("new_sl", 0)
            instance_id = f"{body.get('api_key', '')}:{body.get('account_id', '')}"

            with LOCK:
                TRADE_REPORTS.append({
                    "time": time.time(), "daemon_id": daemon_id,
                    "instance_id": instance_id, "ticket": ticket,
                    "status": status, "reason": reason, "sl": actual_sl,
                })
                if daemon_id:
                    DAEMONS[daemon_id]["last_seen"] = time.time()
                    if ticket:
                        DAEMONS[daemon_id]["active_ticket"] = ticket

                if instance_id and instance_id in TRAILED_POSITIONS:
                    pos = TRAILED_POSITIONS[instance_id]
                    if status == "ok":
                        pos["current_sl"] = actual_sl
                        log.info(f"📊 Trade status: {instance_id} ticket={ticket} SL→{actual_sl}")
                    elif status in ("rejected", "error"):
                        error_type = "REQUOTE" if "10016" in reason or "requote" in reason.lower() \
                                else "INVALID_STOPS" if "10019" in reason or "invalid" in reason.lower() \
                                else "ERROR"
                        action = "RETRY3x" if error_type == "REQUOTE" else "RE-FETCH" if error_type == "INVALID_STOPS" else "HOLD"
                        log.warning(f"⚠️ SL {status}: {instance_id} ticket={ticket} "
                                   f"reason={reason} — SL NOT updated in bridge")
                        send_telegram_alert(
                            f"⚠️ <b>SL MODIFICATION {status.upper()}</b>\n"
                            f"<b>Instance:</b> <code>{instance_id}</code>\n"
                            f"<b>Ticket:</b> {ticket}\n"
                            f"<b>Error:</b> {error_type} — {reason}\n"
                            f"<b>Bridge Action:</b> {action}\n"
                            f"<i>SL in bridge memory is NOT updated. State remains in sync.</i>"
                        )
                    elif status == "closed":
                        del TRAILED_POSITIONS[instance_id]
                        log.info(f"🏁 Position closed: {instance_id} ticket={ticket}")
                        send_telegram_alert(
                            f"🏁 <b>POSITION CLOSED</b>\n"
                            f"<b>Instance:</b> <code>{instance_id}</code>\n"
                            f"<b>Ticket:</b> {ticket}\n"
                            f"<i>Removed from trailing engine.</i>"
                        )

                if status == "reconciliation":
                    RECONCILE_LOG.append({
                        "time": time.time(), "daemon_id": daemon_id,
                        "ticket": ticket, "sl": actual_sl,
                        "tp": body.get("actual_tp", 0), "profit": body.get("profit", 0),
                    })
                    if instance_id and instance_id in TRAILED_POSITIONS:
                        bridge_sl = TRAILED_POSITIONS[instance_id]["current_sl"]
                        if abs(actual_sl - bridge_sl) > 0.001:
                            log.warning(f"🔍 DRIFT: {instance_id} bridge={bridge_sl} broker={actual_sl} — forcing sync")
                            TRAILED_POSITIONS[instance_id]["current_sl"] = actual_sl
                            send_telegram_alert(
                                f"🔍 <b>SILENT DRIFT DETECTED</b>\n"
                                f"<b>Instance:</b> <code>{instance_id}</code>\n"
                                f"<b>Ticket:</b> {ticket}\n"
                                f"<b>Bridge SL:</b> {bridge_sl}\n"
                                f"<b>Broker SL:</b> {actual_sl}\n"
                                f"<b>Delta:</b> {abs(actual_sl - bridge_sl):.3f}\n"
                                f"<i>State forced to broker reality. Reconciliation applied.</i>"
                            )
                        else:
                            log.debug(f"🔍 Reconcile OK: {instance_id} SL={actual_sl} (no drift)")
                            TRAILED_POSITIONS[instance_id]["current_sl"] = actual_sl

            self._json({"status": "ok", "report_id": len(TRADE_REPORTS)})

        elif path == "/daemon/register":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            daemon_id = body.get("daemon_id", "")
            if not daemon_id:
                self._json({"error": "daemon_id required"}, 400)
                return

            with LOCK:
                DAEMONS[daemon_id] = {
                    "account_id": body.get("account_id", ""),
                    "api_key": body.get("api_key", ""),
                    "last_seen": time.time(),
                    "hostname": body.get("hostname", ""),
                    "mt5_version": body.get("mt5_version", ""),
                    "active_ticket": 0,
                }
            log.info(f"🔌 Daemon registered: {daemon_id} | host={body.get('hostname', '?')} "
                    f"| mt5={body.get('mt5_version', '?')} | account={body.get('account_id', '')}")
            self._json({"status": "ok", "daemon_id": daemon_id, "daemon_count": len(DAEMONS)})

        elif path.startswith("/ack/"):
            signal_id = path.split("/ack/", 1)[1]
            with LOCK:
                ACKED.add(signal_id)
                if api_key:
                    ACKED_BY_KEY[api_key].add(signal_id)
            log.info(f"EA ack: {signal_id} | key={api_key}")
            self._json({"status": "ok", "signal_id": signal_id})

        elif path.startswith("/webhook/"):
            # Forward to payment webhook on port 8787
            self._forward_webhook(path)

        elif path == "/admin/keys":
            # Admin: create or update license (SQLite-backed, admin_secret auth)
            if not self._admin_auth(params):
                self._json({"error": "admin only — provide ?admin_secret= or use localhost"}, 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            import secrets, string

            tier = data.get("tier", "starter")
            # Auto-generate key if not provided
            if not data.get("api_key"):
                prefix = {"starter": "VT-FREE", "pro": "VT-PRO", "elite": "VT-ELITE"}.get(tier, "VT-FREE")
                suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
                api_key = f"{prefix}-{suffix}"
            else:
                api_key = data["api_key"]

            label = data.get("label", f"Generated {tier}")
            rate_limit = data.get("rate_limit", {"starter": 3, "pro": 50, "elite": 200}.get(tier, 3))
            rate_window = data.get("rate_window_seconds", 86400)
            expires = data.get("expires", "2026-12-31")
            active = int(data.get("active", True))
            features = json.dumps(data.get("features", []))

            with _DB_LOCK:
                _LICENSES_DB.execute(
                    "INSERT OR REPLACE INTO licenses VALUES (?,?,?,?,?,?,?,?)",
                    [api_key, tier, label, rate_limit, rate_window, expires, active, features]
                )
                _LICENSES_DB.commit()

            bust_license_cache(api_key)
            bust_license_cache()
            _keys_cache = None  # force load_keys() refresh

            log.info(f"🔑 License upserted: {api_key} ({tier}) label={label}")
            self._json({"api_key": api_key, "tier": tier, "status": "saved"})

        elif path == "/admin/keys/revoke":
            # Admin: revoke license (set active=0, SQLite-backed)
            if not self._admin_auth(params):
                self._json({"error": "admin only"}, 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            target_key = data.get("api_key", "")
            if not target_key:
                self._json({"error": "api_key required"}, 400)
                return

            with _DB_LOCK:
                _LICENSES_DB.execute(
                    "UPDATE licenses SET active = 0 WHERE api_key = ?", [target_key]
                )
                _LICENSES_DB.commit()

            bust_license_cache(target_key)
            _keys_cache = None

            log.info(f"🚫 License revoked: {target_key}")
            self._json({"status": "revoked", "api_key": target_key})

        elif path == "/api/create-payment":
            """Create Tripay payment transaction (proxied from whitelisted IP)"""
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            import hmac

            plan = data.get("plan", "video-course")
            customer_name = data.get("customer_name", "Student")
            customer_email = data.get("customer_email", "student@example.com")
            customer_phone = data.get("customer_phone", "")
            method = data.get("method", "QRIS")

            PRICES = {
                "video-course": 299000, "online-live": 799000,
                "offline-workshop": 2500000, "monthly-sub": 199000,
                "platinum-pass": 9000000,
            }
            NAMES = {
                "video-course": "Video Course Belajar AI",
                "online-live": "Online Live Belajar AI",
                "offline-workshop": "Offline Workshop Belajar AI",
                "monthly-sub": "Monthly Subscription Belajar AI",
                "platinum-pass": "Platinum Pass Belajar AI",
            }

            if plan not in PRICES:
                self._json({"error": f"Invalid plan: {plan}"}, 400)
                return

            amount = PRICES[plan]
            merchant_ref = f"BLJ-BRIDGE-{int(time.time()*1000)}"

            mc = os.environ.get("TRIPAY_MERCHANT_CODE", "T23409")
            pk = os.environ.get("TRIPAY_PRIVATE_KEY", "")
            ak = os.environ.get("TRIPAY_API_KEY", "")

            if not pk or not ak:
                self._json({"error": "Tripay credentials not configured"}, 500)
                return

            raw_sig = f"{mc}{merchant_ref}{amount}"
            sig = hmac.new(pk.encode(), raw_sig.encode(), hashlib.sha256).hexdigest()

            tripay_payload = json.dumps({
                "method": method,
                "merchant_ref": merchant_ref,
                "amount": amount,
                "customer_name": customer_name,
                "customer_email": customer_email,
                "customer_phone": customer_phone,
                "order_items": [{"name": NAMES[plan], "price": amount, "quantity": 1}],
                "callback_url": os.environ.get("TRIPAY_CALLBACK_URL", "https://phantomfx.aitradepulse.com/webhook/tripay"),
                "return_url": "https://berkahkarya.org/id/belajarai",
                "expired_time": int(time.time()) + 86400,
                "signature": sig,
            })

            try:
                req = urllib.request.Request(
                    "https://tripay.co.id/api/transaction/create",
                    data=tripay_payload.encode(),
                    headers={"Authorization": f"Bearer {ak}", "Content-Type": "application/json"}
                )
                resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
                if resp.get("success"):
                    self._json({
                        "success": True,
                        "reference": resp["data"]["reference"],
                        "merchant_ref": merchant_ref,
                        "checkout_url": resp["data"]["checkout_url"],
                        "pay_code": resp["data"].get("pay_code"),
                        "qr_url": resp["data"].get("qr_url"),
                        "qr_string": resp["data"].get("qr_string"),
                        "amount": resp["data"]["amount"],
                        "amount_received": resp["data"]["amount_received"],
                        "plan": plan,
                    })
                else:
                    self._json({"error": resp.get("message", "Tripay error")}, 400)
            except Exception as e:
                self._json({"error": f"Payment error: {str(e)}"}, 500)

        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        log.debug(format % args)


# ── Telegram Alert System ──

def _send_telegram_raw(message):
    """POST HTML-formatted message to Telegram bot API. Uses urllib only."""
    payload = json.dumps({
        "chat_id": TELEGRAM_ALERT_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def telegram_worker():
    """Background daemon: drain TELEGRAM_QUEUE and POST to Telegram API.
    Catches all errors silently — this thread must never die."""
    log.info("📨 Telegram alert worker started")
    while True:
        try:
            message = TELEGRAM_QUEUE.get()
            if message is None:
                break
            _send_telegram_raw(message)
        except Exception:
            pass


def send_telegram_alert(message):
    """Non-blocking: push message to queue. Worker drains and sends."""
    TELEGRAM_QUEUE.put(message)


def reconciliation_loop():
    """Every 5 minutes: cross-check bridge TRAILED_POSITIONS against broker reality.
    Requests reconciliation from active daemons. Detects and alerts on silent drift."""
    log.info("🔍 Reconciliation loop started (5-min cycle)")
    while True:
        time.sleep(300)
        try:
            with LOCK:
                now = time.time()
                for instance_id, pos in list(TRAILED_POSITIONS.items()):
                    # Find the daemon responsible for this instance
                    matched_did = None
                    for did, d in DAEMONS.items():
                        d_inst = f"{d.get('api_key', '')}:{d.get('account_id', '')}"
                        if d_inst == instance_id:
                            matched_did = did
                            break

                    if not matched_did:
                        continue  # no daemon to reconcile against — skip

                    # Check last /trade-status report for this instance
                    last_report_ago = None
                    for report in reversed(TRADE_REPORTS):
                        if report.get("instance_id") == instance_id:
                            last_report_ago = now - report.get("time", 0)
                            break

                    # If no report in 10 minutes, request reconciliation from daemon
                    if last_report_ago is None or last_report_ago > 600:
                        reconcile_sig = {
                            "action": "RECONCILE",
                            "signal_id": pos["signal_id"],
                            "ticket": DAEMONS[matched_did].get("active_ticket", 0),
                            "instance_id": instance_id,
                            "bridge_sl": pos["current_sl"],
                            "timestamp": now,
                        }
                        DAEMON_SL_QUEUE[matched_did].append(reconcile_sig)
                        log.info(f"🔍 Reconcile request → {instance_id} "
                                f"(last report {int((last_report_ago or 999)/60)}min ago)")
        except Exception as e:
            log.error(f"Reconciliation loop error: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vilona Signal Bridge V2")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    # Load .env for Tripay credentials
    env_path = os.path.join(PROJECT_DIR, "strategies", "vilona_tradefx", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v)

    # ── Initialize SQLite license DB (Phase 2) ──
    init_licenses_db()

    config = load_keys()
    log.info(f"Bridge V2 listening on {args.host}:{args.port}")
    log.info(f"  API keys loaded: {len(config['keys'])} | tiers: {list(config['tiers'].keys())} (SQLite)")
    log.info(f"  EA poll:     GET  /signal?api_key=VT-xxx&account_id=MT5-12345")
    log.info(f"  Bot signal:  POST /signal?api_key=VT-xxx  (broadcasts to instances of that key)")
    log.info(f"  Admin keys:  GET  /admin/keys (?admin_secret= or localhost)")
    log.info(f"  Create key:  POST /admin/keys")
    log.info(f"  Revoke key:  POST /admin/keys/revoke")
    log.info(f"  EA download: GET  /download/ea or /ea/download")
    log.info(f"  Accounts:    GET  /accounts (instance-level detail)")
    log.info(f"  Trailing:    GET/POST /trailing?api_key=VT-xxx&account_id=MT5-12345")

    # ── Trailing config persistence ──
    def _save_trail_config():
        try:
            os.makedirs(os.path.dirname(TRAIL_CONFIG_FILE), exist_ok=True)
            serializable = {k: dict(v) for k, v in TRAIL_CONFIG.items()}
            with open(TRAIL_CONFIG_FILE, 'w') as f:
                json.dump(serializable, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save trail config: {e}")

    def _load_trail_config():
        try:
            if os.path.exists(TRAIL_CONFIG_FILE):
                with open(TRAIL_CONFIG_FILE) as f:
                    data = json.load(f)
                    for k, v in data.items():
                        for key, val in v.items():
                            TRAIL_CONFIG[k][key] = val
                log.info(f"📂 Loaded trailing config: {len(data)} instance(s)")
        except Exception as e:
            log.error(f"Failed to load trail config: {e}")

    _load_trail_config()

    def trail_pip_size(cfg):
        """Return pip size in price units for this broker's digits setting."""
        digits = cfg.get("digits", 2)
        return 0.10 if digits == 2 else 0.01  # pips for XAUUSD: 2-digit=0.10, 3-digit=0.01

    def trailing_engine():
        """Background thread: monitor XAUUSD price and trail SL for active positions."""
        log.info("🎯 Trailing engine started (10s cycle, broker-aware)")
        while True:
            time.sleep(10)
            try:
                # Fetch live XAUUSD price
                req = urllib.request.Request(
                    "https://api.gold-api.com/price/XAU",
                    headers={"User-Agent": "VilonaTrailing/1.0"}
                )
                resp = urllib.request.urlopen(req, timeout=8)
                price_data = json.loads(resp.read())
                bid = price_data.get("price", 0)
                if not bid or bid < 1000:
                    continue

                with LOCK:
                    for instance_id, pos in list(TRAILED_POSITIONS.items()):
                        cfg = TRAIL_CONFIG[instance_id]
                        if not cfg["enabled"]:
                            continue

                        entry = pos["entry"]
                        current_sl = pos["current_sl"]
                        direction = pos["direction"]
                        tp = pos["tp"]

                        pip_sz = trail_pip_size(cfg)
                        trail_dist = cfg["trail_pips"] * pip_sz
                        breakeven_dist = (cfg["breakeven_pips"] + cfg.get("spread_buffer", 0)) * pip_sz
                        step_dist = cfg["step_pips"] * pip_sz

                        if direction == "BUY":
                            profit_pips = (bid - entry) / pip_sz if pip_sz > 0 else 0
                            new_sl = bid - trail_dist
                            breakeven_price = entry + cfg.get("spread_buffer", 0) * pip_sz
                            breakeven_hit = profit_pips >= (cfg["breakeven_pips"] + cfg.get("spread_buffer", 0))
                        else:  # SELL
                            profit_pips = (entry - bid) / pip_sz if pip_sz > 0 else 0
                            new_sl = bid + trail_dist
                            breakeven_price = entry - cfg.get("spread_buffer", 0) * pip_sz
                            breakeven_hit = profit_pips >= (cfg["breakeven_pips"] + cfg.get("spread_buffer", 0))

                        sl_improvement = 0
                        if direction == "BUY" and new_sl > current_sl:
                            sl_improvement = (new_sl - current_sl) / pip_sz if pip_sz > 0 else 0
                        elif direction == "SELL" and new_sl < current_sl:
                            sl_improvement = (current_sl - new_sl) / pip_sz if pip_sz > 0 else 0

                        # Only update if breakeven hit AND SL improved by step_pips
                        if breakeven_hit and sl_improvement >= cfg["step_pips"]:
                            # Don't trail past TP
                            if (direction == "BUY" and new_sl >= tp) or (direction == "SELL" and new_sl <= tp):
                                continue

                            # Move SL to breakeven on first hit
                            if (direction == "BUY" and current_sl < breakeven_price) or \
                               (direction == "SELL" and current_sl > breakeven_price):
                                target_sl = round(breakeven_price, cfg["digits"])
                            else:
                                target_sl = round(new_sl, cfg["digits"])

                            pos["current_sl"] = target_sl
                            cfg_summary = f"{cfg['account_type']}|{cfg['digits']}d|pip={pip_sz}"
                            log.info(f"🎯 TRAIL: {instance_id} [{cfg_summary}] | {direction} | "
                                    f"SL {current_sl:.{cfg['digits']}f}→{target_sl:.{cfg['digits']}f} | "
                                    f"profit={profit_pips:.1f}pip")

                            # Push trailing update to daemon queues AND instance queue
                            trail_sig = {
                                "signal_id": pos["signal_id"],
                                "symbol": "XAUUSD",
                                "action": direction,
                                "entry": entry,
                                "sl": target_sl,
                                "tp": tp,
                                "tp1": tp, "tp2": 0,
                                "risk_percent": 0,
                                "confidence": 100,
                                "rr_ratio": 0,
                                "comment": f"TRAIL|{cfg['account_type']}|be={profit_pips:.0f}pip",
                                "source": "trailing_engine",
                                "timestamp": time.time(),
                                "status": "trailing",
                                "layers": [],
                                "_for_instance": instance_id,
                            }
                            # Route to daemon queues (active daemons get priority)
                            for did, d in list(DAEMONS.items()):
                                d_inst = f"{d.get('api_key', '')}:{d.get('account_id', '')}"
                                if d_inst == instance_id or not d_inst.split(":")[0]:
                                    DAEMON_SL_QUEUE[did].append(trail_sig)
                            # Also keep in PENDING_BY_INSTANCE for EA fallback
                            PENDING_BY_INSTANCE[instance_id].append(trail_sig)

                # Cleanup orphaned positions (instance gone > 1 hour)
                now = time.time()
                orphaned = [iid for iid in TRAILED_POSITIONS
                           if now - TRAILED_POSITIONS[iid]["timestamp"] > 3600]
                for iid in orphaned:
                    del TRAILED_POSITIONS[iid]

            except Exception as e:
                log.error(f"Trailing engine error: {e}")

    trail_thread = threading.Thread(target=trailing_engine, daemon=True)
    trail_thread.start()
    log.info(f"  Instances:   {len(INSTANCES)} active | Master keys: {len(MASTER_INSTANCES)}")

    def cleanup_stale_instances():
        """Remove instances not seen in 30 minutes."""
        while True:
            time.sleep(300)  # every 5 min
            now = time.time()
            with LOCK:
                stale = [iid for iid, inst in INSTANCES.items() if now - inst["last_seen"] > 1800]
                for iid in stale:
                    api_key = iid.split(":", 1)[0]
                    acct_id = iid.split(":", 1)[1] if ":" in iid else ""
                    del INSTANCES[iid]
                    if api_key in MASTER_INSTANCES and acct_id in MASTER_INSTANCES[api_key]:
                        del MASTER_INSTANCES[api_key][acct_id]
                        if not MASTER_INSTANCES[api_key]:
                            del MASTER_INSTANCES[api_key]
                    if iid in PENDING_BY_INSTANCE:
                        del PENDING_BY_INSTANCE[iid]
                if stale:
                    log.info(f"🧹 Cleaned {len(stale)} stale instance(s)")
            # Also clean stale CONNECTED_ACCOUNTS
            with LOCK:
                stale_keys = [k for k, acc in CONNECTED_ACCOUNTS.items() if now - acc["last_seen"] > 1800]
                for k in stale_keys:
                    del CONNECTED_ACCOUNTS[k]
                    if k in PENDING_BY_KEY:
                        del PENDING_BY_KEY[k]
            # Clean expired dedup cache entries
            with LOCK:
                expired = [h for h, ts in _signal_dedup_cache.items() if now - ts > SIGNAL_DEDUP_TTL]
                for h in expired:
                    del _signal_dedup_cache[h]

    cleanup_thread = threading.Thread(target=cleanup_stale_instances, daemon=True)
    cleanup_thread.start()

    telegram_thread = threading.Thread(target=telegram_worker, daemon=True, name="telegram-worker")
    telegram_thread.start()

    reconcile_thread = threading.Thread(target=reconciliation_loop, daemon=True, name="reconcile-loop")
    reconcile_thread.start()

    server = ThreadingHTTPServer((args.host, args.port), SignalHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()
