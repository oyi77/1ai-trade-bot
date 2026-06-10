#!/usr/bin/env python3
"""Vilona Trade FX — Duitku Payment Integration.
Generate invoice → user pays → callback → auto-generate license key.

Duitku Docs: https://docs.duitku.com/
"""
import hashlib, json, logging, os, time, urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("vtfx-duitku")
WIB = timezone(timedelta(hours=7))

DUITKU_MERCHANT_CODE = os.environ.get("DUITKU_MERCHANT_CODE", "D1821")
DUITKU_API_KEY = os.environ.get("DUITKU_API_KEY", "")
DUITKU_BASE_URL = os.environ.get("DUITKU_BASE_URL", "https://passport.duitku.com/webapi")
DUITKU_CALLBACK_URL = os.environ.get("DUITKU_CALLBACK_URL",
                                     "https://phantomfx.aitradepulse.com/webhook/duitku")

# Product → Tier mapping (sama dengan Tripay)
PRODUCT_TIERS = {
    "vtfx-starter": {"tier": "starter", "price": 29000, "label_prefix": "Starter"},
    "vtfx-pro":     {"tier": "pro",     "price": 79000, "label_prefix": "Pro"},
    "vtfx-elite":   {"tier": "elite",   "price": 149000, "label_prefix": "Elite"},
}


def _sign(merchant_code, amount, merchant_order_id, api_key):
    """Duitku signature: MD5(merchantCode + merchantOrderId + amount + apiKey)"""
    raw = f"{merchant_code}{merchant_order_id}{amount}{api_key}"
    return hashlib.md5(raw.encode()).hexdigest()


def create_transaction(user_id, username, amount,
                       payment_method="VC",  # VC = all channels
                       customer_email="",
                       customer_phone="",
                       product_name="VilonaTradeFX License",
                       expiry_minutes=60) -> dict:
    """Create Duitku payment invoice.

    Args:
        user_id: Telegram user ID
        username: Telegram username
        amount: Amount in IDR
        payment_method: Payment method code (default VC = all available)
        customer_email: Customer email
        customer_phone: Customer phone
        product_name: Product description
        expiry_minutes: Invoice expiry in minutes

    Returns:
        dict with payment_url and reference
    """
    merchant_order_id = f"VTFX-D-{user_id}-{int(time.time())}"

    # Find product from amount
    product_key = None
    for key, info in PRODUCT_TIERS.items():
        if info["price"] == amount:
            product_key = key
            break

    if not product_key:
        product_key = f"vtfx-custom-{amount}"

    # Generate signature
    signature = _sign(DUITKU_MERCHANT_CODE, amount, merchant_order_id, DUITKU_API_KEY)

    # Build payload
    payload = {
        "merchantCode": DUITKU_MERCHANT_CODE,
        "paymentAmount": amount,
        "merchantOrderId": merchant_order_id,
        "productDetails": product_name,
        "email": customer_email or f"{user_id}@telegram.user",
        "customerVaName": username or f"User{user_id}",
        "phoneNumber": customer_phone or "08123456789",
        "paymentMethod": payment_method,
        "callbackUrl": DUITKU_CALLBACK_URL,
        "returnUrl": "https://t.me/berkahkaryaforexbotbot",
        "expiryPeriod": expiry_minutes * 60,  # seconds
        "signature": signature,
    }

    url = f"{DUITKU_BASE_URL}/api/merchant/v2/inquiry"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())

        if result.get("statusCode") == "00":
            # Store pending payment
            _store_payment(merchant_order_id, user_id, username, amount, product_key)

            return {
                "success": True,
                "reference": result.get("reference"),
                "merchant_order_id": merchant_order_id,
                "payment_url": result.get("paymentUrl"),
                "amount": amount,
                "expiry": result.get("expiredDate"),
            }
        else:
            return {
                "success": False,
                "error": result.get("statusMessage", "Unknown error"),
                "code": result.get("statusCode"),
            }

    except Exception as e:
        logger.error(f"Duitku create transaction failed: {e}")
        return {"success": False, "error": str(e)}


def check_transaction(merchant_order_id):
    """Check Duitku transaction status."""
    signature = _sign(DUITKU_MERCHANT_CODE, "", merchant_order_id, DUITKU_API_KEY)
    url = f"{DUITKU_BASE_URL}/api/merchant/transactionStatus"
    payload = {
        "merchantCode": DUITKU_MERCHANT_CODE,
        "merchantOrderId": merchant_order_id,
        "signature": signature,
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return json.loads(resp.read())
    except Exception as e:
        logger.error(f"Duitku check failed: {e}")
        return {"success": False, "error": str(e)}


def verify_callback(callback_data):
    """Verify Duitku callback by checking signature."""
    # Duitku sends a POST with form data
    merchant_code = callback_data.get("merchantCode", "")
    amount = callback_data.get("amount", "0")
    merchant_order_id = callback_data.get("merchantOrderId", "")
    api_key = DUITKU_API_KEY

    expected_sig = _sign(merchant_code, amount, merchant_order_id, api_key)
    received_sig = callback_data.get("signature", "")

    return expected_sig == received_sig


# ── Payment Store (shared with Tripay) ──
PAYMENT_STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "data", "payments.json")


def _load_payments():
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
        "gateway": "duitku",
        "created_at": datetime.now(WIB).isoformat(),
        "license_key": None,
    }
    with open(PAYMENT_STORE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def mark_payment_done(merchant_ref, license_key):
    """Mark payment as done and store license key."""
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
    print(" Duitku Module Ready")
    print(f" Merchant: {DUITKU_MERCHANT_CODE}")
    print(f" API Key: {'***' if DUITKU_API_KEY else 'MISSING'}")

    if DUITKU_API_KEY:
        # Test create transaction
        result = create_transaction(
            user_id="12345",
            username="test_user",
            amount=79000,
            product_name="VilonaTradeFX Pro License",
        )
        print(f"\n Test Transaction:")
        print(f"  Success: {result.get('success')}")
        if result.get('success'):
            print(f"  Payment URL: {result.get('payment_url')}")
        else:
            print(f"  Error: {result.get('error')}")
    else:
        print("\n⚠️ Set DUITKU_API_KEY di .env")
