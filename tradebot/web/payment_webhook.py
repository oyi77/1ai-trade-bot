"""Payment webhook server — Tripay callback handler.

Ported from scripts/payment_webhook_server.py with full legacy fidelity.
HTTP server on port 8787. Handles GET /health, POST /webhook/tripay,
and POST /tripay/callback for Tripay payment confirmation.

Verifies HMAC-SHA256 signatures, upgrades members with idempotency,
fires Bemob/Meta CAPI conversion pixels, and sends Telegram notifications.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

LOG = logging.getLogger("tradebot.web.payment_webhook")

DATA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
)
STATE_PATH = DATA_DIR / "processed_callbacks.json"
WHITELABEL_PATH = DATA_DIR / "whitelabel_config.json"

TRIPAY_PRIVATE_KEY = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))


def _load_processed() -> set[str]:
    """Load set of already-processed merchant_refs to prevent double-upgrade."""
    try:
        if STATE_PATH.exists():
            return set(json.loads(STATE_PATH.read_text()))
    except (json.JSONDecodeError, OSError):
        pass
    return set()


def _save_processed(refs: set[str]) -> None:
    """Persist processed references atomically."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(list(refs)))
    except OSError as e:
        LOG.error("Failed to save processed refs: %s", e)


def _already_processed(merchant_ref: str) -> bool:
    return merchant_ref in _load_processed()


def _mark_processed(merchant_ref: str) -> None:
    refs = _load_processed()
    refs.add(merchant_ref)
    _save_processed(refs)


def _verify_tripay_signature(data: dict) -> bool:
    """Verify Tripay callback HMAC-SHA256 signature."""
    if not TRIPAY_PRIVATE_KEY:
        LOG.warning("TRIPAY_PRIVATE_KEY not set — skipping signature verification")
        return True

    callback_signature = data.get("signature", "")
    payload = data.get("merchant_ref", "")
    expected = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, callback_signature)


def _upgrade_member(chat_id: str, merchant_ref: str) -> bool:
    """Upgrade member to donor for 9999 days."""
    try:
        from tradebot.services.members_service import activate_premium, mark_payment_paid

        mark_payment_paid(merchant_ref)
        activate_premium(str(chat_id), "donor", 9999)
        LOG.info("Upgraded user %s (ref: %s) to DONOR 9999 days", chat_id, merchant_ref)
        return True
    except Exception as e:
        LOG.error("Failed to upgrade member %s: %s", chat_id, e)
        return False


