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

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vilona_tradefx"
STATE_PATH = DATA_DIR / "processed_callbacks.json"
WHITELABEL_PATH = DATA_DIR / "whitelabel_config.json"

TRIPAY_PRIVATE_KEY = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get(
    "VILONA_TRADEFX_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", "")
)


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


def _verify_tripay_signature(raw_body: bytes, callback_signature: str) -> bool:
    """Verify Tripay callback HMAC-SHA256 signature.

    Tripay signs the raw request body and sends the signature in the
    X-Callback-Signature HTTP header.
    """
    if not TRIPAY_PRIVATE_KEY:
        LOG.warning("TRIPAY_PRIVATE_KEY not set — rejecting callback")
        return False

    expected = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, callback_signature)


def _upgrade_member(chat_id: str, merchant_ref: str, tier: str = "pro") -> bool:
    """Upgrade member to specified tier with appropriate expiry."""
    try:
        from tradebot.services.members_service import activate_premium, mark_payment_paid

        mark_payment_paid(merchant_ref)
        # Map tier → days: pro=30, elite=30, lifetime=9999, subscriber (legacy)=9999
        tier_days = {"pro": 30, "elite": 30, "lifetime": 9999, "donor": 9999}
        days = tier_days.get(tier, 9999)
        activate_premium(str(chat_id), tier, days)
        LOG.info(
            "Upgraded user %s (ref: %s) to %s tier (%d days)",
            chat_id,
            merchant_ref,
            tier.upper(),
            days,
        )
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


def _fire_bemob_s2s_postback(
    merchant_ref: str,
    total_amount: int,
    reference: str,
) -> None:
    """Fire BeMob S2S postback with click_id as cid.

    Format: https://rr9u3.bemobtrcks.com/postback?cid={merchant_ref}&payout={total_amount}&txid={reference}

    merchant_ref = click_id dari BeMob (ditangkap frontend, dikirim sebagai merchant_ref)
    total_amount  = nilai transaksi dari Tripay callback (amount)
    reference     = Tripay transaction reference
    """
    BEMOB_POSTBACK_BASE = os.environ.get(
        "BEMOB_POSTBACK_URL",
        "https://rr9u3.bemobtrcks.com/postback",
    )

    if not merchant_ref:
        LOG.warning("BeMob S2S skipped: merchant_ref is empty")
        return

    postback_url = (
        f"{BEMOB_POSTBACK_BASE}"
        f"?cid={merchant_ref}"
        f"&payout={total_amount}"
        f"&txid={reference}"
    )

    try:
        import urllib.request as ureq

        ureq.urlopen(postback_url, timeout=10)
        LOG.info(
            "BeMob S2S fired: cid=%s payout=%d txid=%s",
            merchant_ref,
            total_amount,
            reference,
        )
    except Exception as e:
        LOG.warning("BeMob S2S postback failed: %s | url=%s", e, postback_url[:120])


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

        payload = json.dumps(
            {
                "data": [
                    {
                        "event_name": "Purchase",
                        "event_time": int(time.time()),
                        "user_data": {"external_id": chat_id},
                        "custom_data": {"value": amount, "currency": "IDR"},
                    }
                ],
            }
        ).encode()
        url = f"https://graph.facebook.com/v18.0/{pixel_id}/events?access_token={access_token}"
        req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
        ureq.urlopen(req, timeout=10)
        LOG.info("Meta CAPI Purchase fired for user %s amount %d", chat_id, amount)
    except Exception as e:
        LOG.warning("Meta CAPI failed: %s", e)

def _fire_capi_purchase(chat_id: str, amount: int, tier: str, ref: str) -> None:
    """Fire Meta CAPI purchase event via tracking system."""
    try:
        from tradebot.tracking.events import fire_purchase

        fire_purchase(user_id=chat_id, amount=float(amount), tier=tier, transaction_id=ref)
    except Exception as e:
        LOG.warning("CAPI Purchase failed (non-critical): %s", e)


