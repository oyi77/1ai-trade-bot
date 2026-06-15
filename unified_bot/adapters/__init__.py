"""
Adapters for integrating PoC scripts into the unified bot.

Each adapter wraps a stand-alone PoC script from scripts/ and exposes
a clean async interface compatible with the UnifiedBot architecture.
"""

from unified_bot.adapters.satpam_adapter import SatpamAdapter
from unified_bot.adapters.payment_adapter import ScalevAdapter
from unified_bot.adapters.subscription_adapter import SubscriptionAdapter
from unified_bot.adapters.signal_bridge_adapter import SignalBridgeAdapter
from unified_bot.adapters.engine_consensus_adapter import EngineConsensusAdapter
from unified_bot.adapters.license_manager_adapter import LicenseManagerAdapter
from unified_bot.adapters.expiry_reminder_adapter import ExpiryReminderAdapter
from unified_bot.adapters.daily_report_adapter import DailyReportAdapter

__all__ = [
    "SatpamAdapter",
    "ScalevAdapter",
    "SubscriptionAdapter",
    "SignalBridgeAdapter",
    "EngineConsensusAdapter",
    "LicenseManagerAdapter",
    "ExpiryReminderAdapter",
    "DailyReportAdapter",
]
