#!/usr/bin/env python3
"""
Vilona Trade FX — Payment Integration
Tripay pricing info + transaction creation.
"""
import json, logging, os, sys, time, urllib.request, urllib.error
from pathlib import Path

logger = logging.getLogger("vtfx-payment")

# ── Donation Model — "Dukung Server AI" ────────────────────
# Pay-what-you-want, minimum Rp10.000
PRICING = {
    "donor": {
        "tier": "donor", "price_idr": 0, "label": "Donatur",
        "days": 9999, "features": "👑 Akses penuh • /analyze UNLIMITED • Auto-trade EA • Bridge sinyal • Dukung server AI",
    },
}

MIN_DONATION = 10000  # Minimum donasi Rp10.000

# ── Tripay Config ─────────────────────────────────────────
_TRIPAY_KEY = os.environ.get("TRIPAY_API_KEY", "")
_TRIPAY_SANDBOX = _TRIPAY_KEY.startswith("DEV-")

TRIPAY_MERCHANT = os.environ.get(
    "TRIPAY_MERCHANT_CODE",
    "T22632" if _TRIPAY_SANDBOX else "T23409"
)
TRIPAY_API_KEY = _TRIPAY_KEY
TRIPAY_PRIVATE_KEY = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TRIPAY_BASE = os.environ.get(
    "TRIPAY_BASE_URL",
    "https://tripay.co.id/api-sandbox" if _TRIPAY_SANDBOX else "https://tripay.co.id/api"
)
TRIPAY_CALLBACK = os.environ.get(
    "TRIPAY_CALLBACK_URL",
    "https://phantomfx.aitradepulse.com/webhook/tripay"
)


def get_pricing_info() -> dict:
    """Return full pricing info for display."""
    return {
        "packages": PRICING,
        "methods": ["QRIS", "BRIVA", "BCAVA", "MYBVA"],
        "gateways": ["Tripay"],
    }


def get_pricing_table() -> str:
    """Return formatted donation info."""
    return (
        "🔥 <b>Dukung Server AI</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "💰 <b>Dukung seikhlasnya</b> (min Rp10.000)\n"
        "👑 Status <b>DONATUR VIP</b> — AKTIF PERMANEN\n"
        "━━━━━━━━━━━━━━━━\n"
        "✅ /analyze UNLIMITED\n"
        "✅ EA Auto-Trade\n"
        "✅ Bridge Sinyal\n"
        "━━━━━━━━━━━━━━━━\n"
        "💳 QRIS, VA, Retail — otomatis aktif!\n"
    )


def create_tripay_payment(chat_id: str, username: str, tier: str = "donor",
                          method: str = "QRIS2", amount: int = None) -> dict:
    """Create Tripay donation transaction. Returns dict with payment_url.
    
    tier is now 'donor' by default. amount overrides the donation amount.
    """
    import hashlib, hmac

    if not TRIPAY_API_KEY or not TRIPAY_PRIVATE_KEY:
        return {"error": "Payment gateway belum dikonfigurasi. Hubungi admin."}

    if amount is None:
        # Default donation amount (Rp50.000 suggested)
        amount = int(os.environ.get("DONATION_DEFAULT_AMOUNT", "50000"))

    if amount < MIN_DONATION:
        return {"error": f"Minimum donasi Rp{MIN_DONATION:,}"}

    merchant_ref = f"VTFX-{chat_id}-{int(time.time())}"

    # Build payload
    payload = {
        "method": method,
        "merchant_ref": merchant_ref,
        "amount": amount,
        "customer_name": username or f"User{chat_id}",
        "customer_email": f"{chat_id}@telegram.user",
        "customer_phone": "08123456789",
        "order_items": [{
            "name": "Dukung Server AI - VilonaTradeFX",
            "price": amount,
            "quantity": 1,
        }],
        "callback_url": TRIPAY_CALLBACK,
        "return_url": "https://t.me/berkahkaryaforexbotbot",
        "expired_time": int(time.time()) + 3600,
    }

    # HMAC-SHA256 signature
    raw_sign = f"{TRIPAY_MERCHANT}{merchant_ref}{amount}"
    signature = hmac.new(
        TRIPAY_PRIVATE_KEY.encode(), raw_sign.encode(), hashlib.sha256
    ).hexdigest()
    payload["signature"] = signature

    # Call API
    try:
        req = urllib.request.Request(
            f"{TRIPAY_BASE}/transaction/create",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {TRIPAY_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        if data.get("success"):
            result = data.get("data", {})
            # Record payment order in members DB
            try:
                from members import insert_payment_order
                insert_payment_order(
                    merchant_ref=merchant_ref, chat_id=str(chat_id),
                    amount=amount, product_key=tier, gateway="tripay",
                    payload=result,
                )
            except ImportError:
                pass

            return {
                "success": True,
                "reference": result.get("reference", merchant_ref),
                "merchant_ref": merchant_ref,
                "payment_url": result.get("checkout_url", ""),
                "pay_code": result.get("pay_code", ""),
                "qr_url": result.get("qr_url", ""),
                "amount": amount,
                "tier": tier,
                "expired": result.get("expired_time", int(time.time()) + 3600),
            }
        else:
            return {"error": data.get("message", "Payment gagal")}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        logger.error("Tripay HTTP %s: %s", e.code, body)
        if "Invalid signature" in body:
            return {"error": "Tripay: Invalid signature — cek TRIPAY_PRIVATE_KEY di .env"}
        elif "Sandbox credential" in body:
            return {"error": "Tripay: Sandbox key tapi URL production — coba lagi"}
        return {"error": f"Tripay error ({e.code}). Cek API key di dashboard Tripay."}
    except Exception as e:
        logger.error("Tripay exception: %s", e)
        return {"error": f"Payment error: {str(e)[:100]}"}
