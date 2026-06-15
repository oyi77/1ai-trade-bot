"""Signal quality filtering: strategies, validation, and per-brand adjustments."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from unified_bot.whitelabel import BrandConfiguration

if TYPE_CHECKING:
    from tradebot.models import Signal

LOG = logging.getLogger(__name__)
class QualityGateStrategy(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    CUSTOM = "custom"
class SignalQuality(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    REJECT = "reject"
@dataclass
class SignalMetrics:
    """Signal processing metrics."""
    
    signal_id: str
    symbol: str
    confidence: float
    quality: SignalQuality
    processing_time_ms: float
    engine_scores: Dict[str, float] = None
    brand_adjustments: Dict[str, float] = None
    
    def __post_init__(self):
        if self.engine_scores is None:
            self.engine_scores = {}
        if self.brand_adjustments is None:
            self.brand_adjustments = {}
@dataclass
class QualityGateConfig:
    """Quality gate configuration."""
    
    # Confidence thresholds
    min_confidence: float = 0.7
    max_confidence: float = 1.0
    
    # Processing thresholds
    max_processing_time_ms: float = 1000
    min_processing_time_ms: float = 10
    
    # Engine thresholds
    min_valid_engines: int = 1
    max_failed_engines: int = 2
    
    # Strategy configuration
    strategy: QualityGateStrategy = QualityGateStrategy.MODERATE
    custom_thresholds: Dict[str, float] = None
    
    # Brand adjustments
    brand_adjustments_enabled: bool = True
    default_brand_adjustment: float = 1.0
    
    # Fallback strategies
    fallback_strategies: List[str] = None
    
    # Metrics
    enable_metrics: bool = True
    metrics_retention_hours: int = 24
    
    def __post_init__(self):
        if self.custom_thresholds is None:
            self.custom_thresholds = {}
        if self.fallback_strategies is None:
            self.fallback_strategies = ["conservative", "moderate", "aggressive"]
class QualityGate:
    """Filters signals by confidence thresholds and quality strategies."""
    
    def __init__(self, config: QualityGateConfig):
        self.config = config
        self.metrics = []
        self.strategies = {
            QualityGateStrategy.CONSERVATIVE: self._conservative_strategy,
            QualityGateStrategy.MODERATE: self._moderate_strategy,
            QualityGateStrategy.AGGRESSIVE: self._aggressive_strategy,
            QualityGateStrategy.CUSTOM: self._custom_strategy,
        }
        LOG.info("QualityGate initialized with strategy: %s", config.strategy)
    
    async def filter(self, signals: list["Signal"]) -> list["Signal"]:
        """
        Filter signals based on quality criteria.
        
        Args:
            signals: List of signals to filter
            
        Returns:
            Filtered list of signals
        """
        if not signals:
            return []
        
        filtered_signals = []
        for signal in signals:
            try:
                # Process signal through quality gate
                processed_signal = await self._process_signal(signal)
                
                # Check if signal passes quality gate
                if await self._signal_passes_quality_gate(processed_signal):
                    filtered_signals.append(processed_signal)
                    
                # Record metrics
                await self._record_metrics(processed_signal)
                
            except Exception as e:
                LOG.error("Error processing signal %s: %s", signal.id, e)
                continue
        
        LOG.info("Filtered %d signals to %d signals", 
                len(signals), len(filtered_signals))
        return filtered_signals
    
    async def _process_signal(self, signal: "Signal") -> "Signal":
        """
        Process signal through quality gate.
        
        Args:
            signal: Signal to process
            
        Returns:
            Processed signal
        """
        start_time = time.time()
        
        try:
            # Validate signal
            self._validate_signal(signal)
            
            # Apply quality strategy
            quality = await self._apply_quality_strategy(signal)
            
            # Record metrics
            processing_time_ms = (time.time() - start_time) * 1000
            
            # Create signal metrics
            metrics = SignalMetrics(
                signal_id=signal.id,
                symbol=signal.symbol,
                confidence=signal.confidence,
                quality=quality,
                processing_time_ms=processing_time_ms,
                engine_scores=self._get_engine_scores(signal),
                brand_adjustments=self._get_brand_adjustments(signal)
            )
            
            # Apply quality-based adjustments
            adjusted_confidence = self._adjust_confidence(signal.confidence, quality)
            
            # Create processed signal
            processed_signal = Signal(
                id=signal.id,
                symbol=signal.symbol,
                action=signal.action,
                confidence=adjusted_confidence,
                timeframe=signal.timeframe,
                price=signal.price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                quantity=signal.quantity,
                exchange=signal.exchange,
                timestamp=signal.timestamp,
                metadata=signal.metadata
            )
            
            return processed_signal
            
        except Exception as e:
            LOG.error("Error processing signal %s: %s", signal.id, e)
            raise
    
    async def _signal_passes_quality_gate(self, signal: "Signal") -> bool:
        """
        Check if signal passes quality gate.
        
        Args:
            signal: Signal to check
            
        Returns:
            True if signal passes, False otherwise
        """
        # Check confidence threshold
        if signal.confidence < self.config.min_confidence:
            LOG.debug("Signal %s rejected: confidence too low (%.2f < %.2f)", 
                     signal.id, signal.confidence, self.config.min_confidence)
            return False
        
        # Check processing time
        if signal.processing_time_ms > self.config.max_processing_time_ms:
            LOG.debug("Signal %s rejected: processing time too high (%.2f > %.2f ms)", 
                     signal.id, signal.processing_time_ms, self.config.max_processing_time_ms)
            return False
        
        # Check signal quality
        if signal.quality == SignalQuality.REJECT:
            LOG.debug("Signal %s rejected: poor quality", signal.id)
            return False
        
        return True
    
    async def _apply_quality_strategy(self, signal: "Signal") -> SignalQuality:
        """
        Apply quality strategy to signal.
        
        Args:
            signal: Signal to process
            
        Returns:
            Signal quality
        """
        strategy = self.strategies.get(self.config.strategy, self._moderate_strategy)
        return await strategy(signal)
    
    def _conservative_strategy(self, signal: "Signal") -> SignalQuality:
        """Conservative quality strategy."""
        if signal.confidence >= 0.9 and signal.confidence <= 1.0:
            return SignalQuality.EXCELLENT
        elif signal.confidence >= 0.8 and signal.confidence < 0.9:
            return SignalQuality.GOOD
        elif signal.confidence >= 0.7 and signal.confidence < 0.8:
            return SignalQuality.FAIR
        else:
            return SignalQuality.POOR
    
    def _moderate_strategy(self, signal: "Signal") -> SignalQuality:
        """Moderate quality strategy."""
        if signal.confidence >= 0.85 and signal.confidence <= 1.0:
            return SignalQuality.EXCELLENT
        elif signal.confidence >= 0.75 and signal.confidence < 0.85:
            return SignalQuality.GOOD
        elif signal.confidence >= 0.7 and signal.confidence < 0.75:
            return SignalQuality.FAIR
        else:
            return SignalQuality.POOR
    
    def _aggressive_strategy(self, signal: "Signal") -> SignalQuality:
        """Aggressive quality strategy."""
        if signal.confidence >= 0.8 and signal.confidence <= 1.0:
            return SignalQuality.EXCELLENT
        elif signal.confidence >= 0.6 and signal.confidence < 0.8:
            return SignalQuality.GOOD
        elif signal.confidence >= 0.5 and signal.confidence < 0.6:
            return SignalQuality.FAIR
        else:
            return SignalQuality.POOR
    
    def _custom_strategy(self, signal: "Signal") -> SignalQuality:
        """Custom quality strategy."""
        # Apply custom thresholds
        custom_threshold = self.config.custom_thresholds.get("min_confidence", self.config.min_confidence)
        
        if signal.confidence >= custom_threshold and signal.confidence <= 1.0:
            return SignalQuality.EXCELLENT
        elif signal.confidence >= custom_threshold * 0.9 and signal.confidence < custom_threshold:
            return SignalQuality.GOOD
        elif signal.confidence >= custom_threshold * 0.8 and signal.confidence < custom_threshold * 0.9:
            return SignalQuality.FAIR
        else:
            return SignalQuality.POOR
    
    def _validate_signal(self, signal: "Signal") -> None:
        """
        Validate signal.
        
        Args:
            signal: Signal to validate
            
        Raises:
            ValueError: If signal is invalid
        """
        if not signal.id:
            raise ValueError("Signal ID is required")
        
        if not signal.symbol:
            raise ValueError("Signal symbol is required")
        
        if not signal.action:
            raise ValueError("Signal action is required")
        
        if not isinstance(signal.confidence, (int, float)):
            raise ValueError("Signal confidence must be a number")
        
        if signal.confidence < 0 or signal.confidence > 1:
            raise ValueError("Signal confidence must be between 0 and 1")
        
        if not signal.timeframe:
            raise ValueError("Signal timeframe is required")
        
        if signal.price is not None and signal.price < 0:
            raise ValueError("Signal price cannot be negative")
    
    def _adjust_confidence(self, confidence: float, quality: SignalQuality) -> float:
        """
        Adjust confidence based on quality.
        
        Args:
            confidence: Original confidence
            quality: Signal quality
            
        Returns:
            Adjusted confidence
        """
        adjustment_factor = {
            SignalQuality.EXCELLENT: 1.0,
            SignalQuality.GOOD: 1.0,
            SignalQuality.FAIR: 0.9,
            SignalQuality.POOR: 0.8,
            SignalQuality.REJECT: 0.0,
        }.get(quality, 0.5)
        
        adjusted_confidence = confidence * adjustment_factor
        
        # Apply brand-specific adjustments
        if hasattr(self, 'brand_adjustments'):
            adjusted_confidence *= self.brand_adjustments.get("confidence_adjustment", 1.0)
        
        return min(max(adjusted_confidence, 0.0), 1.0)
    
    def _get_engine_scores(self, signal: "Signal") -> Dict[str, float]:
        """
        Get engine scores for signal.
        
        Args:
            signal: Signal
            
        Returns:
            Engine scores
        """
        # This would be populated from the signal's engine analysis results
        # For now, return empty dict
        return {}
    
    def _get_brand_adjustments(self, signal: "Signal") -> Dict[str, float]:
        """
        Get brand adjustments for signal.
        
        Args:
            signal: Signal
            
        Returns:
            Brand adjustments
        """
        # This would be populated from brand configuration
        # For now, return empty dict
        return {}
    
    async def _record_metrics(self, signal: "Signal") -> None:
        """
        Record signal metrics.
        
        Args:
            signal: Signal
        """
        if self.config.enable_metrics:
            # Store metrics for later analysis
            pass
    
    async def shutdown(self) -> None:
        """Shutdown quality gate."""
        LOG.info("QualityGate shutdown completed")

# Signal import (defined to avoid circular imports)
from tradebot.models import Signal