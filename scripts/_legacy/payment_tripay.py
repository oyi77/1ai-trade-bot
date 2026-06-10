#!/usr/bin/env python3
"""Vilona Trade FX — Tripay Payment Integration (Reconstructed)
Auto-generate license key saat pembayaran sukses.

Tripay API Docs: https://tripay.co.id/developer
"""
import hashlib, hmac, json, logging, os, time, urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("vtfx-tripay")
WIB = timezone(timedelta(hours=7))

TRIPAY_MERCHANT_CODE = os.environ.get("TRIPAY_MERCHANT_CODE", "T23409")
TRIPAY_API_KEY       = os.environ.get("TRIPAY_API_KEY", "")
TRIPAY_PRIVATE_KEY   = os.environ.get("TRIPAY_PRIVATE_KEY", "")
TRIPAY_BASE_URL      = os.environ.get("TRIPAY_BASE_URL", "https://tripay.co.id/api")
TRIPAY_CALLBACK_URL  = os.environ.get("TRIPAY_CALLBACK_URL", "")
DEFAULT_METHOD       = os.environ.get("TRIPAY_DEFAULT_METHOD", "QRIS2")

# Donation model — "Dukung Server AI" (pay-what-you-want)
# Any amount above minimum → DONATUR status
PRODUCT_TIERS = {
    "vtfx-donasi": {"tier": "donor", "price": 0, "label_prefix": "Donatur"},
}

MIN_DONATION = 10000  # Minimum Rp10.000

PAYMENT_STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "data", "payments.json")


def _sign(payload: str) -> str:
    """Generate Tripay HMAC signature."""
    return hmac.new(
        TRIPAY_PRIVATE_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()


def verify_callback_signature(callback_data: str, callback_signature: str) -> bool:
    """Verify Tripay webhook callback."""
    expected = _sign(callback_data)
    return hmac.compare_digest(expected, callback_signature)


def create_transaction(user_id: str, username: str, amount: int,
                       method: str = None, customer_email: str = "",
                       customer_phone: str = "", order_items: list = None) -> dict:
    """Create Tripay Closed Payment transaction.

    Args:
        user_id: Telegram user ID
        username: Telegram username
        amount: Amount in IDR (must match product price)
        method: Payment method code (e.g. 'QRIS2', 'BRIVA', 'MYBVA')
        customer_email: Customer email
        customer_phone: Customer phone
        order_items: List of {"name": str, "price": int, "quantity": int}

    Returns:
        dict with payment URL + reference
    """
    if method is None:
        method = DEFAULT_METHOD

    # Open-amount donation model — any amount valid
    product_key = "vtfx-donasi"
    label = "Dukung Server AI - VilonaTradeFX"

    merchant_ref = f"VTFX-{user_id}-{int(time.time())}"
    payload = {
        "method": method,
        "merchant_ref": merchant_ref,
        "amount": amount,
        "customer_name": username or f"User{user_id}",
        "customer_email": customer_email or f"{user_id}@telegram.user",
        "customer_phone": customer_phone or "08123456789",
        "order_items": order_items or [
            {"name": label, "price": amount, "quantity": 1}
        ],
        "callback_url": TRIPAY_CALLBACK_URL or "https://phantomfx.aitradepulse.com/webhook/tripay",
        "return_url": "https://t.me/berkahkaryaforexbotbot",
        "expired_time": int(time.time()) + 3600,  # 1 hour
        "signature": "",  # will be set below
    }

    # Generate signature: HMAC-SHA256(merchant_code + merchant_ref + amount, private_key)
    raw = f"{TRIPAY_MERCHANT_CODE}{merchant_ref}{amount}"
    payload["signature"] = _sign(raw)

    # Call Tripay API
    url = f"{TRIPAY_BASE_URL}/transaction/create"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={
                                     "Authorization": f"Bearer {TRIPAY_API_KEY}",
                                     "Content-Type": "application/json"
                                 })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())

        # Store pending payment
        _store_payment(merchant_ref, user_id, username, amount, product_key)

        return result
    except Exception as e:
        logger.error(f"Tripay create transaction failed: {e}")
        return {"success": False, "error": str(e)}


def get_payment_channels() -> list:
    """Get available Tripay payment channels."""
    url = f"{TRIPAY_BASE_URL}/merchant/payment-channel"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TRIPAY_API_KEY}"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())
    except Exception as e:
        logger.error(f"Tripay get channels failed: {e}")
        return []


def get_transaction_detail(reference: str) -> dict:
    """Check Tripay transaction status by reference."""
    payload = {"merchant_ref": reference}
    raw = reference + TRIPAY_MERCHANT_CODE
    payload["signature"] = _sign(raw)

    url = f"{TRIPAY_BASE_URL}/transaction/detail"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={
                                     "Authorization": f"Bearer {TRIPAY_API_KEY}",
                                     "Content-Type": "application/json"
                                 })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())
    except Exception as e:
        logger.error(f"Tripay detail failed: {e}")
        return {"success": False, "error": str(e)}


# ── Payment Store ──
def _load_payments() -> dict:
    if os.path.exists(PAYMENT_STORE_PATH):
        with open(PAYMENT_STORE_PATH) as f:
            return json.load(f)
    return {"payments": {}}


def _store_payment(merchant_ref, user_id, username, amount, product_key):
    data = _load_payments()
    data["payments"][merchant_ref] = {
        "user_id": str(user_id),
        "username": username,
        "amount": amount,
        "product_key": product_key,
        "status": "PENDING",
        "created_at": datetime.now(WIB).isoformat(),
        "license_key": None,
    }
    with open(PAYMENT_STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def mark_payment_done(merchant_ref, license_key):
    """Called after successful payment → mark done + store license."""
    data = _load_payments()
    if merchant_ref in data["payments"]:
        data["payments"][merchant_ref]["status"] = "PAID"
        data["payments"][merchant_ref]["license_key"] = license_key
        data["payments"][merchant_ref]["paid_at"] = datetime.now(WIB).isoformat()
        with open(PAYMENT_STORE_PATH, "w") as f:
            json.dump(data, f, indent=2)
        return True, data["payments"][merchant_ref]
    return False, None


# ── Test ──
if __name__ == "__main__":
    print(f" Tripay Module Ready")
    print(f" Merchant: {TRIPAY_MERCHANT_CODE}")
    print(f" API Key: {'***' if TRIPAY_API_KEY else 'MISSING'}")
    print(f" Private Key: {'***' if TRIPAY_PRIVATE_KEY else 'MISSING'}")
    print(f" Products: {list(PRODUCT_TIERS.keys())}")
    if not TRIPAY_API_KEY or not TRIPAY_PRIVATE_KEY:
        print("\n⚠️ Set TRIPAY_API_KEY & TRIPAY_PRIVATE_KEY di .env")
