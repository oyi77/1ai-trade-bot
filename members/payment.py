#!/usr/bin/env python3
"""
Vilona Trade FX — Tiered Subscription + Payment Integration
Tripay pricing info + transaction creation.
Model: Monthly recurring tiers (Pro/Elite) + Lifetime one-time.
"""
import json, logging, os, sys, time, urllib.request, urllib.error
from pathlib import Path

logger = logging.getLogger("vtfx-payment")

# ── TIERED PRICING (Monthly Recurring) ──────────────────
# Free: 5 /analyze per day (DeepSeek only, SL/TP locked)
# Pro: 20 /analyze per day (DeepSeek only, SL/TP unlocked)
# Elite: Unlimited /analyze (DeepSeek + GPT-4o + Grok News, EA + Bridge)
# Lifetime: Elite features permanently (one-time payment)
PRICING = {
    "pro": {
        "tier": "pro",
        "price_idr": 50000,
        "label": "Pro ⭐",
        "days": 30,
        "recurring": True,
        "analyze_limit": 20,
        "features": (
            "✅ /analyze 20x/hari\n"
            "✅ SL/TP full unlock\n"
            "✅ /mtf unlimited\n"
            "✅ /engines unlocked\n"
            "✅ DeepSeek V4 AI analysis"
        ),
    },
    "elite": {
        "tier": "elite",
        "price_idr": 150000,
        "label": "Elite 👑",
        "days": 30,
        "recurring": True,
        "analyze_limit": -1,  # unlimited
        "features": (
            "✅ /analyze UNLIMITED\n"
            "✅ DeepSeek + GPT-4o AI\n"
            "✅ Grok News market context\n"
            "✅ EA Auto-Trade akses\n"
            "✅ Bridge sinyal real-time\n"
            "✅ SL/TP full unlock\n"
            "✅ Prioritas support"
        ),
    },
    "lifetime": {
        "tier": "lifetime",
        "price_idr": 500000,
        "label": "Lifetime 💎",
        "days": 9999,
        "recurring": False,
        "analyze_limit": -1,  # unlimited
        "features": (
            "✅ Semua fitur ELITE\n"
            "✅ Akses PERMANEN selamanya\n"
            "✅ Gak perlu bayar bulanan\n"
            "✅ Limited slots — 5/bulan only"
        ),
    },
}

# Free tier limit
FREE_DAILY_LIMIT = 5
# Backward compat: old donate callbacks use tier="donor" → maps to "lifetime" (grandfathered)
PRICING["donor"] = dict(PRICING["lifetime"], tier="donor", label="Donatur 💚")

MIN_DONATION = int(os.environ.get("TRIPAY_MIN_DONATION", "10000"))

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
        "free_limit": FREE_DAILY_LIMIT,
        "methods": ["QRIS", "BRIVA", "BCAVA", "MYBVA"],
        "gateways": ["Tripay"],
    }


def get_tier(tier_key: str) -> dict | None:
    """Get tier config by key. Returns None if invalid."""
    return PRICING.get(tier_key)


def get_pricing_table() -> str:
    """Return formatted subscription pricing display."""
    return (
        "🔥 <b>Vilona Trade FX — Subscription</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆓 <b>FREE</b> — Rp0\n"
        f"├ /analyze {FREE_DAILY_LIMIT}x/hari\n"
        "├ SL/TP dikunci (Signal Tease)\n"
        "└ DeepSeek AI analysis\n\n"
        "⭐ <b>PRO</b> — Rp50.000/bulan\n"
        "├ /analyze 20x/hari\n"
        "├ SL/TP full unlock\n"
        "├ /mtf + /engines unlocked\n"
        "└ DeepSeek V4 AI\n\n"
        "👑 <b>ELITE</b> — Rp150.000/bulan\n"
        "├ /analyze UNLIMITED\n"
        "├ DeepSeek + GPT-4o AI\n"
        "├ Grok News market context\n"
        "├ EA Auto-Trade akses\n"
        "└ Bridge sinyal real-time\n\n"
        "💎 <b>LIFETIME</b> — Rp500.000 (sekali bayar)\n"
        "├ Semua fitur ELITE\n"
        "├ Akses PERMANEN selamanya\n"
        "└ Limited: 5 slot/bulan\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💳 QRIS, VA, Retail — otomatis aktif!\n"
        "Ketik /subscribe <tier> untuk mulai.\n"
        "Contoh: /subscribe pro"
    )


