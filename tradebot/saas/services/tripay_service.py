"""TriPay payment gateway integration for Indonesian market"""
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from tradebot.config import get_settings
from tradebot.exceptions import PaymentError
from tradebot.logging import get_logger
from tradebot.saas.repositories.payment_repo import PaymentRepository
from tradebot.saas.repositories.user_repo import UserRepository

logger = get_logger(__name__)
settings = get_settings()


class TriPayService:
    """TriPay payment gateway for Indonesian Rupiah (IDR) transactions"""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._payment_repo = PaymentRepository(db)
        self._user_repo = UserRepository(db)

    @staticmethod
    def _generate_signature(payload: str) -> str:
        """Generate HMAC-SHA256 signature for TriPay API"""
        return hmac.new(
            settings.TRIPAY_PRIVATE_KEY.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def verify_callback(callback_data: str, callback_signature: str) -> bool:
        """Verify TriPay webhook callback signature"""
        expected = TriPayService._generate_signature(callback_data)
        return hmac.compare_digest(expected, callback_signature)

    async def create_transaction(
        self,
        user_id: int,
        amount: int,
        product_name: str,
        method: str = "QRIS2",
        customer_email: str = "",
        customer_phone: str = "",
    ) -> dict[str, Any]:
        """Create TriPay closed payment transaction

        Args:
            user_id: User ID
            amount: Amount in IDR
            product_name: Product description
            method: Payment method (QRIS2, BRIVA, etc.)
            customer_email: Customer email
            customer_phone: Customer phone

        Returns:
            Transaction details with payment URL
        """
        user = self._user_repo.get_by_id(user_id)
        if not user:
            raise PaymentError(f"User not found: {user_id}")

        merchant_ref = f"TB-{user_id}-{int(time.time())}"
        raw = f"{settings.TRIPAY_MERCHANT_CODE}{merchant_ref}{amount}"

        payload = {
            "method": method,
            "merchant_ref": merchant_ref,
            "amount": amount,
            "customer_name": f"{user.first_name} {user.last_name}".strip() or f"User{user_id}",
            "customer_email": customer_email or user.email,
            "customer_phone": customer_phone or "08123456789",
            "order_items": [
                {"name": product_name, "price": amount, "quantity": 1}
            ],
            "callback_url": settings.TRIPAY_CALLBACK_URL,
            "return_url": settings.APP_URL + "/payments/success",
            "expired_time": int(time.time()) + 3600,
            "signature": self._generate_signature(raw),
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

                if not result.get("success"):
                    raise PaymentError(
                        f"TriPay transaction failed: {result.get('message', 'Unknown error')}"
                    )

                data = result["data"]
                logger.info(
                    "TriPay transaction created: user=%d ref=%s amount=%d",
                    user_id, merchant_ref, amount,
                )

                return {
                    "success": True,
                    "reference": data.get("reference"),
                    "merchant_ref": merchant_ref,
                    "payment_url": data.get("checkout_url"),
                    "qr_string": data.get("qr_string"),
                    "amount": amount,
                    "expired_at": data.get("expired_at"),
                    "instructions": data.get("instructions", []),
                }

        except httpx.HTTPError as exc:
            logger.error("TriPay HTTP error: %s", exc)
            raise PaymentError(f"Payment gateway error: {exc}") from exc

    async def get_transaction(self, reference: str) -> dict[str, Any]:
        """Get transaction status by reference"""
        raw = f"{reference}{settings.TRIPAY_MERCHANT_CODE}"
        payload = {
            "merchant_ref": reference,
            "signature": self._generate_signature(raw),
        }

        url = f"{settings.TRIPAY_BASE_URL}/transaction/detail"
        headers = {
            "Authorization": f"Bearer {settings.TRIPAY_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                result = resp.json()

                if not result.get("success"):
                    raise PaymentError(
                        f"Failed to get transaction: {result.get('message', 'Unknown error')}"
                    )

                return result["data"]

        except httpx.HTTPError as exc:
            logger.error("TriPay detail error: %s", exc)
            raise PaymentError(f"Failed to check transaction: {exc}") from exc

    async def get_payment_channels(self) -> list[dict[str, Any]]:
        """Get available payment channels"""
        url = f"{settings.TRIPAY_BASE_URL}/merchant/payment-channel"
        headers = {"Authorization": f"Bearer {settings.TRIPAY_API_KEY}"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                result = resp.json()

                if not result.get("success"):
                    return []

                return result.get("data", [])

        except httpx.HTTPError as exc:
            logger.error("TriPay channels error: %s", exc)
            return []

    async def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process TriPay webhook callback"""
        callback_data = payload.get("callback_data", "")
        callback_signature = payload.get("callback_signature", "")

        if not self.verify_callback(callback_data, callback_signature):
            raise PaymentError("Invalid webhook signature")

        import json
        data = json.loads(callback_data)

        event = data.get("event")
        merchant_ref = data.get("merchant_ref")
        status = data.get("status")

        logger.info(
            "TriPay webhook: event=%s ref=%s status=%s",
            event, merchant_ref, status,
        )

        if event == "payment" and status == "PAID":
            await self._handle_payment_success(data)

        return {"success": True, "event": event}

    async def _handle_payment_success(self, data: dict[str, Any]) -> None:
        """Handle successful payment"""
        merchant_ref = data.get("merchant_ref")
        amount = data.get("amount", 0)

        # Extract user_id from merchant_ref (format: TB-{user_id}-{timestamp})
        parts = merchant_ref.split("-")
        if len(parts) < 2:
            logger.error("Invalid merchant_ref format: %s", merchant_ref)
            return

        try:
            user_id = int(parts[1])
        except ValueError:
            logger.error("Invalid user_id in merchant_ref: %s", merchant_ref)
            return

        # Determine product type from amount
        from app.schemas.subscription import DONATION_TIERS
        donation_tier = None
        credits = 0

        for tier, info in DONATION_TIERS.items():
            if info["amount_idr"] == amount:
                donation_tier = tier
                credits = info["credits"]
                break

        if donation_tier:
            # Donation flow
            self._payment_repo.create_payment(
                user_id=user_id,
                amount_idr=amount,
                tier=f"donation_{donation_tier}",
                billing_period="one_time",
                status="completed",
            )

            if credits > 0:
                from app.services.subscription_service import SubscriptionService
                sub_svc = SubscriptionService(self._db)
                sub_svc.credit_bonus_generations(user_id, credits)
                logger.info(
                    "Donation credited: user=%d tier=%s credits=%d",
                    user_id, donation_tier, credits,
                )
        else:
            # Subscription flow
            logger.info("Payment received: user=%d amount=%d", user_id, amount)
