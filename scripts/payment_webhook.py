#!/usr/bin/env python3
"""Vilona Trade FX — Payment Webhook Server.
Handle Tripay/Duitku payment callbacks → auto-generate license → DM customer.
Runs as standalone HTTP server on port 8787.
"""
import json, logging, sys, os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from payment_tripay import (
        verify_callback_signature, mark_payment_done,
        PRODUCT_TIERS, get_transaction_detail, _load_payments
    )
    TRIPAY_OK = True
except ImportError:
    TRIPAY_OK = False

try:
    from license_manager import generate_key
    LICENSE_OK = True
except ImportError:
    LICENSE_OK = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vtfx-webhook")


class WebhookHandler(BaseHTTPRequestHandler):

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "ok", "tripay": TRIPAY_OK, "license": LICENSE_OK})
        elif self.path.startswith("/webhook/tripay"):
            self._json({"status": "webhook ready — use POST"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/webhook/tripay":
            self._handle_tripay_webhook()
        elif path == "/webhook/duitku":
            self._handle_duitku_webhook()
        else:
            self._json({"error": "not found"}, 404)

    def _handle_tripay_webhook(self):
        """Handle Tripay payment callback → generate license → DM user."""
        if not TRIPAY_OK:
            self._json({"error": "tripay module not loaded"}, 500)
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = raw.decode()

        # Parse JSON body
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Try form-encoded
            data = {}
            for k, v in parse_qs(body).items():
                data[k] = v[0] if v else ""

        # Verify signature
        callback_data = json.dumps(data) if not isinstance(data.get("callback_data"), str) else data.get("callback_data", body)
        signature = data.get("callback_signature", "")
        if signature and not verify_callback_signature(str(callback_data), str(signature)):
            log.warning("Invalid Tripay signature")
            self._json({"error": "invalid_signature"}, 403)
            return

        # Extract payment info
        merchant_ref = data.get("merchant_ref", "")
        status = data.get("status", "")
        amount = int(data.get("amount", 0))

        log.info(f"Tripay callback: {merchant_ref} | status={status} | amount={amount}")

        if status.upper() != "PAID" and status != "1":
            self._json({"status": "ignored", "reason": f"status={status}"})
            return

        # Payment confirmed → generate license
        self._process_payment(merchant_ref, amount)

    def _handle_duitku_webhook(self):
        """Handle Duitku callback (similar flow)."""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read()
        body = raw.decode()

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}
            for k, v in parse_qs(body).items():
                data[k] = v[0] if v else ""

        merchant_ref = data.get("merchantOrderId", data.get("reference", ""))
        status = data.get("resultCode", data.get("status", ""))
        amount = int(data.get("amount", 0))

        log.info(f"Duitku callback: {merchant_ref} | status={status} | amount={amount}")

        if str(status) not in ("00", "SUCCESS", "PAID"):
            self._json({"status": "ignored", "reason": f"status={status}"})
            return

        self._process_payment(merchant_ref, amount)

    def _process_payment(self, merchant_ref, amount):
        """Auto-generate license key and store."""
        if not LICENSE_OK:
            log.error("License module not loaded!")
            self._json({"error": "license_module_unavailable"}, 500)
            return

        # Determine tier from amount
        tier = "pro"  # default
        label_prefix = "Customer"
        for key, info in PRODUCT_TIERS.items():
            if info["price"] == amount:
                tier = info["tier"]
                label_prefix = info["label_prefix"]
                break

        # Check if already processed
        payments = _load_payments()
        payment = payments.get("payments", {}).get(merchant_ref)
        if payment and payment.get("status") == "PAID":
            log.info(f"Already processed: {merchant_ref}")
            self._json({"status": "already_processed", "license_key": payment.get("license_key")})
            return

        # Generate license
        user_id = payment.get("user_id", "unknown") if payment else "unknown"
        username = payment.get("username", "") if payment else ""
        label = f"{label_prefix} - {username or user_id}"

        try:
            api_key, config = generate_key(tier=tier, label=label)
            log.info(f"✅ License generated: {api_key} ({tier}) for {label}")

            # Mark payment done
            mark_payment_done(merchant_ref, api_key)

            # DM user via bot in background (don't block response)
            import threading
            threading.Thread(target=_notify_user, args=(user_id, api_key, tier, amount),
                             daemon=True).start()

            self._json({
                "status": "ok",
                "license_key": api_key,
                "tier": tier,
                "user_id": user_id,
            })
        except Exception as e:
            log.error(f"License generation failed: {e}")
            self._json({"error": str(e)}, 500)

    def log_message(self, format, *args):
        log.debug(format % args)


def _notify_user(user_id, api_key, tier, amount):
    """Send license key to user via Telegram bot. Graceful if bot unavailable."""
    if user_id == "unknown":
        return

    try:
        import urllib.request as _ur
        import urllib.parse as _up
        # Use Telegram Bot API directly instead of importing bot handler
        bot_token = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
        if not bot_token:
            log.warning("No bot token — skipping DM")
            return

        price_fmt = f"Rp {amount:,}".replace(",", ".")
        text = (
            f"✅ <b>Pembayaran Berhasil!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 {price_fmt}\n"
            f"📦 Tier: <b>{tier.upper()}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 License Key lo:\n"
            f"<code>{api_key}</code>\n\n"
            f"<i>Copy key ini ke EA → input API_Key\n"
            f"Download EA: https://phantomfx.aitradepulse.com/download/ea</i>"
        ).encode()
        data = _up.urlencode({
            "chat_id": user_id,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = _ur.Request(f"https://api.telegram.org/bot{bot_token}/sendMessage", data=data)
        _ur.urlopen(req, timeout=10)
        log.info(f"DM sent to {user_id}")
    except Exception as e:
        log.error(f"Failed to DM user {user_id}: {e}")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    log.info(f"Payment Webhook listening on 0.0.0.0:{port}")
    log.info(f"  Tripay:  POST /webhook/tripay")
    log.info(f"  Duitku:  POST /webhook/duitku")
    log.info(f"  Health:  GET  /health")
    log.info(f"  Tripay: {'✅' if TRIPAY_OK else '❌'} | License: {'✅' if LICENSE_OK else '❌'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()
