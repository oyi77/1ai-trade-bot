#!/usr/bin/env python3
"""
Vilona Trade FX — Payment Integration
Tripay + Duitku pricing info + transaction creation.
"""
import json, logging, os, sys, time, urllib.request, urllib.error
from pathlib import Path

logger = logging.getLogger("vtfx-payment")

# ── Pricing ──────────────────────────────────────────────
PRICING = {
    "starter": {
        "tier": "starter", "price_idr": 29000, "label": "Starter",
        "days": 7, "features": "Trial 7 hari • Sinyal dasar • /analyze 3x/hari",
    },
    "pro": {
        "tier": "pro", "price_idr": 79000, "label": "Pro",
        "days": 30, "features": "Akses penuh • Analisa unlimited • Auto-trade EA • Sinyal real-time",
    },
    "elite": {
        "tier": "elite", "price_idr": 149000, "label": "Elite",
        "days": 30, "features": "Multi akun • Auto-trade EA • Custom strategy • Priority support",
    },
}

# ── Tripay Config ─────────────────────────────────────────
TRIPAY_MERCHANT = os.environ.get("TRIPAY_MERCHANT_CODE", "T23409")
TRIPAY_API_KEY = os.environ.get("TRIPAY_API_KEY", "")
TRIPAY_PRIVATE_KEY = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TRIPAY_BASE = os.environ.get("TRIPAY_BASE_URL", "https://tripay.co.id/api")
TRIPAY_CALLBACK = os.environ.get(
    "TRIPAY_CALLBACK_URL",
    "https://phantomfx.aitradepulse.com/webhook/tripay"
)


def get_pricing_info() -> dict:
    """Return full pricing info for display."""
    return {
        "packages": PRICING,
        "methods": ["QRIS", "BRIVA", "BCAVA", "MYBVA"],
        "gateways": ["Tripay", "Duitku"],
    }


def get_pricing_table() -> str:
    """Return formatted pricing text."""
    lines = ["💎 <b>Harga Langganan</b>", "━━━━━━━━━━━━━━━━"]
    for key, pkg in PRICING.items():
        emoji = {"starter": "🆓", "pro": "⭐", "elite": "👑"}.get(key, "📦")
        lines.append(f"{emoji} <b>{pkg['label']}</b> — Rp{pkg['price_idr']:,}/bln")
        lines.append(f"   {pkg['features']}")
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("💳 Bayar via QRIS, VA, atau Retail")
    return "\n".join(lines)


def create_tripay_payment(chat_id: str, username: str, tier: str,
                          method: str = "QRIS2") -> dict:
    """Create Tripay transaction. Returns dict with payment_url."""
    import hashlib, hmac

    pkg = PRICING.get(tier)
    if not pkg:
        return {"error": f"Paket '{tier}' tidak ditemukan"}

    if not TRIPAY_API_KEY or not TRIPAY_PRIVATE_KEY:
        return {"error": "Payment gateway belum dikonfigurasi. Hubungi admin."}

    amount = pkg["price_idr"]
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
            "name": f"VilonaTradeFX - {pkg['label']}",
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
            result = data["data"]
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
        return {"error": f"Payment error ({e.code}). Coba lagi nanti."}
    except Exception as e:
        logger.error("Tripay exception: %s", e)
        return {"error": f"Payment error: {str(e)[:100]}"}
