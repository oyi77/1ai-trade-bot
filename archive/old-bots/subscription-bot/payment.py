"""Tripay payment integration for Stockity Subscription Bot.

Reuses the existing Tripay module from scripts/payment_tripay.py.
Handles: create invoice, verify callback, check status, webhook server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from collections.abc import Callable
from typing import Optional

LOG = logging.getLogger("subscription_bot.payment")

# ── Load Tripay env from vilona_tradefx ───────────────────────────────
_env_loaded = False
for _env_path in [
    Path(__file__).resolve().parent.parent / "strategies" / "vilona_tradefx" / ".env",
    Path(__file__).resolve().parent.parent.parent / "strategies" / "vilona_tradefx" / ".env",
]:
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
        LOG.info("Loaded Tripay env from %s", _env_path)
        break

# ── Tripay config ────────────────────────────────────────────────────────

TRIPAY_MERCHANT_CODE = os.environ.get("TRIPAY_MERCHANT_CODE", "T23409")
TRIPAY_API_KEY       = os.environ.get("TRIPAY_API_KEY", "")
TRIPAY_PRIVATE_KEY   = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TRIPAY_BASE_URL      = os.environ.get("TRIPAY_BASE_URL", "https://tripay.co.id/api")
TRIPAY_CALLBACK_URL  = os.environ.get("TRIPAY_CALLBACK_URL",
                         "https://phantomfx.aitradepulse.com/webhook/tripay")
DEFAULT_METHOD       = os.environ.get("TRIPAY_DEFAULT_METHOD", "QRIS2")

WEBHOOK_PORT = int(os.environ.get("SUBSCRIPTION_WEBHOOK_PORT", "8788"))

# Callback: dipanggil webhook server saat pembayaran sukses
# Di-set sama bot.py setelah init
_on_payment_success: Optional[Callable] = None


def set_payment_handler(handler: Callable):
    """Set callback for when Tripay confirms payment."""
    global _on_payment_success
    _on_payment_success = handler


# ── HMAC ────────────────────────────────────────────────────────────────

def _sign(payload: str) -> str:
    return hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_callback(body: bytes, callback_sig: str) -> bool:
    """Verify Tripay webhook HMAC-SHA256 signature."""
    if not TRIPAY_PRIVATE_KEY:
        LOG.warning("TRIPAY_PRIVATE_KEY not set — skipping signature check")
        return True
    expected = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(), body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, callback_sig)


# ── Create payment ──────────────────────────────────────────────────────

def create_invoice(
    user_id: int,
    username: str,
    plan: str,
    amount: int,
    method: str = None,
) -> dict:
    """Create Tripay invoice. Returns Tripay API response."""
    method = method or DEFAULT_METHOD
    merchant_ref = f"SUB-{user_id}-{plan.upper()}-{int(time.time())}"

    payload = {
        "method": method,
        "merchant_ref": merchant_ref,
        "amount": amount,
        "customer_name": username or f"User{user_id}",
        "customer_email": f"{user_id}@telegram.user",
        "customer_phone": "08123456789",
        "order_items": [
            {
                "name": f"Stockity Signal {plan.capitalize()}",
                "price": amount,
                "quantity": 1,
            }
        ],
        "callback_url": TRIPAY_CALLBACK_URL,
        "return_url": "https://t.me/agent_1ai2_bot",
        "expired_time": int(time.time()) + 3600,  # 1 jam
        "signature": "",
    }

    # Signature: HMAC-SHA256(merchant_code + merchant_ref + amount)
    raw = f"{TRIPAY_MERCHANT_CODE}{merchant_ref}{amount}"
    payload["signature"] = _sign(raw)

    url = f"{TRIPAY_BASE_URL}/transaction/create"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {TRIPAY_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        LOG.info("Tripay invoice created: ref=%s amount=%d", merchant_ref, amount)
        return result
    except Exception as e:
        LOG.error("Tripay create invoice failed: %s", e)
        return {"success": False, "error": str(e)}


# ── Check payment status ────────────────────────────────────────────────

def check_status(merchant_ref: str) -> dict:
    """Check payment status via Tripay API."""
    payload = {"merchant_ref": merchant_ref}
    payload["signature"] = _sign(merchant_ref + TRIPAY_MERCHANT_CODE)

    url = f"{TRIPAY_BASE_URL}/transaction/detail"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {TRIPAY_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        LOG.warning("Tripay check_status failed for %s: %s", merchant_ref, e)
        return {"success": False, "error": str(e)}


def is_paid(merchant_ref: str) -> bool:
    """Returns True if payment is confirmed PAID/SUCCESS/SETTLEMENT."""
    result = check_status(merchant_ref)
    if result.get("success") and result.get("data"):
        status = result["data"].get("status", "").upper()
        return status in ("PAID", "SUCCESS", "SETTLEMENT")
    return False


# ── Webhook server ──────────────────────────────────────────────────────

class WebhookHandler(BaseHTTPRequestHandler):
    """Mini HTTP server for Tripay callbacks."""

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "ok", "service": "subscription-webhook"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b""

        if path in ("/webhook/tripay", "/tripay/callback"):
            self._handle_tripay(body)
        else:
            self._json({"error": "not found"}, 404)

    def _handle_tripay(self, body: bytes):
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return

        # Verify signature
        sig = self.headers.get("X-Callback-Signature", "")
        if sig and not verify_callback(body, sig):
            LOG.warning("Invalid Tripay signature: ref=%s", data.get("merchant_ref", "?"))
            self._json({"error": "invalid signature"}, 403)
            return

        merchant_ref = data.get("merchant_ref", "")
        status = data.get("status", "").upper()

        LOG.info("Tripay callback: ref=%s status=%s amount=%s",
                 merchant_ref, status, data.get("total_amount", "?"))

        if status not in ("PAID", "SUCCESS", "SETTLEMENT"):
            self._json({"status": "ignored", "reason": f"status={status}"})
            return

        # Extract user_id from ref: SUB-{user_id}-{PLAN}-{ts}
        user_id = 0
        plan = ""
        try:
            parts = merchant_ref.split("-")
            if len(parts) >= 3 and parts[0] == "SUB":
                user_id = int(parts[1])
                plan = parts[2].lower()
        except (ValueError, IndexError):
            pass

        if not user_id or not plan:
            LOG.error("Cannot parse merchant_ref: %s", merchant_ref)
            self._json({"error": "invalid merchant_ref"}, 400)
            return

        LOG.info("Payment confirmed: user=%d plan=%s ref=%s", user_id, plan, merchant_ref)

        # Panggil handler biar bot yg activate subscription
        if _on_payment_success:
            _on_payment_success(user_id, plan, merchant_ref)

        self._json({"status": "ok", "user_id": user_id, "plan": plan})

    def log_message(self, fmt, *args):
        LOG.info("%s — %s", self.client_address[0], fmt % args)


def start_webhook():
    """Start webhook server in a background thread."""
    import threading

    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    LOG.info("Tripay webhook listening on :%s", WEBHOOK_PORT)
    return server
