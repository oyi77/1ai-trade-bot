#!/usr/bin/env python3
"""
Payment Webhook Server — Vilona Trade FX
Listens on port 8787. Receives Tripay callbacks forwarded from bridge.
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
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "")
BOT_TOKEN = os.environ.get("VILONA_TRADEFX_TELEGRAM_BOT_TOKEN", "")
BOT_TOKEN_1AI = os.environ.get("TELEGRAM_BOT_TOKEN_1AI", "8343388239:AAFgeAkc9bvjywyCsHqRIa_RiJ6q-rp6uv0")

# ── Donation model: ANY amount → donor (LIFETIME) ─────────
# No more fixed tiers. "Dukung Server AI" = pay-what-you-want.
DONOR_DAYS = 9999  # Lifetime access — bukan subscription


def tg_send(text: str, chat_id: str, bot_token: str = None, reply_markup=None):
    """Send Telegram message via API."""
    token = bot_token or BOT_TOKEN
    if not token or not chat_id:
        return None
    try:
        payload = {
            "chat_id": chat_id, "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
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


def upgrade_member(chat_id: str, tier: str, days: int, merchant_ref: str = "") -> bool:
    """Upgrade member ke DONATUR via members.db (SQLite). Returns True on full success."""
    try:
        from members import upgrade_tier as mem_upgrade, mark_payment_paid
        mem_upgrade(chat_id, tier, days, merchant_ref)
        log.info(f"Upgraded in members.db: {chat_id} → {tier}")
        try:
            mark_payment_paid(merchant_ref)
            log.info(f"Payment marked paid: {merchant_ref}")
        except Exception as e:
            log.error(f"mark_payment_paid failed for ref {merchant_ref}: {e}")
            # Upgrade succeeded but payment marker failed — log and continue
        # ── Meta Conversions API: Fire Purchase event ──
        try:
            from scripts.meta_conversion import send_purchase
            # Extract amount from merchant_ref if available (format: VTFX-chatid-amount)
            amount = 50000  # default
            if merchant_ref:
                parts = merchant_ref.split("-")
                if len(parts) >= 3:
                    try:
                        amount = int(parts[2]) if parts[2].isdigit() else 50000
                    except: pass
            send_purchase(amount, chat_id)
        except Exception as e:
            log.warning(f"Meta CAPI Purchase failed (non-critical): {e}")
        return True
    except Exception as e:
        log.error(f"members.db upgrade failed for {chat_id}: {e}")
        return False


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
        try:
            content_len = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_len = 0
        body = self.rfile.read(content_len) if content_len else b""

        log.info(f"Webhook: {path} ({content_len} bytes)")

        if path in ("/webhook/tripay", "/tripay/callback"):
            self._handle_tripay(body)
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
        if not callback_sig or not verify_tripay_signature(body, callback_sig):
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

        chat_id = ""
        brand = "vilona"
        try:
            parts = merchant_ref.split("-")
            if len(parts) >= 2:
                prefix = parts[0].upper()
                if prefix == "VTFX":
                    brand, chat_id = "vilona", parts[1]
                elif prefix == "1AI":
                    brand, chat_id = "1ai", parts[1]
        except Exception as e:
            log.warning(f"Failed to extract chat_id from ref {merchant_ref}: {e}")

        if not chat_id:
            log.error(f"Cannot extract chat_id from ref: {merchant_ref}")
            self._json({"error": "invalid merchant_ref"}, 400)
            return

        token = BOT_TOKEN_1AI if brand == "1ai" else BOT_TOKEN

        # Upgrade member to DONOR
        if not upgrade_member(chat_id, "donor", DONOR_DAYS, merchant_ref):
            log.error(f"Member upgrade failed for {chat_id} — returning 500 for retry")
            self._json({"status": "error", "message": "upgrade_failed"}, 500)
            return

        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from unified_bot.telegram.whitelabel_runner import track_commission
            track_commission(payment_id=merchant_ref, amount=float(total_amount),
                user_id=chat_id, brand_id=brand)
        except Exception as e:
            log.warning(f"Commission tracking skipped: {e}")

        if brand == "1ai":
            msg = (
                f"🔥 <b>BOOM! Bahan bakar server sudah masuk.</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Rp{int(total_amount):,}</b> — Makasih Bro!\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👑 Status kamu sekarang: <b>DONATUR VIP</b>\n"
                f"\n"
                f"Akses VIP kamu <b>AKTIF PERMANEN</b>.\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ /analyze — UNLIMITED\n"
                f"✅ /sinyal — REAL-TIME\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 Admin: @codergaboets"
            )
            markup = {"inline_keyboard": [
                [{"text": "📊 Analyze Now", "url": "https://t.me/agent_1ai2_bot"}],
                [{"text": "📞 Contact Admin", "url": "https://t.me/codergaboets"}],
            ]}
        else:
            channel_link = "https://t.me/vilonaaichanel"
            group_link = "https://t.me/+kX8tspebrpVhMmE1"
            ea_link = "https://phantomfx.aitradepulse.com/ea/download/"
            msg = (
                f"🔥 <b>BOOM! Bahan bakar server sudah masuk.</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Rp{int(total_amount):,}</b> — Makasih Bro!\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👑 Status kamu sekarang: <b>DONATUR VIP</b>\n"
                f"\n"
                f"Akses VIP kamu <b>AKTIF PERMANEN</b>.\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ /analyze — UNLIMITED\n"
                f"✅ EA Auto-Trade — AKTIF\n"
                f"✅ EA Bridge — AKTIF PERMANEN\n"
                f"✅ Master API Key — multi MT5\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 Download EA MT5: {ea_link}\n"
                f"📢 Channel: {channel_link}\n"
                f"👥 Grup: {group_link}"
            )
            markup = {"inline_keyboard": [
                [{"text": "📥 Download EA MT5", "url": ea_link}],
                [{"text": "📢 Join Channel", "url": channel_link}],
                [{"text": "👥 Join Grup", "url": group_link}],
            ]}
        result = tg_send(msg, chat_id, bot_token=token, reply_markup=markup)
        if result is None:
            log.warning(f"tg_send DM to {chat_id} returned None (message may not have been delivered)")

        # Notify group — Social Proof (semangat gotong royong)
        if GROUP_CHAT_ID:
            group_msg = (
                f"🔥 <b>BAHAN BAKAR AI MASUK! 🚀</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💰 Ada kawan yang baru men-support server AI\n"
                f"sebesar <b>Rp{int(total_amount):,}</b>!\n"
                f"\n"
                f"Terima kasih orang baik! Mesin AI kita\n"
                f"makin buas hari ini berkat dukunganmu. 🥂\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💚 Mau ikut bensin server? /donate\n"
                f"📢 Signal real-time: @vilonaaichanel"
            )
            group_result = tg_send(group_msg, GROUP_CHAT_ID)
            if group_result is None:
                log.warning("tg_send to GROUP_CHAT_ID returned None (group notification may not have been delivered)")

        log.info(f"✅ Donation complete: {chat_id} → DONATUR (ref={merchant_ref[:16]} amount={total_amount})")
        self._json({"status": "ok", "chat_id": chat_id, "donor": True})

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
    log.info(f"   Health:  /health")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
