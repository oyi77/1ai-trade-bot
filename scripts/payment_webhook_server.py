#!/usr/bin/env python3
"""
Payment Webhook Server — Vilona Trade FX
Listens on port 8787. Receives Tripay/Duitku callbacks forwarded from bridge.
Verifies signature → upgrades member → DMs user.
"""
import hashlib, hmac, json, logging, os, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("payment-webhook")

WIB = timezone(timedelta(hours=7))
PORT = int(os.environ.get("PAYMENT_WEBHOOK_PORT", "8787"))

# ── Env ──────────────────────────────────────────────────
TRIPAY_PRIVATE_KEY = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TRIPAY_MERCHANT_CODE = os.environ.get("TRIPAY_MERCHANT_CODE", "T23409")
ADMIN_CHAT_ID = os.environ.get("VILONA_TRADEFX_ADMIN_CHAT_ID", os.environ.get("VILONA_TRADEFX_CHAT_ID", ""))
BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")

# ── Tier mapping ──────────────────────────────────────────
AMOUNT_TO_TIER = {
    29000: ("starter", 7),
    79000: ("pro", 30),
    149000: ("elite", 30),
}


def tg_send(text: str, chat_id: str, bot_token: str = None):
    """Send Telegram message via API."""
    token = bot_token or BOT_TOKEN
    if not token or not chat_id:
        return None
    try:
        payload = json.dumps({
            "chat_id": chat_id, "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.error(f"tg_send failed: {e}")
        return None


def verify_tripay_signature(body: bytes, callback_sig: str) -> bool:
    """Verify Tripay HMAC-SHA256 signature."""
    if not TRIPAY_PRIVATE_KEY:
        log.warning("TRIPAY_PRIVATE_KEY not set — skipping signature check")
        return True  # Allow in dev mode
    expected = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, callback_sig)


def upgrade_member(chat_id: str, tier: str, days: int, merchant_ref: str = ""):
    """Upgrade member in both subscription_manager (JSON) and members (SQLite)."""
    # subscription_manager (JSON)
    try:
        from subscription_manager import upgrade_tier as sub_upgrade
        sub_upgrade(chat_id, tier, days)
        log.info(f"Upgraded in subscription_manager: {chat_id} → {tier}")
    except Exception as e:
        log.warning(f"subscription_manager upgrade failed: {e}")

    # members.db (SQLite)
    try:
        from members import upgrade_tier as mem_upgrade, mark_payment_paid
        mem_upgrade(chat_id, tier, days, merchant_ref)
        mark_payment_paid(merchant_ref)
        log.info(f"Upgraded in members.db: {chat_id} → {tier}")
    except Exception as e:
        log.warning(f"members.db upgrade failed: {e}")


class WebhookHandler(BaseHTTPRequestHandler):
    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json({"status": "ok", "service": "vtfx-payment-webhook"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b""

        log.info(f"Webhook: {path} ({content_len} bytes)")

        if path in ("/webhook/tripay", "/tripay/callback"):
            self._handle_tripay(body)
        elif path in ("/webhook/duitku", "/duitku/callback"):
            self._handle_duitku(body)
        elif path == "/health":
            self._json({"status": "ok"})
        else:
            self._json({"error": "not found"}, 404)

    def _handle_tripay(self, body: bytes):
        """Process Tripay payment callback."""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return

        # Verify signature
        callback_sig = self.headers.get("X-Callback-Signature", "")
        if callback_sig and not verify_tripay_signature(body, callback_sig):
            log.warning(f"Invalid Tripay signature for: {data.get('merchant_ref', '?')}")
            self._json({"error": "invalid signature"}, 403)
            return

        merchant_ref = data.get("merchant_ref", "")
        status = data.get("status", "").upper()
        amount = data.get("amount", 0)
        total_amount = data.get("total_amount", amount)

        log.info(f"Tripay callback: ref={merchant_ref} status={status} amount={total_amount}")

        # Only process PAID/SUCCESS
        if status not in ("PAID", "SUCCESS", "SETTLEMENT"):
            log.info(f"Ignoring status: {status}")
            self._json({"status": "ignored", "reason": f"status={status}"})
            return

        # Extract chat_id from merchant_ref: VTFX-{chat_id}-{timestamp}
        chat_id = ""
        try:
            parts = merchant_ref.split("-")
            if len(parts) >= 2 and parts[0] == "VTFX":
                chat_id = parts[1]
        except Exception:
            pass

        if not chat_id:
            log.error(f"Cannot extract chat_id from ref: {merchant_ref}")
            self._json({"error": "invalid merchant_ref"}, 400)
            return

        # Map amount to tier
        tier, days = "pro", 30
        for amt, (t, d) in AMOUNT_TO_TIER.items():
            if abs(total_amount - amt) < 5000:
                tier, days = t, d
                break

        # Upgrade member
        upgrade_member(chat_id, tier, days, merchant_ref)

        # DM user
        tier_emoji = {"starter": "🆓", "pro": "⭐", "elite": "👑"}.get(tier, "📦")
        msg = (
            f"✅ <b>Pembayaran Diterima!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 Rp{total_amount:,}\n"
            f"{tier_emoji} Tier: <b>{tier.upper()}</b>\n"
            f"📅 Durasi: {days} hari\n"
            f"🔑 Ref: <code>{merchant_ref[:16]}</code>\n\n"
            f"Selamat! Semua fitur {tier.upper()} sudah aktif.\n"
            f"👉 /help — Lihat command\n"
            f"👉 /analyze xauusd — Mulai analisa"
        )
        tg_send(msg, chat_id)

        # Notify admin
        if ADMIN_CHAT_ID and ADMIN_CHAT_ID != chat_id:
            admin_msg = (
                f"💰 <b>Pembayaran Baru!</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"👤 User: <code>{chat_id}</code>\n"
                f"{tier_emoji} Tier: <b>{tier.upper()}</b>\n"
                f"💰 Rp{total_amount:,}\n"
                f"📅 {days} hari"
            )
            tg_send(admin_msg, ADMIN_CHAT_ID)

        log.info(f"✅ Payment complete: {chat_id} → {tier} ({days}d)")
        self._json({"status": "ok", "tier": tier, "chat_id": chat_id})

    def _handle_duitku(self, body: bytes):
        """Process Duitku payment callback."""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json({"error": "invalid json"}, 400)
            return

        merchant_order_id = data.get("merchantOrderId", "")
        result_code = str(data.get("resultCode", ""))

        log.info(f"Duitku callback: order={merchant_order_id} result={result_code}")

        if result_code != "00":
            self._json({"status": "ignored", "reason": f"resultCode={result_code}"})
            return

        # Extract chat_id from merchantOrderId: VTFX-{chat_id}-{timestamp}
        chat_id = ""
        try:
            parts = merchant_order_id.split("-")
            if len(parts) >= 2 and parts[0] == "VTFX":
                chat_id = parts[1]
        except Exception:
            pass

        if not chat_id:
            self._json({"error": "invalid merchantOrderId"}, 400)
            return

        amount = data.get("amount", 0)
        tier, days = "pro", 30
        for amt, (t, d) in AMOUNT_TO_TIER.items():
            if abs(int(amount) - amt) < 5000:
                tier, days = t, d
                break

        upgrade_member(chat_id, tier, days, merchant_order_id)
        self._json({"status": "ok", "tier": tier})

    def log_message(self, format, *args):
        log.info(f"{self.client_address[0]} — {format % args}")


def main():
    # Load env from .env
    env_paths = [
        Path(__file__).resolve().parent / "strategies" / "vilona_tradefx" / ".env",
        Path(__file__).resolve().parent.parent / "strategies" / "vilona_tradefx" / ".env",
    ]
    for env_path in env_paths:
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k, v.strip())
            log.info(f"Loaded env: {env_path}")
            break

    server = HTTPServer(("127.0.0.1", PORT), WebhookHandler)
    log.info(f"💰 Payment webhook listening on :{PORT}")
    log.info(f"   Tripay:  /webhook/tripay")
    log.info(f"   Duitku:  /webhook/duitku")
    log.info(f"   Health:  /health")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
