"""
Shim — re-exports from tradebot.brokers.deriv.
Keeps existing imports working during migration.
"""
import sys, warnings
from pathlib import Path

# Ensure tradebot is importable
_tradebot = Path(__file__).resolve().parent.parent.parent / "tradebot"
if str(_tradebot) not in sys.path:
    sys.path.insert(0, str(_tradebot.parent))

warnings.warn(
    "scripts/deriv/ is deprecated — use tradebot.brokers.deriv instead",
    DeprecationWarning, stacklevel=2
)

from tradebot.brokers.deriv import *  # noqa: F401, F403
__all__ = [  # noqa: F405
    "DerivWSClient", "DerivTick", "DerivOHLCV", "DerivContractResult",
    "MomenPatternAnalyzer", "AdjacencyPatternAnalyzer", "StreakCountdownAnalyzer",
    "DigitMartingaleStrategy", "MultiStreamActuary", "CognitiveDB",
]
