#!/usr/bin/env python3
"""Vilona Trade FX Signal Bridge V2 — Multi-User Commercial Edition.
- API key authentication + tier-based rate limiting
- Signal queue per user
- Compatible with VilonaTradeFX_EA.mq5 (Commercial)

Usage: python3 vilona_tradefx_signal_bridge.py --port 8765 --host 0.0.0.0
"""
import json, time, threading, argparse, logging, os
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import deque, defaultdict
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bridge")

# ── Config paths ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
KEYS_FILE = os.path.join(PROJECT_DIR, "api_keys.json")

# ── Global state ──
HISTORY = deque(maxlen=500)
PENDING = deque(maxlen=100)        # global pending queue (all users)
PENDING_BY_KEY = defaultdict(deque)  # per-user pending
LOCK = threading.Lock()
ID_COUNTER = 0
ACKED = set()
START_TIME = time.time()

# ── Rate limiting ──
RATE_COUNTERS = defaultdict(list)  # api_key → [timestamps]


def load_keys():
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Failed to load API keys: {e}")
        return {"keys": {}, "tiers": {}, "default_tier": "starter"}


def gen_id():
    global ID_COUNTER
    ID_COUNTER += 1
    return f"vtfx_{int(time.time()*1000)}_{ID_COUNTER}"


def validate_key(api_key):
    """Returns (valid, tier_info)"""
    if not api_key:
        return False, None
    config = load_keys()
    key_data = config["keys"].get(api_key)
    if not key_data or not key_data.get("active"):
        # Allow any key with default starter tier for MVP
        return True, config["tiers"]["starter"]
    return True, config["tiers"].get(key_data.get("tier"), config["tiers"]["starter"])


