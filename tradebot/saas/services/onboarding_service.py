"""Onboarding service — drives the step-by-step setup + feature gating"""
from sqlalchemy.orm import Session

from tradebot.logging import get_logger
from tradebot.saas.models.onboarding import (
    OnboardingStep,
    ONBOARDING_STEP_ORDER,
    FEATURE_PREREQUISITES,
)
from tradebot.saas.repositories.onboarding_repo import OnboardingRepository
from tradebot.saas.schemas.onboarding import (
    OnboardingStatusResponse,
    OnboardingStepInfo,
    FeatureGateResponse,
    ConfigureRiskRequest,
    STEP_META,
)

logger = get_logger(__name__)


class OnboardingService:
    """Onboarding business logic — status, step completion, feature gating"""

    def __init__(self, db: Session) -> None:
        self._repo = OnboardingRepository(db)

    # ── Read status ────────────────────────────────────────────────

    def get_status(self, user_id: int) -> OnboardingStatusResponse:
        """Build the full onboarding status payload for the frontend."""
        ob = self._repo.get_by_user(user_id)
        steps = self._build_step_list(ob)

        current_meta = STEP_META.get(ob.current_step.value, STEP_META["completed"])

        return OnboardingStatusResponse(
            is_completed=ob.current_step == OnboardingStep.COMPLETED,
            is_dismissed=ob.is_dismissed,
            progress_percent=ob.progress_percent(),
            current_step=ob.current_step.value,
            steps=steps,
            next_action_url=current_meta["action_url"],
            next_action_label=current_meta["action_label"],
        )

    # ── Step mutations ─────────────────────────────────────────────

    def complete_step(self, user_id: int, step_name: str) -> OnboardingStatusResponse:
        """Mark a step as completed and return updated status."""
        step = OnboardingStep(step_name)
        self._repo.complete_step(user_id, step)
        logger.info("Onboarding step completed: user_id=%d step=%s", user_id, step_name)
        return self.get_status(user_id)

    def skip_step(self, user_id: int, step_name: str,
                  reason: str | None = None) -> OnboardingStatusResponse:
        """Skip an optional step and advance."""
        step = OnboardingStep(step_name)
        meta = STEP_META.get(step_name, {})
        if not meta.get("is_skippable", False):
            from tradebot.exceptions import ValidationError
            raise ValidationError(f"Step '{step_name}' cannot be skipped")

        self._repo.skip_step(user_id, step, reason)
        logger.info("Onboarding step skipped: user_id=%d step=%s", user_id, step_name)
        return self.get_status(user_id)

    def configure_risk(self, user_id: int,
                       payload: ConfigureRiskRequest) -> OnboardingStatusResponse:
        """Save risk preferences and complete the configure_risk step."""
        self._repo.save_risk_config(
            user_id,
            risk_per_trade_percent=payload.risk_per_trade_percent,
            max_daily_loss_percent=payload.max_daily_loss_percent,
            default_stop_loss_percent=payload.default_stop_loss_percent,
            default_take_profit_percent=payload.default_take_profit_percent,
        )
        self._repo.complete_step(user_id, OnboardingStep.CONFIGURE_RISK)
        logger.info("Risk configured during onboarding: user_id=%d", user_id)
        return self.get_status(user_id)

    def dismiss(self, user_id: int) -> OnboardingStatusResponse:
        """Dismiss the onboarding flow (can be reopened)."""
        self._repo.dismiss(user_id)
        return self.get_status(user_id)

    def reopen(self, user_id: int) -> OnboardingStatusResponse:
        """Reopen dismissed onboarding."""
        self._repo.reopen(user_id)
        return self.get_status(user_id)

    # ── Feature gating ─────────────────────────────────────────────

    def check_feature_access(self, user_id: int,
                             feature: str) -> FeatureGateResponse:
        """
        Check if user has completed all prerequisites for a feature.
        Called by other services/routes BEFORE allowing access.
        Returns a gate response with missing steps and redirect info.
        """
        if feature not in FEATURE_PREREQUISITES:
            return FeatureGateResponse(
                allowed=True,
                feature=feature,
                missing_steps=[],
                redirect_step="",
                message="Feature is available",
            )

        missing = self._repo.missing_steps_for_feature(user_id, feature)
        if not missing:
            return FeatureGateResponse(
                allowed=True,
                feature=feature,
                missing_steps=[],
                redirect_step="",
                message="Feature is available",
            )

        # Build step info for missing steps
        ob = self._repo.get_by_user(user_id)
        missing_infos = [self._step_to_info(ob, step) for step in missing]
        first_missing = missing[0]
        first_meta = STEP_META.get(first_missing.value, {})

        human_labels = {
            "signals": "view trading signals",
            "auto_trading": "enable auto-trading",
            "manual_trade": "execute trades",
            "upgrade_plan": "upgrade your plan",
            "api_access": "use API access",
        }

        return FeatureGateResponse(
            allowed=False,
            feature=feature,
            missing_steps=missing_infos,
            redirect_step=first_missing.value,
            message=(
                f"To {human_labels.get(feature, 'use this feature')}, "
                f"please complete: {first_meta.get('label', first_missing.value)}"
            ),
        )

    # ── Auto-complete hooks (called by other services) ─────────────

    def auto_complete_connect_platform(self, user_id: int) -> None:
        """Called when user successfully connects a trading platform."""
        self._repo.complete_step(user_id, OnboardingStep.CONNECT_PLATFORM)

    def auto_complete_choose_plan(self, user_id: int) -> None:
        """Called when user selects a plan (incl. free)."""
        self._repo.complete_step(user_id, OnboardingStep.CHOOSE_PLAN)

    def auto_complete_first_signal(self, user_id: int) -> None:
        """Called when user views their first signal."""
        self._repo.complete_step(user_id, OnboardingStep.FIRST_SIGNAL)

    def auto_complete_first_trade(self, user_id: int) -> None:
        """Called when user executes their first trade."""
        self._repo.complete_step(user_id, OnboardingStep.FIRST_TRADE)

    # ── Private helpers ────────────────────────────────────────────

    @staticmethod
    def _build_step_list(ob) -> list[OnboardingStepInfo]:
        """Build the full ordered list of steps with completion info."""
        steps: list[OnboardingStepInfo] = []
        for step in ONBOARDING_STEP_ORDER:
            steps.append(OnboardingService._step_to_info(ob, step))
        return steps

    @staticmethod
    def _step_to_info(ob, step: OnboardingStep) -> OnboardingStepInfo:
        meta = STEP_META.get(step.value, {})
        col = f"{step.value}_completed_at"
        completed_at = getattr(ob, col, None)
        return OnboardingStepInfo(
            step=step.value,
            label=meta.get("label", step.value),
            description=meta.get("description", ""),
            is_completed=completed_at is not None,
            is_current=(ob.current_step == step),
            is_skippable=meta.get("is_skippable", False),
            completed_at=completed_at,
        )
