"""
Signal analysis models — core signal types passed through the pipeline.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto


class SignalGrade(Enum):
    """Signal quality / confidence grade."""
    STRONG = auto()
    MODERATE = auto()
    WEAK = auto()
    NEUTRAL = auto()


class SignalSource(Enum):
    """Origin of the signal analysis."""
    MOMEN = "momen"
    ADJACENCY = "adjacency"
    STREAK = "streak"
    COLD_DIGIT = "cold_digit"
    CONSENSUS = "consensus"
    MANUAL = "manual"


@dataclass
class Signal:
    """A trading signal produced by an analysis engine."""
    symbol: str
    direction: str  # CALL or PUT
    predicted_digit: int
    confidence: float  # 0.0 - 1.0
    source: SignalSource
    grade: SignalGrade = SignalGrade.NEUTRAL
    entry_price: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return self.confidence > 0.0

    def __post_init__(self):
        # Auto-assign grade from confidence if not explicitly set
        if self.grade == SignalGrade.NEUTRAL and self.confidence > 0:
            if self.confidence >= 0.7:
                self.grade = SignalGrade.STRONG
            elif self.confidence >= 0.5:
                self.grade = SignalGrade.MODERATE
            elif self.confidence >= 0.3:
                self.grade = SignalGrade.WEAK
