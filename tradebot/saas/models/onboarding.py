"""Onboarding state model — tracks each user's step-by-step setup progress"""
from datetime import datetime, timezone
from typing import Optional
import enum

from sqlalchemy import Boolean, DateTime, String, Enum, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OnboardingStep(str, enum.Enum):
    """Each discrete step a user must complete"""
    WELCOME = "welcome"                     # Saw the welcome screen
    VERIFY_EMAIL = "verify_email"           # Confirmed email address
    CHOOSE_PLAN = "choose_plan"             # Picked free-trial or paid plan
    CONNECT_PLATFORM = "connect_platform"   # Connected ≥1 exchange API key
    CONFIGURE_RISK = "configure_risk"       # Set stop-loss / risk preferences
    FIRST_SIGNAL = "first_signal"           # Viewed first trading signal
    FIRST_TRADE = "first_trade"             # Executed first trade (demo or live)
    COMPLETED = "completed"                 # All steps finished


# Canonical order — used to compute "next step"
ONBOARDING_STEP_ORDER: list[OnboardingStep] = [
    OnboardingStep.WELCOME,
    OnboardingStep.VERIFY_EMAIL,
    OnboardingStep.CHOOSE_PLAN,
    OnboardingStep.CONNECT_PLATFORM,
    OnboardingStep.CONFIGURE_RISK,
    OnboardingStep.FIRST_SIGNAL,
    OnboardingStep.FIRST_TRADE,
    OnboardingStep.COMPLETED,
]

# Map from feature-requiring step → which step needs to be done first
FEATURE_PREREQUISITES: dict[str, list[OnboardingStep]] = {
    "signals":       [OnboardingStep.VERIFY_EMAIL, OnboardingStep.CHOOSE_PLAN],
    "auto_trading":  [OnboardingStep.VERIFY_EMAIL, OnboardingStep.CHOOSE_PLAN, OnboardingStep.CONNECT_PLATFORM, OnboardingStep.CONFIGURE_RISK],
    "manual_trade":  [OnboardingStep.VERIFY_EMAIL, OnboardingStep.CHOOSE_PLAN, OnboardingStep.CONNECT_PLATFORM],
    "upgrade_plan":  [OnboardingStep.VERIFY_EMAIL],
    "api_access":    [OnboardingStep.VERIFY_EMAIL, OnboardingStep.CHOOSE_PLAN, OnboardingStep.CONNECT_PLATFORM],
}


class UserOnboarding(Base):
    """Persistent onboarding state per user"""

    __tablename__ = "user_onboardings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    # Per-step completion timestamps (null = not yet done)
    welcome_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    verify_email_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    choose_plan_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    connect_platform_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    configure_risk_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    first_signal_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    first_trade_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Current step the user should see next
    current_step: Mapped[OnboardingStep] = mapped_column(
        Enum(OnboardingStep), default=OnboardingStep.WELCOME
    )

    # Risk configuration set during onboarding step
    risk_per_trade_percent: Mapped[float] = mapped_column(default=1.0)      # % of balance per trade
    max_daily_loss_percent: Mapped[float] = mapped_column(default=5.0)      # max daily drawdown %
    default_stop_loss_percent: Mapped[float] = mapped_column(default=2.0)   # default SL%
    default_take_profit_percent: Mapped[float] = mapped_column(default=4.0) # default TP%
    preferred_platforms: Mapped[Optional[dict]] = mapped_column(JSON)       # ["binance","bybit"]

    # Skips tracking — user can skip optional steps
    skipped_steps: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)

    # Whether the entire flow was dismissed
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationship
    user: Mapped["User"] = relationship()

    # ── helper methods ────────────────────────────────────────────────

    def completed_steps(self) -> list[OnboardingStep]:
        """Return list of steps the user has completed."""
        done: list[OnboardingStep] = []
        for step in ONBOARDING_STEP_ORDER:
            col = f"{step.value}_completed_at"
            if getattr(self, col, None) is not None:
                done.append(step)
        return done

    def progress_percent(self) -> int:
        """0-100 progress through the onboarding checklist."""
        total = len(ONBOARDING_STEP_ORDER) - 1  # exclude COMPLETED itself
        done = len([s for s in self.completed_steps() if s != OnboardingStep.COMPLETED])
        return int(done / total * 100) if total else 100

    def is_step_complete(self, step: OnboardingStep) -> bool:
        col = f"{step.value}_completed_at"
        return getattr(self, col, None) is not None

    def __repr__(self) -> str:
        return f"<UserOnboarding user_id={self.user_id} step={self.current_step}>"
