"""Onboarding routes — status, step completion, skip, feature gating"""
from fastapi import APIRouter

from app.api.deps import CurrentUser, DBSession
from app.schemas.common import APIResponse
from app.schemas.onboarding import (
    CompleteStepRequest,
    SkipStepRequest,
    ConfigureRiskRequest,
    DismissOnboardingRequest,
)
from app.services.onboarding_service import OnboardingService

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


@router.get("/status", response_model=APIResponse)
def get_onboarding_status(user: CurrentUser, db: DBSession):
    """
    Get current onboarding status — progress %, current step, all steps.
    Frontend should call this on every page-load and show the onboarding
    checklist until is_completed or is_dismissed.
    """
    svc = OnboardingService(db)
    status = svc.get_status(user.id)
    return APIResponse.ok(data=status.model_dump())


@router.post("/complete-step", response_model=APIResponse)
def complete_onboarding_step(payload: CompleteStepRequest, user: CurrentUser, db: DBSession):
    """
    Mark an onboarding step as completed.
    Some steps are auto-completed (e.g. verify_email, connect_platform)
    but the client can also explicitly mark them.
    """
    svc = OnboardingService(db)
    status = svc.complete_step(user.id, payload.step)
    return APIResponse.ok(data=status.model_dump(), message=f"Step '{payload.step}' completed.")


@router.post("/skip-step", response_model=APIResponse)
def skip_onboarding_step(payload: SkipStepRequest, user: CurrentUser, db: DBSession):
    """
    Skip an optional onboarding step (e.g. connect_platform, configure_risk).
    Non-skippable steps (welcome, verify_email, choose_plan) will be rejected.
    """
    svc = OnboardingService(db)
    status = svc.skip_step(user.id, payload.step, payload.reason)
    return APIResponse.ok(data=status.model_dump(), message=f"Step '{payload.step}' skipped.")


@router.post("/configure-risk", response_model=APIResponse)
def configure_risk_preferences(payload: ConfigureRiskRequest, user: CurrentUser, db: DBSession):
    """
    Save risk preferences during onboarding (step: configure_risk).
    Automatically marks the step as completed.
    """
    svc = OnboardingService(db)
    status = svc.configure_risk(user.id, payload)
    return APIResponse.ok(data=status.model_dump(), message="Risk preferences saved.")


@router.post("/dismiss", response_model=APIResponse)
def dismiss_onboarding(payload: DismissOnboardingRequest, user: CurrentUser, db: DBSession):
    """
    Dismiss the onboarding flow. User can reopen it later.
    Frontend should stop showing the onboarding checklist.
    """
    svc = OnboardingService(db)
    status = svc.dismiss(user.id)
    return APIResponse.ok(data=status.model_dump(), message="Onboarding dismissed.")


@router.post("/reopen", response_model=APIResponse)
def reopen_onboarding(user: CurrentUser, db: DBSession):
    """Reopen the onboarding flow if previously dismissed."""
    svc = OnboardingService(db)
    status = svc.reopen(user.id)
    return APIResponse.ok(data=status.model_dump(), message="Onboarding reopened.")


@router.get("/gate/{feature}", response_model=APIResponse)
def check_feature_gate(feature: str, user: CurrentUser, db: DBSession):
    """
    Check if user can access a specific feature.
    Returns allowed=true/false + missing prerequisite steps.
    
    Features: signals, auto_trading, manual_trade, upgrade_plan, api_access
    
    Frontend should call this before navigating to a gated feature page.
    If allowed=false, show a modal with the missing steps and a CTA to
    complete them.
    """
    svc = OnboardingService(db)
    gate = svc.check_feature_access(user.id, feature)
    return APIResponse.ok(data=gate.model_dump())
