"""Subscription schemas"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class SubscriptionPlan(BaseModel):
    """Available subscription plan"""

    tier: str
    name: str
    description: str
    price_monthly_idr: int
    price_annual_idr: int
    features: list[str]
    daily_signal_limit: int
    daily_trade_limit: int
    max_concurrent_trades: int
    has_auto_trading: bool
    has_api_access: bool
    has_priority_support: bool
    generation_credits: int = 3


class SubscriptionResponse(BaseModel):
    """Current subscription status"""

    tier: str
    is_active: bool
    auto_renew: bool
    daily_signal_limit: int
    daily_trade_limit: int
    max_concurrent_trades: int
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    trial_ends_at: Optional[datetime]
    cancelled_at: Optional[datetime]

    class Config:
        from_attributes = True


class SubscriptionUpgradeRequest(BaseModel):
    """Upgrade subscription request"""

    target_tier: str  # starter, pro, enterprise
    billing_period: str = "monthly"  # monthly, annual


class CheckoutSessionRequest(BaseModel):
    """Create TriPay checkout session request"""

    tier: str  # starter, pro, enterprise
    billing_period: str = "monthly"  # monthly, annual
    payment_method: str = "QRIS2"  # QRIS2, BRIVA, etc.


class CheckoutSessionResponse(BaseModel):
    """TriPay checkout session response"""

    payment_url: str
    merchant_ref: str
    reference: str
    amount_idr: int
    expired_at: int
    qr_string: Optional[str] = None
    instructions: list[dict] = []


# ── Donation / Tip Jar ─────────────────────────────────────────────

DONATION_TIERS = {
    "pro": {"amount_idr": 50000, "label": "⭐ PRO (Rp 50K/month)"}
    "elite": {"amount_idr": 150000, "label": "👑 ELITE (Rp 150K/month)"}
    "lifetime": {"amount_idr": 500000, "label": "💎 LIFETIME (Rp 500K once)"}
    
}


class DonationRequest(BaseModel):
    """One-time donation that grants bonus generation credits"""

    tier: str = "coffee"
    payment_method: str = "QRIS2"

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        if v not in DONATION_TIERS:
            raise ValueError(
                f"Unknown donation tier. Choose: {', '.join(DONATION_TIERS.keys())}"
            )
        return v


class DonationResponse(BaseModel):
    """TriPay checkout session for donation"""

    payment_url: str
    merchant_ref: str
    reference: str
    credits_to_receive: int
    amount_idr: int
    qr_string: Optional[str] = None
    instructions: list[dict] = []


class GenerationQuotaStatus(BaseModel):
    """Generation quota included in subscription response"""

    total_credits: int
    used_credits: int
    remaining_credits: int
    bonus_credits: int
    is_unlimited: bool
