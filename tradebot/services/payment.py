"""PaymentService — Unified payment gateway integration.

Wraps Tripay and Duitku payment APIs into a single service.
Uses httpx for HTTP calls and tradebot.config.settings for credentials.

Absorbed from scripts/payment_tripay.py and scripts/payment_duitku.py.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from tradebot.config import settings

LOG = logging.getLogger("tradebot.services.payment")


class PaymentService:
    """Unified payment gateway for Tripay and Duitku.

    Provides transaction creation, callback verification,
    and payment channel listing for both gateways.
    """

    def __init__(self, store_path: str | Path | None = None):
        self._store_path = Path(store_path or settings.PAYMENT_STORE_PATH)

    # ── Tripay ───────────────────────────────────────────────────

    @staticmethod
    def _tripay_sign(payload: str) -> str:
        """Generate Tripay HMAC-SHA256 signature."""
        return hmac.new(
            settings.TRIPAY_PRIVATE_KEY.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def verify_tripay_callback(callback_data: str, callback_signature: str) -> bool:
        """Verify Tripay webhook callback signature."""
        expected = PaymentService._tripay_sign(callback_data)
        return hmac.compare_digest(expected, callback_signature)

    async def create_tripay_transaction(
        self,
        user_id: str,
        username: str,
        amount: int,
        method: str = "",
        tier: str = "pro",
        customer_email: str = "",
        customer_phone: str = "",
        order_items: list[dict] | None = None,
    ) -> dict:
        """Create Tripay Closed Payment transaction.

        Args:
            user_id: Telegram user ID.
            username: Telegram username.
            amount: Amount in IDR.
            method: Payment method code (e.g. 'QRIS2', 'BRIVA').
            customer_email: Customer email.
            customer_phone: Customer phone.
            order_items: List of {name, price, quantity} dicts.

        Returns:
            dict with payment URL + reference, or error.
        """
        if not method:
            method = settings.TRIPAY_DEFAULT_METHOD

        merchant_ref = f"VTFX-{tier}-{user_id}-{int(time.time())}"
        label = "Dukung Server AI - VilonaTradeFX"

        raw = f"{settings.TRIPAY_MERCHANT_CODE}{merchant_ref}{amount}"
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
            "callback_url": settings.TRIPAY_CALLBACK_URL or "",
            "return_url": "https://t.me/berkahkaryaforexbotbot",
            "expired_time": int(time.time()) + 3600,
            "signature": self._tripay_sign(raw),
        }

        url = f"{settings.TRIPAY_BASE_URL}/transaction/create"
        headers = {
            "Authorization": f"Bearer {settings.TRIPAY_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                result = resp.json()
                self._store_payment(merchant_ref, user_id, username, amount, "vtfx-subscribe")
                return result
        except Exception as exc:
            LOG.error("Tripay create transaction failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def get_tripay_channels(self) -> list:
        """Get available Tripay payment channels."""
        url = f"{settings.TRIPAY_BASE_URL}/merchant/payment-channel"
        headers = {"Authorization": f"Bearer {settings.TRIPAY_API_KEY}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            LOG.error("Tripay get channels failed: %s", exc)
            return []

    async def get_tripay_transaction(self, reference: str) -> dict:
        """Check Tripay transaction status by reference.

        Tripay /transaction/detail uses GET with query params
        ``reference`` + ``signature`` (POST returns HTTP 405).
        """
        import urllib.parse

        sig = self._tripay_sign(settings.TRIPAY_MERCHANT_CODE + reference)
        params = urllib.parse.urlencode(
            {"reference": reference, "signature": sig}
        )
        url = f"{settings.TRIPAY_BASE_URL}/transaction/detail?{params}"
        headers = {"Authorization": f"Bearer {settings.TRIPAY_API_KEY}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            LOG.error("Tripay detail failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # ── Duitku ───────────────────────────────────────────────────

    @staticmethod
    def _duitku_sign(merchant_code: str, amount: int | str, merchant_order_id: str, api_key: str) -> str:  # noqa: E501
        """Duitku signature: MD5(merchantCode + merchantOrderId + amount + apiKey)."""
        raw = f"{merchant_code}{merchant_order_id}{amount}{api_key}"
        return hashlib.md5(raw.encode()).hexdigest()

    async def create_duitku_transaction(
        self,
        user_id: str,
        username: str,
        amount: int,
        payment_method: str = "VC",
        customer_email: str = "",
        customer_phone: str = "",
        product_name: str = "VilonaTradeFX License",
        expiry_minutes: int = 60,
    ) -> dict:
        """Create Duitku payment invoice.

        Args:
            user_id: Telegram user ID.
            username: Telegram username.
            amount: Amount in IDR.
            payment_method: Payment method code (default VC = all available).
            customer_email: Customer email.
            customer_phone: Customer phone.
            product_name: Product description.
            expiry_minutes: Invoice expiry in minutes.

        Returns:
            dict with payment_url and reference, or error.
        """
        merchant_order_id = f"VTFX-D-{user_id}-{int(time.time())}"

        signature = self._duitku_sign(
            settings.DUITKU_MERCHANT_CODE, amount, merchant_order_id, settings.DUITKU_API_KEY
        )

        payload = {
            "merchantCode": settings.DUITKU_MERCHANT_CODE,
            "paymentAmount": amount,
            "merchantOrderId": merchant_order_id,
            "productDetails": product_name,
            "email": customer_email or f"{user_id}@telegram.user",
            "customerVaName": username or f"User{user_id}",
            "phoneNumber": customer_phone or "08123456789",
            "paymentMethod": payment_method,
            "callbackUrl": settings.DUITKU_CALLBACK_URL or "",
            "returnUrl": "https://t.me/berkahkaryaforexbotbot",
            "expiryPeriod": expiry_minutes * 60,
            "signature": signature,
        }

        url = f"{settings.DUITKU_BASE_URL}/api/merchant/v2/inquiry"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result = resp.json()

                if result.get("statusCode") == "00":
                    self._store_payment(merchant_order_id, user_id, username, amount, "duitku")
                    return {
                        "success": True,
                        "reference": result.get("reference"),
                        "merchant_order_id": merchant_order_id,
                        "payment_url": result.get("paymentUrl"),
                        "amount": amount,
                        "expiry": result.get("expiredDate"),
                    }
                return {
                    "success": False,
                    "error": result.get("statusMessage", "Unknown error"),
                    "code": result.get("statusCode"),
                }
        except Exception as exc:
            LOG.error("Duitku create transaction failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def check_duitku_transaction(self, merchant_order_id: str) -> dict:
        """Check Duitku transaction status."""
        signature = self._duitku_sign(
            settings.DUITKU_MERCHANT_CODE, "0", merchant_order_id, settings.DUITKU_API_KEY
        )
        url = f"{settings.DUITKU_BASE_URL}/api/merchant/transactionStatus"
        payload = {
            "merchantCode": settings.DUITKU_MERCHANT_CODE,
            "merchantOrderId": merchant_order_id,
            "signature": signature,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            LOG.error("Duitku check failed: %s", exc)
            return {"success": False, "error": str(exc)}

    @staticmethod
    def verify_duitku_callback(callback_data: dict) -> bool:
        """Verify Duitku callback by checking signature."""
        merchant_code = callback_data.get("merchantCode", "")
        amount = callback_data.get("amount", "0")
        merchant_order_id = callback_data.get("merchantOrderId", "")

        expected = PaymentService._duitku_sign(
            merchant_code, amount, merchant_order_id, settings.DUITKU_API_KEY
        )
        return expected == callback_data.get("signature", "")

    # ── Shared Payment Store ─────────────────────────────────────

    def _load_payments(self) -> dict:
        """Load payment store from JSON file."""
        try:
            if self._store_path.exists():
                with open(self._store_path) as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
        return {"payments": {}}

    def _store_payment(self, merchant_ref: str, user_id: str, username: str, amount: int, product_key: str):  # noqa: E501
        """Store a pending payment record."""
        data = self._load_payments()
        data["payments"][merchant_ref] = {
            "user_id": str(user_id),
            "username": username,
            "amount": amount,
            "product_key": product_key,
            "status": "PENDING",
            "created_at": datetime.now(UTC).isoformat(),
            "license_key": None,
        }
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._store_path, "w") as f:
            json.dump(data, f, indent=2)

    def mark_payment_done(self, merchant_ref: str, license_key: str) -> tuple[bool, dict | None]:
        """Mark a payment as done and store the license key.

        Returns:
            (True, payment_record) if found and updated, (False, None) otherwise.
        """
        data = self._load_payments()
        if merchant_ref in data["payments"]:
            data["payments"][merchant_ref]["status"] = "PAID"
            data["payments"][merchant_ref]["license_key"] = license_key
            data["payments"][merchant_ref]["paid_at"] = datetime.now(UTC).isoformat()
            with open(self._store_path, "w") as f:
                json.dump(data, f, indent=2)
            return True, data["payments"][merchant_ref]
        return False, None


async def create_tripay_payment(
    chat_id: str,
    username: str,
    tier: str = "pro",
    method: str = "QRIS",
    amount: int | None = None,
) -> dict:
    """Convenience wrapper — create a Tripay subscription payment.

    Mirrors the legacy members.payment.create_tripay_payment signature
    but uses the unified PaymentService backend.
    """
    if amount is None:
        # Look up tier price; default to pro
        tier_prices = {"pro": 50000, "elite": 150000, "lifetime": 500000}
        amount = tier_prices.get(tier, 50000)
    if amount < 50000:
        return {"error": "Minimum subscribe Rp50.000"}
    svc = PaymentService()
    return await svc.create_tripay_transaction(
        user_id=chat_id,
        username=username,
        amount=amount,
        method=method,
        tier=tier,
    )
