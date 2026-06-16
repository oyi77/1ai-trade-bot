"""Subscription service — plan management, TriPay checkout, upgrades/downgrades"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from tradebot.config import get_settings
from tradebot.exceptions import SubscriptionError
from tradebot.logutils import get_logger
from tradebot.models.user import SubscriptionTier
from tradebot.saas.repositories.user_repo import UserRepository
from tradebot.saas.services.tripay_service import TriPayService
from tradebot.saas.schemas.subscription import (
    SubscriptionPlan,
    SubscriptionResponse,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
)

logger = get_logger(__name__)
settings = get_settings()

# ── Plan catalog ─────────────────────────────────────────────────────

PLAN_CATALOG: dict[str, SubscriptionPlan] = {
    "free": SubscriptionPlan(
        tier="free", name="Free", description="Get started with basic signals",
        price_monthly_idr=0, price_annual_idr=0,
        features=[
            "5 free signals/day", "3 signal generations (lifetime)",
            "Basic analysis", "Email support",
        ],
        daily_signal_limit=5, daily_trade_limit=0, max_concurrent_trades=0,
        has_auto_trading=False, has_api_access=False, has_priority_support=False,
        generation_credits=3,
    ),
    "starter": SubscriptionPlan(
        tier="starter", name="Starter", description="For individual traders",
        price_monthly_idr=299000, price_annual_idr=2990000,
        features=[
            "25 signals/day", "Unlimited signal generations",
            "Advanced analysis", "3 concurrent trades",
            "Demo auto-trading", "Email + chat support",
        ],
        daily_signal_limit=25, daily_trade_limit=10, max_concurrent_trades=3,
        has_auto_trading=True, has_api_access=False, has_priority_support=False,
        generation_credits=9999,
    ),
    "pro": SubscriptionPlan(
        tier="pro", name="Pro", description="For serious traders",
        price_monthly_idr=749000, price_annual_idr=7490000,
        features=[
            "Unlimited signals", "Unlimited generations", "All indicators",
            "10 concurrent trades", "Live auto-trading",
            "API access", "Priority support",
        ],
        daily_signal_limit=999, daily_trade_limit=50, max_concurrent_trades=10,
        has_auto_trading=True, has_api_access=True, has_priority_support=True,
        generation_credits=9999,
    ),
    "enterprise": SubscriptionPlan(
        tier="enterprise", name="Enterprise", description="For teams & funds",
        price_monthly_idr=2249000, price_annual_idr=22490000,
        features=[
            "Unlimited everything", "Custom indicators", "Unlimited trades",
            "White-label", "Dedicated support", "SLA",
        ],
        daily_signal_limit=9999, daily_trade_limit=999, max_concurrent_trades=100,
        has_auto_trading=True, has_api_access=True, has_priority_support=True,
        generation_credits=9999,
    ),
}

TIER_ORDER = ["free", "starter", "pro", "enterprise"]


class SubscriptionService:
    """Subscription business logic"""

    def __init__(self, db: Session) -> None:
        self._user_repo = UserRepository(db)
        self._tripay = TriPayService(db)

    # ── Plan catalog ───────────────────────────────────────────────

    @staticmethod
    def list_plans() -> list[SubscriptionPlan]:
        return list(PLAN_CATALOG.values())

    @staticmethod
    def get_plan(tier: str) -> SubscriptionPlan:
        plan = PLAN_CATALOG.get(tier)
        if not plan:
            raise SubscriptionError(f"Unknown plan tier: {tier}")
        return plan

    # ── Current subscription ───────────────────────────────────────

    def get_subscription(self, user_id: int) -> SubscriptionResponse:
        sub = self._user_repo.get_subscription(user_id)
        return SubscriptionResponse.model_validate(sub)

    # ── TriPay checkout ────────────────────────────────────────────

    async def create_checkout_session(
        self, user_id: int, payload: CheckoutSessionRequest
    ) -> CheckoutSessionResponse:
        """Create a TriPay payment session for plan purchase."""
        plan = self.get_plan(payload.tier)
        if plan.price_monthly_idr == 0:
            raise SubscriptionError("Free plan does not require checkout")

        amount = (
            plan.price_monthly_idr if payload.billing_period == "monthly"
            else plan.price_annual_idr
        )

        product_name = f"Trading Bot {plan.name} - {payload.billing_period.capitalize()}"

        result = await self._tripay.create_transaction(
            user_id=user_id,
            amount=amount,
            product_name=product_name,
            method=payload.payment_method,
        )

        logger.info(
            "Checkout session created: user_id=%d tier=%s amount=%d",
            user_id, payload.tier, amount,
        )

        return CheckoutSessionResponse(
            payment_url=result["payment_url"],
            merchant_ref=result["merchant_ref"],
            reference=result["reference"],
            amount_idr=amount,
            expired_at=result["expired_at"],
            qr_string=result.get("qr_string"),
            instructions=result.get("instructions", []),
        )

    # ── Plan activation (called from webhook) ──────────────────────

    def activate_plan(
        self, user_id: int, tier: str,
        period_start: datetime, period_end: datetime
    ) -> None:
        """Activate a paid plan after successful TriPay payment."""
        plan = self.get_plan(tier)
        self._user_repo.update_subscription(
            user_id,
            tier=SubscriptionTier(tier),
            is_active=True,
            daily_signal_limit=plan.daily_signal_limit,
            daily_trade_limit=plan.daily_trade_limit,
            max_concurrent_trades=plan.max_concurrent_trades,
            started_at=period_start,
            current_period_start=period_start,
            current_period_end=period_end,
        )
        logger.info("Plan activated: user_id=%d tier=%s", user_id, tier)

    def cancel_subscription(self, user_id: int) -> None:
        """Cancel subscription at end of current period."""
        self._user_repo.update_subscription(
            user_id,
            auto_renew=False,
            cancelled_at=datetime.now(timezone.utc),
        )
        logger.info("Subscription cancelled: user_id=%d", user_id)

    def downgrade_to_free(self, user_id: int) -> None:
        """Revert user to free plan (called when subscription expires)."""
        free_plan = PLAN_CATALOG["free"]
        self._user_repo.update_subscription(
            user_id,
            tier=SubscriptionTier.FREE,
            is_active=True,
            daily_signal_limit=free_plan.daily_signal_limit,
            daily_trade_limit=free_plan.daily_trade_limit,
            max_concurrent_trades=free_plan.max_concurrent_trades,
            current_period_start=None,
            current_period_end=None,
        )
        logger.info("Downgraded to free: user_id=%d", user_id)

    # ── Upgrade validation ─────────────────────────────────────────

    def validate_upgrade(self, user_id: int, target_tier: str) -> bool:
        """Verify upgrade is valid (higher tier than current)."""
        sub = self._user_repo.get_subscription(user_id)
        current_idx = TIER_ORDER.index(sub.tier.value) if sub.tier.value in TIER_ORDER else 0
        target_idx = TIER_ORDER.index(target_tier) if target_tier in TIER_ORDER else -1

        if target_idx <= current_idx:
            raise SubscriptionError(
                f"Cannot upgrade from {sub.tier.value} to {target_tier}. "
                f"Choose a higher tier."
            )
        return True

    # ── Free-trial ─────────────────────────────────────────────────

    def start_free_trial(self, user_id: int, tier: str = "starter",
                         trial_days: int = 7) -> None:
        """Start a free trial of a paid tier."""
        plan = self.get_plan(tier)
        now = datetime.now(timezone.utc)
        self._user_repo.update_subscription(
            user_id,
            tier=SubscriptionTier(tier),
            is_active=True,
            daily_signal_limit=plan.daily_signal_limit,
            daily_trade_limit=plan.daily_trade_limit,
            max_concurrent_trades=plan.max_concurrent_trades,
            started_at=now,
            trial_ends_at=now + timedelta(days=trial_days),
            current_period_start=now,
            current_period_end=now + timedelta(days=trial_days),
        )
        logger.info("Free trial started: user_id=%d tier=%s days=%d", user_id, tier, trial_days)

    # ── Donation / Tip Jar ────────────────────────────────────────

    async def create_donation_checkout(
        self, user_id: int, tier: str, payment_method: str = "QRIS2"
    ) -> dict:
        """Create TriPay payment session for donation → bonus credits."""
        from tradebot.saas.schemas.subscription import DONATION_TIERS

        if tier not in DONATION_TIERS:
            raise SubscriptionError(f"Unknown donation tier: {tier}")

        donation_info = DONATION_TIERS[tier]
        credits = donation_info["credits"]
        amount = donation_info["amount_idr"]

        result = await self._tripay.create_transaction(
            user_id=user_id,
            amount=amount,
            product_name=f"Support Trading Bot - {donation_info['label']}",
            method=payment_method,
        )

        logger.info("Donation checkout created: user=%d tier=%s", user_id, tier)
        return {
            "payment_url": result["payment_url"],
            "merchant_ref": result["merchant_ref"],
            "reference": result["reference"],
            "credits_to_receive": credits,
            "amount_idr": amount,
            "qr_string": result.get("qr_string"),
            "instructions": result.get("instructions", []),
        }

    def credit_bonus_generations(self, user_id: int, credits: int) -> None:
        """Grant bonus generation credits from donation or other sources."""
        sub = self._user_repo.get_subscription(user_id)
        self._user_repo.update_subscription(
            user_id, bonus_credits=sub.bonus_credits + credits
        )
        logger.info("Bonus credits awarded: user=%d credits=%d", user_id, credits)
