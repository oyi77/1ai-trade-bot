"""Onboarding schemas — request/response shapes for every step"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Responses ────────────────────────────────────────────────────────

class OnboardingStepInfo(BaseModel):
    """Single step in the onboarding checklist"""

    step: str
    label: str
    description: str
    is_completed: bool
    is_current: bool
    is_skippable: bool
    completed_at: Optional[datetime] = None


class OnboardingStatusResponse(BaseModel):
    """Full onboarding state returned to client"""

    is_completed: bool
    is_dismissed: bool
    progress_percent: int
    current_step: str
    steps: list[OnboardingStepInfo]
    next_action_url: str          # deep-link the UI should navigate to
    next_action_label: str        # CTA text, e.g. "Verify your email"


class FeatureGateResponse(BaseModel):
    """Returned when user tries a feature they haven't unlocked yet"""

    allowed: bool
    feature: str
    missing_steps: list[OnboardingStepInfo]
    redirect_step: str            # which onboarding step to send them to
    message: str                  # user-friendly explanation


# ── Requests ─────────────────────────────────────────────────────────

class CompleteStepRequest(BaseModel):
    """Mark a step as done (client calls after user finishes it)"""

    step: str  # must match OnboardingStep enum value


class SkipStepRequest(BaseModel):
    """User explicitly skips an optional step"""

    step: str
    reason: Optional[str] = None  # optional feedback


class ConfigureRiskRequest(BaseModel):
    """Risk-configuration payload sent during the 'configure_risk' step"""

    risk_per_trade_percent: float = Field(ge=0.1, le=10.0, default=1.0)
    max_daily_loss_percent: float = Field(ge=1.0, le=20.0, default=5.0)
    default_stop_loss_percent: float = Field(ge=0.5, le=10.0, default=2.0)
    default_take_profit_percent: float = Field(ge=0.5, le=30.0, default=4.0)


class DismissOnboardingRequest(BaseModel):
    """User dismisses the entire onboarding flow (can be re-opened later)"""

    confirm: bool = True


# Step metadata for the frontend
STEP_META: dict[str, dict] = {
    "welcome": {
        "label": "Welcome",
        "description": "Get started with your trading bot",
        "is_skippable": False,
        "action_url": "/onboarding/welcome",
        "action_label": "Let's go!",
    },
    "verify_email": {
        "label": "Verify Email",
        "description": "Confirm your email address to secure your account",
        "is_skippable": False,
        "action_url": "/verify-email",
        "action_label": "Check your inbox",
    },
    "choose_plan": {
        "label": "Choose a Plan",
        "description": "Pick a plan — start free with 5 signals/day or unlock full power",
        "is_skippable": False,
        "action_url": "/pricing",
        "action_label": "View plans",
    },
    "connect_platform": {
        "label": "Connect Exchange",
        "description": "Link your Binance, Bybit, OKX, or KuCoin account",
        "is_skippable": True,
        "action_url": "/settings/platforms",
        "action_label": "Connect exchange",
    },
    "configure_risk": {
        "label": "Configure Risk",
        "description": "Set your risk tolerance, stop-loss, and take-profit defaults",
        "is_skippable": True,
        "action_url": "/settings/risk",
        "action_label": "Set risk preferences",
    },
    "first_signal": {
        "label": "View First Signal",
        "description": "Check out your first live trading signal",
        "is_skippable": True,
        "action_url": "/signals",
        "action_label": "Browse signals",
    },
    "first_trade": {
        "label": "Execute First Trade",
        "description": "Place your first trade — try demo mode risk-free!",
        "is_skippable": True,
        "action_url": "/trading",
        "action_label": "Start trading",
    },
    "completed": {
        "label": "All Done!",
        "description": "You're fully set up — happy trading!",
        "is_skippable": False,
        "action_url": "/dashboard",
        "action_label": "Go to dashboard",
    },
}
