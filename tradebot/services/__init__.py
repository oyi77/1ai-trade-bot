"""Long-running services."""

from .briefing_service import send_daily_briefing
from .consensus_service import (
    append_quant_consensus_ui,
    format_sequoia_block,
    run_engine_consensus,
    run_sequoia_screen,
)
from .health import HealthCheckResult, HealthReport, HealthService, HealthStatus
from .members_service import (
    activate_premium,
    check_quota,
    deactivate_premium,
    ensure_member,
    get_due_members,
    get_member,
    get_member_stats,
    get_monthly_fuel_stats,
    get_stale_donors,
    get_total_donations,
    get_user_last_donation,
    init_db,
    insert_payment_order,
    is_premium,
    mark_expired,
    mark_payment_paid,
    upgrade_tier,
    use_quota,
)
from .payment import PaymentService
from .publisher import SignalPublisher
from .reminder_service import send_bensin_reminders
from .signal_calculator_service import (
    compute_signal,
    format_signal_telegram,
    log_signal,
)
from .signal_service import (
    add_signal,
    get_recent_signals,
    get_stats,
    get_user_signals,
    update_outcome,
)
from .telegram import TelegramService
from .trade_tracker_service import get_daily_trades
from .watchdog import WatchdogService

__all__ = [
    "TelegramService",
    "PaymentService",
    "HealthService",
    "HealthReport",
    "HealthCheckResult",
    "HealthStatus",
    "WatchdogService",
    "SignalPublisher",
    "add_signal",
    "append_quant_consensus_ui",
    "compute_signal",
    "format_sequoia_block",
    "format_signal_telegram",
    "get_daily_trades",
    "get_recent_signals",
    "get_stats",
    "get_user_signals",
    "log_signal",
    "run_engine_consensus",
    "run_sequoia_screen",
    "update_outcome",
    "send_daily_briefing",
    "send_bensin_reminders",
    "init_db",
    "ensure_member",
    "get_member",
    "upgrade_tier",
    "activate_premium",
    "deactivate_premium",
    "mark_expired",
    "get_total_donations",
    "insert_payment_order",
    "mark_payment_paid",
    "get_member_stats",
    "get_due_members",
    "is_premium",
    "check_quota",
    "use_quota",
    "get_monthly_fuel_stats",
    "get_user_last_donation",
    "get_stale_donors",
]