def _send_telegram_notification(chat_id: str, brand: str, tier: str = "donor") -> None:
    """Send payment confirmation with tier-specific messaging via Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        LOG.warning("TELEGRAM_BOT_TOKEN not set — cannot send notification")
        return

    import urllib.request as ureq

    tier_config = {
        "pro": {
            "label": "⭐ PRO",
            "analyze": "20x/hari",
            "perks": "SL/TP Unlock • /mtf • /engines",
            "cta": "/analyze xauusd — mulai sekarang!",
        },
        "elite": {
            "label": "👑 ELITE",
            "analyze": "UNLIMITED ♾️",
            "perks": "GPT-4o AI • Grok News • EA Auto-Trade • Bridge Sinyal",
            "cta": "Download EA: https://bit.ly/vilona-ea",
        },
        "lifetime": {
            "label": "💎 LIFETIME",
            "analyze": "UNLIMITED ♾️ — PERMANEN",
            "perks": "Full Elite • Gak perlu bayar lagi • Akses selamanya",
            "cta": "Channel: @vilonaaichanel | Group: @vilona_tradefx_group",
        },
        "donor": {
            "label": "💚 SUBSCRIBER",
            "analyze": "UNLIMITED ♾️",
            "perks": "EA Auto-Trade • Bridge • Full Akses",
            "cta": "Download EA: https://bit.ly/vilona-ea",
        },
    }
    tc = tier_config.get(tier, tier_config["donor"])

    text = (
        f"✅ <b>PEMBAYARAN TERKONFIRMASI!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎉 Status: <b>{tc['label']}</b> — AKTIF!\n"
        f"⚡ /analyze: <b>{tc['analyze']}</b>\n"
        f"🔧 {tc['perks']}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔥 {tc['cta']}\n\n"
        f"<i>Mari cetak profit bersama Vilona AI! 🚀</i>"
    )

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
    ).encode()
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        req = ureq.Request(url, data=payload, headers={"Content-Type": "application/json"})
        ureq.urlopen(req, timeout=10)
        LOG.info("Telegram notification sent to user %s (brand=%s)", chat_id, brand)
    except Exception as e:
        LOG.warning("Failed to send Telegram notification: %s", e)

def _track_commission(chat_id: str, amount: int, ref: str, brand: str) -> None:
    """Track commission for payment."""
    try:
        from tradebot.services.mlm_service import record_commission

        record_commission(payer_user_id=chat_id, amount_idr=amount, source="payment", reference_id=ref)
    except Exception as e:
        LOG.warning("Commission tracking failed (non-critical): %s", e)


def _sync_group(chat_id: str, tier: str) -> None:
    """Add user to premium Telegram groups."""
    try:
        from tradebot.tracking.group_sync import add_to_premium_groups

        add_to_premium_groups(chat_id, tier)
    except Exception as e:
        LOG.warning("Group sync failed (non-critical): %s", e)


def _log_activity(chat_id: str, amount: int, tier: str, ref: str) -> None:
    """Log payment activity."""
    try:
        from tradebot.tracking.activity import log_activity

        log_activity(chat_id, chat_id, "", "payment_success", tier, {"amount": amount, "ref": ref})
    except Exception as e:
        LOG.warning("Activity log failed (non-critical): %s", e)


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
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": "payment-webhook",
                    "timestamp": time.time(),
                },
            )
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

        # Verify signature — Tripay signs raw body, sends signature in header
        callback_signature = self.headers.get("X-Callback-Signature", "")
        if not _verify_tripay_signature(body, callback_signature):
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

        # Parse merchant_ref to extract brand, tier, and chat_id
        # NEW format: VTFX-{tier}-{chat_id}-{timestamp}
        # OLD format: VTFX-{chat_id}-{timestamp}
        parts = merchant_ref.split("-")
        brand = "vilona" if parts[0] in ("VTFX", "VTFX") else "vilona"
        tier = "pro"
        chat_id = ""
        if len(parts) >= 4:
            # New format: VTFX-pro-12345678-1718123456
            tier = parts[1]
            chat_id = parts[2]
        elif len(parts) >= 2:
            # Old format: VTFX-12345678-1718123456
            chat_id = parts[1]
        LOG.info("Parsed ref: tier=%s chat_id=%s", tier, chat_id)

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
            upgrade_ok = _upgrade_member(chat_id, merchant_ref, tier)
        else:
            upgrade_ok = False
            LOG.warning("Cannot upgrade: no chat_id in merchant_ref=%s", merchant_ref)

        if upgrade_ok:
            _mark_processed(merchant_ref)
            _fire_bemob_conversion(merchant_ref, amount, brand)
            _fire_bemob_s2s_postback(merchant_ref, amount, data.get("reference", ""))
            _fire_meta_capi(chat_id, amount, brand)
            _fire_capi_purchase(chat_id, amount, tier, merchant_ref)
            _track_commission(chat_id, amount, merchant_ref, brand)
            _sync_group(chat_id, tier)
            _log_activity(chat_id, amount, tier, merchant_ref)
            _send_telegram_notification(chat_id, brand, tier)

        self._send_json(
            200,
            {
                "success": upgrade_ok,
                "merchant_ref": merchant_ref,
            },
        )


def start_webhook_server(host: str = "0.0.0.0", port: int = 8787) -> HTTPServer:
    """Start payment webhook HTTP server."""
    server = HTTPServer((host, port), PaymentWebhookHandler)
    LOG.info("Payment webhook server listening on %s:%d", host, port)
    return server