def check_rate_limit(api_key):
    """Returns True if request is within rate limit. rate_limit=0 means unlimited."""
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
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
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

    def _get_params(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return parsed.path.rstrip("/"), qs

    def _poll_signal(self, api_key, tier):
        """Poll signal for a specific user. Takes from per-user queue or global queue."""
        with LOCK:
            # First try per-user queue
            if api_key in PENDING_BY_KEY and PENDING_BY_KEY[api_key]:
                sig = PENDING_BY_KEY[api_key].popleft()
                log.info(f"Signal delivered (user): {sig['signal_id']} → {api_key}")
                return self._format_signal(sig)

            # Fallback to global queue
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
            "risk_percent": 0, "comment": "", "confidence": 0,
            "layers": [], "layer_count": 0, "tier": "", "pending": False,
        }

    def do_GET(self):
        path, params = self._get_params()
        api_key = params.get("api_key", [""])[0]

        if path == "/health" or path == "":
            self._json({
                "status": "ok",
                "uptime_seconds": int(time.time() - START_TIME),
                "queue_size": len(PENDING),
            })
        elif path == "/status":
            with LOCK:
                self._json({
                    "pending": len(PENDING) > 0,
                    "pending_id": PENDING[0]["signal_id"] if PENDING else None,
                    "history_count": len(HISTORY),
                    "last_signal_id": HISTORY[-1]["signal_id"] if HISTORY else None,
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
            result = self._poll_signal(api_key, tier)
            self._json(result)

        elif path.startswith("/ack/"):
            signal_id = path.split("/ack/", 1)[1]
            with LOCK:
                ACKED.add(signal_id)
            log.info(f"EA ack: {signal_id} | key={api_key}")
            self._json({"status": "ok", "signal_id": signal_id})

        elif path == "/keys" or path == "/admin/keys":
            # Admin: list keys (simple auth — localhost only)
            if self.client_address[0] not in ("127.0.0.1", "::1", "localhost"):
                self._json({"error": "admin only"}, 403)
                return
            config = load_keys()
            keys_safe = {
                k: {"tier": v["tier"], "label": v.get("label", ""),
                    "rate_limit": v.get("rate_limit", "?"), "active": v["active"]}
                for k, v in config["keys"].items()
            }
            self._json({"keys": keys_safe, "tiers": config["tiers"]})

        elif path == "/history":
            with LOCK:
                self._json({"count": len(HISTORY), "signals": list(HISTORY)})
        elif path == "/download/ea" or path == "/download/ea.ex5":
            # Serve EA file for download
            ea_path = os.path.join(PROJECT_DIR, "ea", "VilonaTradeFX_EA.ex5")
            try:
                with open(ea_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename=VilonaTradeFX_EA.ex5")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._json({"error": "file not found"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def _forward_webhook(self, path):
        """Forward webhook calls to payment_webhook on port 8787."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            import urllib.request as ur
            req = ur.Request(f"http://127.0.0.1:8787{path}",
                             data=body or None,
                             headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
            resp = ur.urlopen(req, timeout=30)
            result = resp.read()
            self._json(json.loads(result) if result else {"status": "forwarded"})
        except Exception as e:
            log.error(f"Webhook forward failed: {e}")
            self._json({"error": "webhook_failed", "detail": str(e)}, 500)

    def do_POST(self):
        path, params = self._get_params()
        api_key = params.get("api_key", [""])[0]

        if path == "/signal":
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
                "comment": data.get("comment", "VTFX/AI"),
                "source": data.get("source", "vtfx"),
                "timestamp": data.get("timestamp"),
                "received_at": time.time(),
                "status": "pending",
                "layers": data.get("layers", []),
                "target_user": data.get("target_user"),  # optional: deliver to specific user
            }

            with LOCK:
                HISTORY.append(signal)

                # Route to specific user if target_user set, AND always global queue
                target = signal.get("target_user") or api_key
                if target:
                    PENDING_BY_KEY[target].append(signal)
                # Always add to global queue as fallback
                PENDING.append(signal)

            layers_count = len(signal['layers']) if isinstance(signal.get('layers'), list) else 0
            log.info(f"Signal: {sig_id} | {symbol} {action} | layers={layers_count} | target={target or 'global'}")
            self._json({
                "signal_id": sig_id,
                "status": "queued",
                "pending_count": len(PENDING),
            })

        elif path.startswith("/ack/"):
            signal_id = path.split("/ack/", 1)[1]
            with LOCK:
                ACKED.add(signal_id)
            log.info(f"EA ack: {signal_id} | key={api_key}")
            self._json({"status": "ok", "signal_id": signal_id})

        elif path.startswith("/webhook/"):
            # Forward to payment webhook on port 8787
            self._forward_webhook(path)

        elif path == "/admin/generate-key":
            # Admin: generate new API key (localhost only)
            if self.client_address[0] not in ("127.0.0.1", "::1", "localhost"):
                self._json({"error": "admin only"}, 403)
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
            prefix = {"starter": "VT-FREE", "pro": "VT-PRO", "elite": "VT-ELITE"}.get(tier, "VT-FREE")
            suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            new_key = f"{prefix}-{suffix}"

            config = load_keys()
            config["keys"][new_key] = {
                "tier": tier,
                "label": data.get("label", f"Generated {tier}"),
                "rate_limit": data.get("rate_limit", {"starter": 3, "pro": 50, "elite": 200}.get(tier, 3)),
                "rate_window_seconds": 86400,
                "expires": data.get("expires", "2026-12-31"),
                "active": True,
                "features": config["tiers"].get(tier, {}).get("features", []),
            }

            with open(KEYS_FILE, "w") as f:
                json.dump(config, f, indent=2)

            log.info(f"New key generated: {new_key} ({tier})")
            self._json({"api_key": new_key, "tier": tier, "status": "created"})

        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        log.debug(format % args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vilona Signal Bridge V2")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    config = load_keys()
    log.info(f"Bridge V2 listening on {args.host}:{args.port}")
    log.info(f"  API keys loaded: {len(config['keys'])} | tiers: {list(config['tiers'].keys())}")
    log.info(f"  EA poll:     GET  /signal?api_key=VT-xxx")
    log.info(f"  Bot signal:  POST /signal")
    log.info(f"  Admin keys:  GET  /admin/keys (localhost only)")
    log.info(f"  Gen key:     POST /admin/generate-key (localhost only)")

    server = HTTPServer((args.host, args.port), SignalHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()