def create_tripay_payment(chat_id: str, username: str, tier: str = "pro",
                          method: str = "QRIS", amount: int = None) -> dict:
    """Create Tripay subscription/donation transaction.

    Args:
        chat_id: Telegram chat ID
        username: Telegram username
        tier: 'pro', 'elite', or 'lifetime'
        method: Payment method (QRIS, BRIVA, etc.)
        amount: Override amount (uses tier default if None)

    Returns dict with payment_url or error.
    """
    import hashlib, hmac

    if not TRIPAY_API_KEY or not TRIPAY_PRIVATE_KEY:
        return {"error": "Payment gateway belum dikonfigurasi. Hubungi admin."}

    tier_config = PRICING.get(tier)
    if not tier_config:
        return {"error": f"Tier '{tier}' tidak valid. Pilih: pro, elite, lifetime"}

    if amount is None:
        amount = tier_config["price_idr"]

    if amount < MIN_DONATION:
        return {"error": f"Minimum pembayaran Rp{MIN_DONATION:,}"}

    merchant_ref = f"VTFX-{tier}-{chat_id}-{int(time.time())}"

    # Build payload with tier metadata
    payload = {
        "method": method,
        "merchant_ref": merchant_ref,
        "amount": amount,
        "customer_name": username or f"User{chat_id}",
        "customer_email": f"{chat_id}@telegram.user",
        "customer_phone": "08123456789",
        "order_items": [{
            "name": f"VilonaTradeFX - {tier_config['label']}",
            "price": amount,
            "quantity": 1,
        }],
        "callback_url": TRIPAY_CALLBACK,
        "return_url": os.environ.get(
            "TRIPAY_RETURN_URL",
            "https://t.me/berkahkaryaforexbotbot"
        ),
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
                    merchant_ref=merchant_ref,
                    chat_id=str(chat_id),
                    amount=amount,
                    product_key=tier,
                    gateway="tripay",
                    payload=result,
                )
            except ImportError:
                logger.warning(
                    "payment order tracking unavailable — "
                    "import insert_payment_order failed"
                )

            return {
                "success": True,
                "reference": result.get("reference", merchant_ref),
                "merchant_ref": merchant_ref,
                "payment_url": result.get("checkout_url", ""),
                "pay_code": result.get("pay_code", ""),
                "qr_url": result.get("qr_url", ""),
                "amount": amount,
                "tier": tier,
                "tier_label": tier_config["label"],
                "expired": result.get("expired_time", int(time.time()) + 3600),
            }
        else:
            return {"error": data.get("message", "Payment gagal")}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        logger.error("Tripay HTTP %s: %s", e.code, body)
        if "Invalid signature" in body:
            return {
                "error": "Tripay: Invalid signature — cek TRIPAY_PRIVATE_KEY di .env"
            }
        elif "Sandbox credential" in body:
            return {
                "error": "Tripay: Sandbox key tapi URL production — coba lagi"
            }
        return {"error": f"Tripay error ({e.code}). Cek API key di dashboard Tripay."}
    except Exception as e:
        logger.error("Tripay exception: %s", e)
        return {"error": f"Payment error: {str(e)[:100]}"}


def _sign(raw: str) -> str:
    """HMAC-SHA256 signature for Tripay API."""
    import hashlib, hmac
    return hmac.new(
        TRIPAY_PRIVATE_KEY.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def check_tripay_status(merchant_ref: str) -> dict:
    """Check Tripay transaction status by merchant_ref."""
    if not TRIPAY_API_KEY or not TRIPAY_PRIVATE_KEY:
        return {"success": False, "error": "Tripay API key not configured"}

    payload = {"merchant_ref": merchant_ref}
    payload["signature"] = _sign(TRIPAY_MERCHANT + merchant_ref)

    try:
        req = urllib.request.Request(
            f"{TRIPAY_BASE}/transaction/detail",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {TRIPAY_API_KEY}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        logger.error("Tripay status check HTTP %s: %s", e.code, body)
        return {"success": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        logger.error("Tripay status check failed: %s", e)
        return {"success": False, "error": str(e)[:200]}


def is_tripay_paid(merchant_ref: str) -> bool:
    """Returns True if Tripay payment is confirmed PAID."""
    result = check_tripay_status(merchant_ref)
    if result.get("success") and result.get("data"):
        status = result["data"].get("status", "").upper()
        return status in ("PAID", "SUCCESS", "SETTLEMENT")
    return False
