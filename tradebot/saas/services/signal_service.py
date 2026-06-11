"""Signal service — listing, generating, scanning, quota enforcement"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from tradebot.analytics import (
    MarketDataError,
    analyze,
    fetch_candles,
    fetch_candles_multi,
)
from tradebot.exceptions import (
    AuthorizationError,
    GenerationLimitError,
    LimitExceededError,
    TradingPlatformError,
)
from tradebot.logging import get_logger
from tradebot.models.signal import SignalSource, SignalType
from tradebot.saas.repositories.signal_repo import SignalRepository
from tradebot.saas.repositories.user_repo import UserRepository
from tradebot.saas.services.onboarding_service import OnboardingService
from tradebot.saas.schemas.signal import (
    GenerateSignalRequest,
    GenerationQuotaResponse,
    ScanMarketsRequest,
    SignalFilterParams,
    SignalListResponse,
    SignalResponse,
)

logger = get_logger(__name__)

FREE_GENERATION_LIMIT = 3
UNLIMITED_THRESHOLD = 999


class SignalService:
    """Signal listing, user generation, market scanning, quota gating"""

    def __init__(self, db: Session) -> None:
        self._signal_repo = SignalRepository(db)
        self._user_repo = UserRepository(db)
        self._onboarding = OnboardingService(db)

    # ── Browse system signals ──────────────────────────────────────

    def list_signals(self, user_id: int,
                     filters: SignalFilterParams) -> SignalListResponse:
        gate = self._onboarding.check_feature_access(user_id, "signals")
        if not gate.allowed:
            raise AuthorizationError(gate.message)

        sub = self._user_repo.get_subscription(user_id)
        if sub.tier.value == "free":
            filters.is_free_only = True

        signals, total = self._signal_repo.list_active(
            symbol=filters.symbol,
            signal_type=filters.signal_type,
            min_confidence=filters.min_confidence,
            is_free_only=filters.is_free_only,
            page=filters.page,
            page_size=filters.page_size,
            sort_by=filters.sort_by,
            sort_order=filters.sort_order,
        )

        items = [SignalResponse.model_validate(s) for s in signals]
        total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)

        return SignalListResponse(
            signals=items,
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            total_pages=total_pages,
            has_next=filters.page < total_pages,
            has_prev=filters.page > 1,
        )

    def view_signal(self, user_id: int, signal_id: int) -> SignalResponse:
        gate = self._onboarding.check_feature_access(user_id, "signals")
        if not gate.allowed:
            raise AuthorizationError(gate.message)

        signal = self._signal_repo.get_by_id(signal_id)
        sub = self._user_repo.get_subscription(user_id)

        if sub.tier.value == "free" and not signal.is_free_signal:
            raise AuthorizationError(
                "This is a premium signal. Upgrade to Starter or higher to access."
            )

        viewed_today = self._signal_repo.count_signals_viewed_today(user_id)
        if viewed_today >= sub.daily_signal_limit:
            raise LimitExceededError("Daily signal views", sub.daily_signal_limit, viewed_today)

        self._signal_repo.record_signal_view(signal_id, user_id)

        if viewed_today == 0:
            self._onboarding.auto_complete_first_signal(user_id)

        return SignalResponse.model_validate(signal)

    # ── User signal generator ─────────────────────────────────────

    def generate_signal(self, user_id: int,
                        payload: GenerateSignalRequest) -> SignalResponse:
        """Run TA analysis for a single symbol and return a signal.
        Free users: 3 lifetime generations. Paid: per-tier quota."""
        self._enforce_generation_quota(user_id)

        analysis = self._run_technical_analysis(
            payload.symbol, payload.timeframe, payload.indicators,
        )

        signal = self._signal_repo.create_user_signal(
            user_id=user_id,
            symbol=payload.symbol,
            signal_type=analysis["signal_type"],
            confidence_score=analysis["confidence"],
            analysis_reason=analysis["reason"],
            entry_price=analysis["entry_price"],
            stop_loss=analysis["stop_loss"],
            take_profit_1=analysis["take_profit_1"],
            take_profit_2=analysis["take_profit_2"],
            take_profit_3=analysis["take_profit_3"],
            risk_reward_ratio=analysis["risk_reward_ratio"],
            source=SignalSource.USER_GENERATED,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        )

        self._increment_generation_count(user_id)
        self._onboarding.auto_complete_first_signal(user_id)

        logger.info(
            "Signal generated: user=%d symbol=%s conf=%.2f",
            user_id, payload.symbol, analysis["confidence"],
        )
        return SignalResponse.model_validate(signal)

    # ── Market scanner ─────────────────────────────────────────────

    def scan_markets(self, user_id: int,
                     payload: ScanMarketsRequest) -> list[SignalResponse]:
        """Scan multiple symbols → return top signals sorted by confidence.
        Costs 1 generation credit per scan."""
        self._enforce_generation_quota(user_id)

        results: list[dict] = []
        for symbol in payload.symbols:
            analysis = self._run_technical_analysis(
                symbol, payload.timeframe, ["rsi", "macd", "ema_cross"],
            )
            if analysis["confidence"] >= payload.min_confidence:
                results.append({"symbol": symbol, **analysis})

        results.sort(key=lambda r: r["confidence"], reverse=True)
        results = results[:payload.limit]

        signals: list[SignalResponse] = []
        for result in results:
            signal = self._signal_repo.create_user_signal(
                user_id=user_id,
                symbol=result["symbol"],
                signal_type=result["signal_type"],
                confidence_score=result["confidence"],
                analysis_reason=result["reason"],
                entry_price=result["entry_price"],
                stop_loss=result["stop_loss"],
                take_profit_1=result["take_profit_1"],
                take_profit_2=result.get("take_profit_2"),
                take_profit_3=result.get("take_profit_3"),
                risk_reward_ratio=result["risk_reward_ratio"],
                source=SignalSource.USER_SCAN,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
            )
            signals.append(SignalResponse.model_validate(signal))

        self._increment_generation_count(user_id)
        self._onboarding.auto_complete_first_signal(user_id)

        logger.info("Scan completed: user=%d scanned=%d matched=%d",
                     user_id, len(payload.symbols), len(signals))
        return signals

    # ── Generation quota ───────────────────────────────────────────

    def get_generation_quota(self, user_id: int) -> GenerationQuotaResponse:
        sub = self._user_repo.get_subscription(user_id)
        total = sub.generation_credits + sub.bonus_credits
        used = sub.free_generations_used
        is_unlimited = sub.generation_credits >= UNLIMITED_THRESHOLD
        remaining = max(0, total - used) if not is_unlimited else 9999

        upgrade_prompt = None
        donate_prompt = None
        if not is_unlimited and remaining <= 1:
            upgrade_prompt = (
                "You're running low on signal generations! "
                "Upgrade to Starter ($19.99/mo) for unlimited generations."
            )
            donate_prompt = (
                "Love free signals? Buy us a coffee ($5) and get 5 bonus generations!"
            )

        return GenerationQuotaResponse(
            tier=sub.tier.value,
            total_credits=total,
            used_credits=used,
            remaining_credits=remaining,
            bonus_credits=sub.bonus_credits,
            is_unlimited=is_unlimited,
            upgrade_prompt=upgrade_prompt,
            donate_prompt=donate_prompt,
        )

    def list_my_generations(self, user_id: int, page: int = 1,
                            page_size: int = 20) -> SignalListResponse:
        signals, total = self._signal_repo.list_user_generated(user_id, page, page_size)
        items = [SignalResponse.model_validate(s) for s in signals]
        total_pages = max(1, (total + page_size - 1) // page_size)
        return SignalListResponse(
            signals=items, total=total, page=page, page_size=page_size,
            total_pages=total_pages,
            has_next=page < total_pages, has_prev=page > 1,
        )

    # ── Private helpers ────────────────────────────────────────────

    def _enforce_generation_quota(self, user_id: int) -> None:
        sub = self._user_repo.get_subscription(user_id)
        if sub.generation_credits >= UNLIMITED_THRESHOLD:
            return

        total_available = sub.generation_credits + sub.bonus_credits
        if sub.free_generations_used >= total_available:
            raise GenerationLimitError(
                used=sub.free_generations_used,
                limit=total_available,
            )

    def _increment_generation_count(self, user_id: int) -> None:
        sub = self._user_repo.get_subscription(user_id)
        self._user_repo.update_subscription(
            user_id, free_generations_used=sub.free_generations_used + 1
        )

    @staticmethod
    def _run_technical_analysis(symbol: str, timeframe: str,
                                indicators: list[str]) -> dict:
        """Fetch real OHLCV candles and run technical analysis.

        Returns dict with: signal_type, confidence, reason, entry_price,
        stop_loss, take_profit_1/2/3, risk_reward_ratio.

        Raises TradingPlatformError if market data cannot be retrieved.
        """
        try:
            candles = fetch_candles(symbol, timeframe)
        except MarketDataError as exc:
            raise TradingPlatformError("MarketData", str(exc)) from exc

        result = analyze(candles, requested_indicators=indicators)
        direction = result["direction"]
        confidence = result["confidence"]

        if direction == 1:
            signal_type = SignalType.STRONG_BUY if confidence > 0.80 else SignalType.BUY
        elif direction == -1:
            signal_type = SignalType.STRONG_SELL if confidence > 0.80 else SignalType.SELL
        else:
            signal_type = SignalType.BUY

        indicator_label = ", ".join(indicators) if indicators else "default"
        reason = f"TA {timeframe} [{indicator_label}]: {result['reason']}"

        return {
            "signal_type": signal_type,
            "confidence": confidence,
            "reason": reason,
            "entry_price": result["entry_price"],
            "stop_loss": result["stop_loss"],
            "take_profit_1": result["take_profit_1"],
            "take_profit_2": result["take_profit_2"],
            "take_profit_3": result["take_profit_3"],
            "risk_reward_ratio": result["risk_reward_ratio"],
        }