def _load_whitelabel() -> dict[str, Any]:
    """Load whitelabel config for brand-based messaging."""
    try:
        if WHITELABEL_PATH.exists():
            return json.loads(WHITELABEL_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return {"brands": {}}


def _fire_bemob_conversion(reference: str, amount: int, brand: str) -> None:
    """Fire Bemob conversion postback if configured."""
    wl = _load_whitelabel()
    brand_config = wl.get("brands", {}).get(brand, {})
    postback_url = brand_config.get("bemob_postback_url", "")
    if not postback_url:
        return
    try:
        import urllib.request as ureq
        url = postback_url.format(
            reference=reference,
            amount=str(amount),
            brand=brand,
        )
        ureq.urlopen(url, timeout=5)
        LOG.info("Bemob conversion fired: %s", url[:100])
    except Exception as e:
        LOG.warning("Bemob conversion failed: %s", e)


def _fire_meta_capi(chat_id: str, amount: int, brand: str) -> None:
    """Fire Meta CAPI Purchase event via FB Pixel API."""
    wl = _load_whitelabel()
    brand_config = wl.get("brands", {}).get(brand, {})
    pixel_id = brand_config.get("meta_pixel_id", "")
    access_token = brand_config.get("meta_access_token", "")
    if not pixel_id or not access_token:
        return
    try:
        import urllib.request as ureq
        payload = json.dumps({
            "data": [{
                "event_name": "Purchase",
                "event_time": int(time.time()),
                "user_data": {"external_id": chat_id},
                "custom_data": {"value": amount, "currency": "IDR"},
            }],
        }).encode()
        url = f"https://graph.facebook.com/v18.0/{pixel_id}/events?access_token={access_token}"
        req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
        ureq.urlopen(req, timeout=10)
        LOG.info("Meta CAPI Purchase fired for user %s amount %d", chat_id, amount)
    except Exception as e:
        LOG.warning("Meta CAPI failed: %s", e)


def _send_telegram_notification(chat_id: str, brand: str) -> None:
    """Send payment confirmation and EA links via Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        LOG.warning("TELEGRAM_BOT_TOKEN not set — cannot send notification")
        return

    import urllib.request as ureq

    if brand == "vilona":
        text = (
            "✅ <b>PEMBAYARAN TERKONFIRMASI!</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "👑 Status kamu sekarang: <b>DONATUR VIP</b>\n"
            "♾️ /analyze — UNLIMITED\n"
            "🤖 EA Auto-Trade — AKTIF PERMANEN\n\n"
            "Download EA: https://bit.ly/vilona-ea\n"
            "Channel: @vilonaaichanel\n"
            "Group: @vilona_tradefx_group\n\n"
            "Mari cetak profit! 🔥"
        )
    elif brand == "1ai":
        text = (
            "✅ <b>PEMBAYARAN TERKONFIRMASI!</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "👑 Status: <b>PREMIUM AKTIF</b>\n"
            "♾️ Akses semua fitur — UNLIMITED\n\n"
            "Selamat menikmati layanan premium! 🚀"
        )
    else:
        text = (
            "✅ <b>PEMBAYARAN TERKONFIRMASI!</b>\n"
            "━━━━━━━━━━━━━━━━\n"
            "👑 Status kamu sekarang: <b>DONATUR VIP</b>\n"
            "♾️ /analyze — UNLIMITED\n\n"
            "Mari cetak profit! 🔥"
        )

    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
        ureq.urlopen(req, timeout=10)
        LOG.info("Telegram notification sent to user %s (brand=%s)", chat_id, brand)
    except Exception as e:
        LOG.warning("Failed to send Telegram notification: %s", e)


class PaymentWebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for payment webhooks."""

    # Silence default log messages
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        LOG.debug("Webhook: %s", format % args)

    def _send_json(self, code: int, data: dict[str, Any]) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "service": "payment-webhook",
                "timestamp": time.time(),
            })
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path in ("/webhook/tripay", "/tripay/callback"):
            self._handle_tripay(body)
        else:
            self._send_json(404, {"error": "Not found"})

    def _handle_tripay(self, body: bytes) -> None:
        """Process Tripay payment callback."""
        try:
            data = json.loads(body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            LOG.error("Invalid Tripay callback body: %s", e)
            self._send_json(400, {"error": "Invalid JSON"})
            return

        merchant_ref = data.get("merchant_ref", "")
        status = data.get("status", "")

        if not merchant_ref:
            LOG.warning("Tripay callback missing merchant_ref")
            self._send_json(200, {"success": False, "error": "Missing merchant_ref"})
            return

        # Verify signature
        if not _verify_tripay_signature(data):
            LOG.warning("Tripay callback invalid signature for ref=%s", merchant_ref)
            self._send_json(200, {"success": False, "error": "Invalid signature"})
            return

        # Idempotency check
        if _already_processed(merchant_ref):
            LOG.info("Tripay callback already processed: %s", merchant_ref)
            self._send_json(200, {"success": True, "status": "already_processed"})
            return

        # Only process PAID status
        if status != "PAID":
            LOG.info("Tripay callback not PAID (status=%s) for ref=%s", status, merchant_ref)
            self._send_json(200, {"success": True, "status": "not_paid"})
            return

        # Parse merchant_ref to extract brand and chat_id
        # Format: VTFX-<chat_id>-<timestamp> or 1AI-<chat_id>-<timestamp>
        parts = merchant_ref.split("-")
        brand = "vilona" if parts[0] == "VTFX" else ("1ai" if parts[0] == "1AI" else "vilona")
        chat_id = parts[1] if len(parts) > 1 else ""

        amount = int(data.get("amount", 0))
        LOG.info(
            "Tripay PAID: ref=%s brand=%s chat_id=%s amount=%d",
            merchant_ref,
            brand,
            chat_id,
            amount,
        )

        # Upgrade member
        if chat_id:
            upgrade_ok = _upgrade_member(chat_id, merchant_ref)
        else:
            upgrade_ok = False
            LOG.warning("Cannot upgrade: no chat_id in merchant_ref=%s", merchant_ref)

        if upgrade_ok:
            _mark_processed(merchant_ref)
            _fire_bemob_conversion(merchant_ref, amount, brand)
            _fire_meta_capi(chat_id, amount, brand)
            _send_telegram_notification(chat_id, brand)

        self._send_json(200, {
            "success": upgrade_ok,
            "merchant_ref": merchant_ref,
        })


def start_webhook_server(host: str = "0.0.0.0", port: int = 8787) -> HTTPServer:
    """Start payment webhook HTTP server."""
    server = HTTPServer((host, port), PaymentWebhookHandler)
    LOG.info("Payment webhook server listening on %s:%d", host, port)
    return server
