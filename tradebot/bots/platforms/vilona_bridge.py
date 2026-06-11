"""Vilona Signal Bridge V2 — Multi-User Commercial Edition.

Extracted and cleaned from bots/vilona-bot/signal_bridge.py.
Provides HTTP-based signal queue for MT5 EA polling.

Uses tradebot.brokers for signal dispatch patterns
and tradebot.config.settings for all configuration.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import string
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tradebot.config import settings

LOG = logging.getLogger("tradebot.bots.vilona.signal_bridge")


# ── Global state ──────────────────────────────────────────────────────────
HISTORY: deque[dict[str, Any]] = deque(maxlen=500)
PENDING: deque[dict[str, Any]] = deque(maxlen=100)
PENDING_BY_KEY: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
PENDING_BY_INSTANCE: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=50))
LOCK = threading.Lock()
ID_COUNTER = 0
ACKED: set[str] = set()
ACKED_BY_KEY: dict[str, set[str]] = defaultdict(set)
START_TIME = time.time()

# Instance tracking
INSTANCES: dict[str, dict[str, Any]] = {}
MASTER_INSTANCES: dict[str, dict[str, str]] = defaultdict(dict)
RATE_COUNTERS: dict[str, list[float]] = defaultdict(list)
CONNECTED_ACCOUNTS: dict[str, dict[str, Any]] = {}


# ── Key management ────────────────────────────────────────────────────────

def _default_keys_path() -> Path:
    return Path(settings.DATA_DIR) / "api_keys.json"


def load_keys(path: Path | None = None) -> dict[str, Any]:
    p = path or _default_keys_path()
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:
        LOG.error("Failed to load API keys: %s", e)
        return {"keys": {}, "tiers": {}, "default_tier": "starter"}


def save_keys(config: dict[str, Any], path: Path | None = None) -> None:
    p = path or _default_keys_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(config, f, indent=2)


def gen_id() -> str:
    global ID_COUNTER
    ID_COUNTER += 1
    return f"vtfx_{int(time.time() * 1000)}_{ID_COUNTER}"


def validate_key(api_key: str) -> tuple[bool, dict[str, Any] | None]:
    """Returns (valid, tier_info)."""
    if not api_key:
        return False, None
    config = load_keys()
    key_data = config["keys"].get(api_key)
    if not key_data or not key_data.get("active"):
        return False, None
    starter = config["tiers"].get("starter", {"max_layers": 1, "features": []})
    return True, config["tiers"].get(key_data.get("tier", ""), starter)


def check_rate_limit(api_key: str) -> bool:
    """True if request is within rate limit. rate_limit=0 means unlimited."""
    config = load_keys()
    key_data = config["keys"].get(api_key, {})
    limit = key_data.get("rate_limit", 3)
    if limit == 0:
        return True
    window = key_data.get("rate_window_seconds", 86400)
    now = time.time()
    timestamps = RATE_COUNTERS[api_key]
    timestamps[:] = [t for t in timestamps if now - t < window]
    if len(timestamps) >= limit:
        return False
    timestamps.append(now)
    return True


# ── HTTP Handler ──────────────────────────────────────────────────────────

class SignalHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the signal bridge."""

    def _json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _get_params(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return parsed.path.rstrip("/"), qs

    def _poll_signal(
        self, api_key: str, tier: dict[str, Any] | None = None, instance_id: str | None = None
    ) -> dict[str, Any]:
        with LOCK:
            if instance_id and instance_id in PENDING_BY_INSTANCE and PENDING_BY_INSTANCE[instance_id]:  # noqa: E501
                sig = PENDING_BY_INSTANCE[instance_id].popleft()
                LOG.info("Signal delivered (instance): %s → %s", sig["signal_id"], instance_id)
                return self._format_signal(sig)
            if api_key in PENDING_BY_KEY and PENDING_BY_KEY[api_key]:
                sig = PENDING_BY_KEY[api_key].popleft()
                LOG.info("Signal delivered (user): %s → %s", sig["signal_id"], api_key)
                return self._format_signal(sig)
            if not PENDING:
                return self._empty_signal()
            sig = PENDING.popleft()
        LOG.info("Signal delivered (global): %s → %s", sig["signal_id"], api_key)
        return self._format_signal(sig)

    def _format_signal(self, sig: dict[str, Any]) -> dict[str, Any]:
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

    @staticmethod
    def _empty_signal() -> dict[str, Any]:
        return {
            "signal_id": "", "symbol": "", "action": "HOLD",
            "entry": 0, "sl": 0, "tp": 0,
            "tp1": 0, "tp2": 0, "tp3": 0, "tp4": 0,
            "risk_percent": 0, "comment": "", "confidence": 0,
            "layers": [], "layer_count": 0, "tier": "", "pending": False,
        }

    def do_GET(self) -> None:
        path, params = self._get_params()
        api_key = params.get("api_key", [""])[0]

        if path == "/health":
            self._json({
                "status": "ok",
                "uptime_seconds": int(time.time() - START_TIME),
                "queue_size": len(PENDING),
            })
        elif path in ("", "/"):
            self._json({"service": "Vilona Signal Bridge V2", "status": "running"})
        elif path == "/status":
            with LOCK:
                self._json({
                    "pending": len(PENDING) > 0,
                    "pending_id": PENDING[0]["signal_id"] if PENDING else None,
                    "history_count": len(HISTORY),
                    "last_signal_id": HISTORY[-1]["signal_id"] if HISTORY else None,
                })
        elif path in ("/signal", "/signal/pending"):
            is_valid, tier = validate_key(api_key)
            if not is_valid:
                self._json({"error": "invalid_api_key"}, 401)
                return
            if not check_rate_limit(api_key):
                self._json({"error": "rate_limited", "action": "HOLD", "pending": False}, 429)
                return
            self._current_key = api_key
            account_id = params.get("account_id", [None])[0]
            if account_id:
                instance_id = f"{api_key}:{account_id}"
                INSTANCES[instance_id] = {
                    "last_seen": time.time(),
                    "ip": self.client_address[0],
                    "signals_polled": INSTANCES.get(instance_id, {}).get("signals_polled", 0) + 1,
                    "first_seen": INSTANCES.get(instance_id, {}).get("first_seen", time.time()),
                    "label": INSTANCES.get(instance_id, {}).get("label", account_id),
                    "api_key": api_key,
                    "account_id": account_id,
                }
                MASTER_INSTANCES[api_key][account_id] = instance_id
                result = self._poll_signal(api_key, tier, instance_id=instance_id)
            else:
                CONNECTED_ACCOUNTS[api_key] = {
                    "last_seen": time.time(),
                    "ip": self.client_address[0],
                    "signals_polled": CONNECTED_ACCOUNTS.get(api_key, {}).get("signals_polled", 0) + 1,  # noqa: E501
                    "first_seen": CONNECTED_ACCOUNTS.get(api_key, {}).get("first_seen", time.time()),  # noqa: E501
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
            LOG.info("EA ack: %s | key=%s", signal_id, api_key)
            self._json({"status": "ok", "signal_id": signal_id})
        elif path in ("/keys", "/admin/keys"):
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
        elif path == "/accounts":
            self._json(self._build_accounts_response())
        elif path in ("/download/ea", "/download/ea.ex5", "/ea/download"):
            # ── DONOR GATE: require valid API key with pro/elite tier ──
            is_valid, tier_info = validate_key(api_key)
            if not is_valid:
                self._json({"error": "donor only — valid API key required"}, 403)
                return
            config = load_keys()
            key_data = config["keys"].get(api_key, {})
            if key_data.get("tier", "starter") == "starter":
                self._json({"error": "donor only — upgrade to access EA download"}, 403)
                return
            ea_path = Path(settings.DATA_DIR).parent / "ea" / "VilonaTradeFX_EA.ex5"
            try:
                content = ea_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename=VilonaTradeFX_EA.ex5")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._json({"error": "file not found"}, 404)
        elif path == "/api/trade-log":
            log_path = Path(settings.DATA_DIR) / "trade_log.json"
            try:
                with open(log_path) as f:
                    self._json(json.load(f))
            except (FileNotFoundError, json.JSONDecodeError):
                self._json([])
        elif path == "/api/dash-stats":
            stats: dict[str, Any] = {
                "total": 0, "win_rate": 0, "total_profit": 0,
                "trades": [], "uptime": "", "ea_count": 0,
            }
            try:
                _scripts = str(Path(settings.DATA_DIR).parent / "scripts")
                if _scripts not in sys.path:
                    sys.path.insert(0, _scripts)
                from tradebot.services.trade_tracker_service import (
                    get_recent_trades,
                    get_stats,
                )
                s = get_stats()
                stats.update(s)
                stats["trades"] = get_recent_trades(10)
                stats["total_profit"] = s.get("total_profit_usd", 0)
            except Exception:
                pass
            try:
                req = urllib.request.Request(
                    "https://api.gold-api.com/price/XAU",
                    headers={"User-Agent": "VilonaBridge/1.0"},
                )
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
                stats["ea_count"] = len(
                    [i for i in INSTANCES.values() if time.time() - i["last_seen"] < 120]
                )
            self._json(stats)
        elif path == "/api/engine-readings":
            eng_path = Path(settings.DATA_DIR).parent / "bridges" / "signal_bridge" / "engine_status.json"  # noqa: E501
            try:
                cached = json.loads(eng_path.read_text())
                if time.time() - eng_path.stat().st_mtime < 120:
                    self._json(cached)
                    return
            except Exception:
                pass
            try:
                _scripts = str(Path(settings.DATA_DIR).parent / "scripts")
                if _scripts not in sys.path:
                    sys.path.insert(0, _scripts)
                from tradebot.services.consensus_service import run_engine_consensus
                result = run_engine_consensus(symbol="XAUUSD")
                dashboard_output: dict[str, Any] = {
                    "symbol": result.get("symbol", "XAUUSD"),
                    "price": result.get("price", 0),
                    "timestamp": result.get("timestamp", ""),
                    "timeframes": {},
                    "hierarchical": result.get("hierarchical", {}),
                    "mtf_alignment": result.get("mtf_alignment", "NONE"),
                    "macro_trend": result.get("macro_trend", "NEUTRAL"),
                    "counter_trend_flags": result.get("counter_trend_flags", []),
                }
                from tradebot.services.consensus_service import get_tf_weights, get_timeframes
                _tf_weights = get_tf_weights()
                _timeframes = get_timeframes()
                for tf in _timeframes:
                    tr = result.get("timeframes", {}).get(tf, {})
                    if tr:
                        dashboard_output["timeframes"][tf] = {
                            "verdict": tr["verdict"],
                            "consensus_pct": tr["consensus_pct"],
                            "buy_count": tr["buy_count"],
                            "sell_count": tr["sell_count"],
                            "total": tr["total"],
                            "engines": tr.get("engines", {}),
                            "weight": _tf_weights.get(tf, 0),
                        }
                        for _k in ("macro", "structure", "entry"):
                            if _k in tr:
                                dashboard_output["timeframes"][tf][_k] = tr[_k]
                try:
                    eng_path.parent.mkdir(parents=True, exist_ok=True)
                    eng_path.write_text(json.dumps(dashboard_output, indent=2))
                except Exception:
                    pass
                active_tf = dashboard_output["timeframes"].get("M15", {})
                dashboard_output["engines"] = active_tf.get("engines", {})
                dashboard_output["verdict"] = dashboard_output["hierarchical"].get("verdict", "HOLD")  # noqa: E501
                dashboard_output["consensus_pct"] = dashboard_output["hierarchical"].get("consensus_score", 0)  # noqa: E501
                dashboard_output["buy_count"] = active_tf.get("buy_count", 0)
                dashboard_output["sell_count"] = active_tf.get("sell_count", 0)
                dashboard_output["total"] = active_tf.get("total", 0)
                self._json(dashboard_output)
            except Exception as e:
                LOG.error("/api/engine-readings error: %s", e)
                self._json({"error": str(e), "engines": {}, "verdict": "N/A", "timeframes": {}})
        elif path == "/api/news":
            try:
                items: list[dict[str, str]] = []
                req = urllib.request.Request(
                    "https://finance.yahoo.com/rss/headline?s=GC=F",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    root = ET.fromstring(resp.read())
                    for item in root.iter("item"):
                        title = item.findtext("title", "")
                        link = item.findtext("link", "")
                        pubdate = item.findtext("pubDate", "")
                        if title:
                            items.append({"title": title, "link": link, "date": pubdate})
                self._json({"items": items[:12]})
            except Exception as e:
                LOG.error("News fetch error: %s", e)
                self._json({"items": [], "error": str(e)})
        elif path == "/api/config":
            # ── LOCALHOST ONLY ──
            if self.client_address[0] not in ("127.0.0.1", "::1", "localhost"):
                self._json({"error": "admin only"}, 403)
                return
            self._json({"api_key": "VT-MASTER-734AD731F5FB"})
        elif path == "/api/donations":
            try:
                _root = str(Path(settings.DATA_DIR).parent)
                if _root not in sys.path:
                    sys.path.insert(0, _root)
                from tradebot.services.members_service import get_total_donations
                self._json({"total_raised": get_total_donations(), "currency": "IDR"})
            except Exception as e:
                LOG.error("/api/donations error: %s", e)
                self._json({"total_raised": 0, "error": str(e)})

        else:
            self._json({"error": "not found"}, 404)

    def _build_accounts_response(self) -> dict[str, Any]:
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
        master_keys = {}
        for inst_id in sorted(INSTANCES.keys()):
            api_key = inst_id.split(":", 1)[0]
            if api_key not in master_keys:
                master_keys[api_key] = {"instance_ids": [], "instance_count": 0}
            master_keys[api_key]["instance_ids"].append(inst_id)
            master_keys[api_key]["instance_count"] += 1
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
        return {
            "total_instances": len(INSTANCES),
            "instances": instances_data,
            "master_keys_count": len(master_keys),
            "master_keys": dict(master_keys),
            "legacy_accounts": legacy_accounts,
            "bridge_uptime_sec": int(now - START_TIME),
            "mode": "instance_broadcast",
        }

    def do_POST(self) -> None:
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
            signal: dict[str, Any] = {
                "signal_id": sig_id,
                "symbol": data.get("symbol", "XAUUSD"),
                "action": data.get("action", "HOLD"),
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
                "target_user": data.get("target_user"),
            }

            broadcast_count = self._broadcast_signal(signal, api_key)
            layers_count = len(signal["layers"]) if isinstance(signal.get("layers"), list) else 0
            LOG.info(
                "Signal: %s | %s %s | layers=%d | broadcast→%d",
                sig_id, signal["symbol"], signal["action"], layers_count, broadcast_count,
            )

            with LOCK:
                HISTORY.append(signal)

            self._json({
                "signal_id": sig_id,
                "status": "queued",
                "broadcast_count": broadcast_count,
                "mode": "broadcast" if broadcast_count > 0 else "queued",
            })
        elif path.startswith("/ack/"):
            signal_id = path.split("/ack/", 1)[1]
            with LOCK:
                ACKED.add(signal_id)
                if api_key:
                    ACKED_BY_KEY[api_key].add(signal_id)
            LOG.info("EA ack: %s | key=%s", signal_id, api_key)
            self._json({"status": "ok", "signal_id": signal_id})
        elif path.startswith("/webhook/"):
            self._forward_webhook(path)
        elif path == "/admin/generate-key":
            if self.client_address[0] not in ("127.0.0.1", "::1", "localhost"):
                self._json({"error": "admin only"}, 403)
                return
            self._handle_generate_key()
        elif path == "/api/create-payment":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            plan = data.get("plan", "video-course")
            customer_name = data.get("customer_name", "Student")
            customer_email = data.get("customer_email", "student@example.com")
            customer_phone = data.get("customer_phone", "")
            method = data.get("method", "QRIS")

            PRICES = {  # noqa: N806
                "video-course": 299000, "online-live": 799000,
                "offline-workshop": 2500000, "monthly-sub": 199000,
                "platinum-pass": 9000000,
            }
            NAMES = {  # noqa: N806
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
            merchant_ref = f"BLJ-BRIDGE-{int(time.time() * 1000)}"

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
                "callback_url": os.environ.get(
                    "TRIPAY_CALLBACK_URL", "https://phantomfx.aitradepulse.com/webhook/tripay"
                ),
                "return_url": "https://berkahkarya.org/id/belajarai",
                "expired_time": int(time.time()) + 86400,
                "signature": sig,
            })

            try:
                req = urllib.request.Request(
                    "https://tripay.co.id/api/transaction/create",
                    data=tripay_payload.encode(),
                    headers={
                        "Authorization": f"Bearer {ak}",
                        "Content-Type": "application/json",
                    },
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
                self._json({"error": f"Payment error: {e}"}, 500)

        else:
            self._json({"error": "not found"}, 404)

    def _broadcast_signal(self, signal: dict[str, Any], api_key: str) -> int:
        """Broadcast signal to appropriate queues. Returns count of targets."""
        broadcast_count = 0
        with LOCK:
            target = signal.get("target_user")
            broadcast_api_key = api_key if api_key else None

            if target:
                PENDING_BY_KEY[target].append(signal)
                broadcast_count = 1
            elif broadcast_api_key and broadcast_api_key in MASTER_INSTANCES and MASTER_INSTANCES[broadcast_api_key]:  # noqa: E501
                for acct_id in list(MASTER_INSTANCES[broadcast_api_key].keys()):
                    instance_id = MASTER_INSTANCES[broadcast_api_key][acct_id]
                    acct_signal = dict(signal)
                    acct_signal["_for_instance"] = instance_id
                    PENDING_BY_INSTANCE[instance_id].append(acct_signal)
                    broadcast_count += 1
                # Juga masukin ke global queue buat legacy polling (tanpa account_id)
                PENDING.append(dict(signal))
                LOG.info("Instance broadcast (%s): %d instance(s) + global fallback", broadcast_api_key, broadcast_count)  # noqa: E501
            elif broadcast_api_key:
                PENDING.append(signal)
                LOG.info("No instances for %s, queued global", broadcast_api_key)
            else:
                for mk in list(MASTER_INSTANCES.keys()):
                    for acct_id in list(MASTER_INSTANCES[mk].keys()):
                        instance_id = MASTER_INSTANCES[mk][acct_id]
                        acct_signal = dict(signal)
                        acct_signal["_for_instance"] = instance_id
                        PENDING_BY_INSTANCE[instance_id].append(acct_signal)
                        broadcast_count += 1
                if broadcast_count == 0:
                    for key in list(CONNECTED_ACCOUNTS.keys()):
                        acct_signal = dict(signal)
                        acct_signal["_for_account"] = key
                        PENDING_BY_KEY[key].append(acct_signal)
                        broadcast_count += 1
                if broadcast_count == 0:
                    PENDING.append(signal)
                LOG.info("Broadcast to %d instance(s)", broadcast_count)
        return broadcast_count

    def _handle_generate_key(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return

        tier = data.get("tier", "starter")
        prefix = {"starter": "VT-FREE", "pro": "VT-PRO", "elite": "VT-ELITE"}.get(tier, "VT-FREE")
        suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
        new_key = f"{prefix}-{suffix}"

        config = load_keys()
        config["keys"][new_key] = {
            "tier": tier,
            "label": data.get("label", f"Generated {tier}"),
            "rate_limit": data.get("rate_limit", {"starter": 3, "pro": 50, "elite": 200}.get(tier, 3)),  # noqa: E501
            "rate_window_seconds": 86400,
            "expires": data.get("expires", "2026-12-31"),
            "active": True,
            "features": config["tiers"].get(tier, {}).get("features", []),
        }
        save_keys(config)
        LOG.info("New key generated: %s (%s)", new_key, tier)
        self._json({"api_key": new_key, "tier": tier, "status": "created"})

    def _forward_webhook(self, path: str) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            import urllib.request as ur
            webhook_url = f"http://127.0.0.1:8787{path}"
            req = ur.Request(
                webhook_url,
                data=body or None,
                headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
            )
            resp = ur.urlopen(req, timeout=30)
            result = resp.read()
            self._json(json.loads(result) if result else {"status": "forwarded"})
        except Exception as e:
            LOG.error("Webhook forward failed: %s", e)
            self._json({"error": "webhook_failed", "detail": str(e)}, 500)

    def log_message(self, format: str, *args: Any) -> None:
        LOG.debug(format % args)


# ── Background cleanup ────────────────────────────────────────────────────

def _cleanup_stale_instances() -> None:
    """Remove instances not seen in 30 minutes."""
    while True:
        time.sleep(300)
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
                LOG.info("Cleaned %d stale instance(s)", len(stale))
            stale_keys = [k for k, acc in CONNECTED_ACCOUNTS.items() if now - acc["last_seen"] > 1800]  # noqa: E501
            for k in stale_keys:
                del CONNECTED_ACCOUNTS[k]
                if k in PENDING_BY_KEY:
                    del PENDING_BY_KEY[k]


# ── BridgeServer (managed wrapper) ─────────────────────────────────────────

class BridgeServer:
    """Managed wrapper around the HTTP signal bridge server."""

    def __init__(self, host: str = "", port: int = 0) -> None:
        self._host = host or settings.BRIDGE_HOST or "0.0.0.0"
        self._port = port or 8765
        self._server: HTTPServer | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            LOG.warning("BridgeServer already running")
            return
        self._server = HTTPServer((self._host, self._port), SignalHandler)
        self._cleanup_thread = threading.Thread(target=_cleanup_stale_instances, daemon=True)
        self._cleanup_thread.start()
        self._running = True
        LOG.info("Vilona Signal Bridge listening on %s:%d", self._host, self._port)

        config = load_keys()
        LOG.info("  API keys loaded: %d | tiers: %s", len(config["keys"]), list(config["tiers"].keys()))  # noqa: E501

        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.shutdown()
            LOG.info("BridgeServer stopped")

    @property
    def is_running(self) -> bool:
        return self._running


# ── VilonaSignalBridge (programmatic client) ──────────────────────────────

class VilonaSignalBridge:
    """Client for posting signals to the bridge from within the bot."""

    def __init__(self, bridge_urls: list[str] | None = None) -> None:
        self.bridge_urls = bridge_urls or [
            "https://phantomfx.aitradepulse.com",
            "http://localhost:8765",
        ]

    def post_signal(self, sig: dict[str, Any], price: float) -> bool:
        """Post a signal to the bridge. Returns True on success."""
        symbol = sig.get("symbol", sig.get("display", "XAUUSD"))
        payload = {
            "action": sig.get("action", "HOLD"),
            "symbol": symbol,
            "entry": sig.get("entry", price),
            "sl": sig.get("sl", 0),
            "tp": sig.get("tp", 0),
            "tp1": sig.get("tp1", sig.get("tp", 0)),
            "tp2": sig.get("tp2", 0),
            "confidence": sig.get("confidence", 0),
            "risk_percent": sig.get("risk_percent", 1.0),
            "comment": sig.get("comment", f"VTFX/{sig.get('source', 'vilona-tradefx')}"),
            "source": sig.get("source", "vilona-tradefx"),
            "layers": sig.get("layers", []),
            "target_user": sig.get("target_user", ""),
            "timestamp": sig.get("timestamp", ""),
        }
        data = json.dumps(payload).encode()
        import urllib.request as ur
        for url in self.bridge_urls:
            try:
                req = ur.Request(
                    f"{url}/signal",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                ur.urlopen(req, timeout=5)
                return True
            except Exception:
                continue
        return False
