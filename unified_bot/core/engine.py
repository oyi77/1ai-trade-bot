"""Core signal processing engine wrapping tradebot pipeline with whitelabel support."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from unified_bot.whitelabel import BrandConfiguration, BrandManager

from tradebot.engines.consensus import EngineConsensus
from tradebot.pipeline.signal_pipeline import SignalPipeline
from tradebot.models import Signal, Tick

if TYPE_CHECKING:
    from .quality_gate import QualityGate

from unified_bot.core.metrics import ProcessingMetrics

LOG = logging.getLogger(__name__)
class UnifiedSignalEngine:
    """Signal pipeline wrapper with per-brand config, rate limiting, and quality gating."""
    
    def __init__(self, brand_manager: BrandManager):
        self.brand_manager = brand_manager
        self.current_brand: Optional[str] = None
        
        # Core tradebot components
        self.pipeline: Optional[SignalPipeline] = None
        self.consensus: Optional[EngineConsensus] = None
        self.quality_gate: Optional[QualityGate] = None
        
        # Unified metrics
        self.metrics = ProcessingMetrics()
        
        # Brand-specific configuration
        self.brand_config: Optional[BrandConfiguration] = None
        
        # Signal callback
        self.signal_callback: Optional[Callable[[Signal], Any]] = None
        
        # Rate limiting
        self.rate_limiter = RateLimiter()
        
        LOG.info("UnifiedSignalEngine initialized")
    
    async def initialize(self, brand_id: str) -> bool:
        """
        Initialize engine for a specific brand.
        
        Args:
            brand_id: Brand identifier
            
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Get brand configuration
            self.brand_config = self.brand_manager.get_brand(brand_id)
            if not self.brand_config:
                LOG.error("Brand %s not found", brand_id)
                return False
            
            # Set current brand
            self.current_brand = brand_id
            
            # Initialize core tradebot components
            await self._initialize_core_components()
            
            # Validate brand configuration
            if not self._validate_brand_config():
                LOG.error("Brand configuration validation failed")
                return False
            
            LOG.info("UnifiedSignalEngine initialized for brand %s", brand_id)
            return True
            
        except Exception as e:
            LOG.error("Failed to initialize engine for brand %s: %s", brand_id, e)
            return False
    
    async def process_ticks(self, ticks: list[Tick], brand_id: str) -> list[Signal]:
        """
        Process ticks for a specific brand.
        
        Args:
            ticks: List of ticks to process
            brand_id: Brand identifier
            
        Returns:
            List of processed signals
        """
        # Rate limiting
        if not self.rate_limiter.can_process(brand_id):
            LOG.warning("Rate limit exceeded for brand %s", brand_id)
            return []
        
        # Set brand
        await self.initialize(brand_id)
        
        # Process ticks through pipeline
        signals = []
        try:
            # Add brand-specific filters
            filtered_ticks = self._apply_brand_filters(ticks)
            
            # Process through pipeline
            pipeline_signals = await self.pipeline.process(filtered_ticks)
            
            # Apply quality gate
            if self.quality_gate:
                pipeline_signals = await self.quality_gate.filter(pipeline_signals)
            
            # Apply brand-specific confidence filtering
            signals = self._apply_confidence_filter(pipeline_signals)
            
            # Record metrics
            self.metrics.record_processing(len(ticks), len(signals))
            
            # Emit signals
            for signal in signals:
                if self.signal_callback:
                    await self.signal_callback(signal)
                
            LOG.info("Processed %d ticks, generated %d signals for brand %s", 
                    len(ticks), len(signals), brand_id)
            
        except Exception as e:
            LOG.error("Error processing ticks for brand %s: %s", brand_id, e)
            self.metrics.record_error()
        
        return signals
    
    async def _initialize_core_components(self) -> None:
        """Initialize core tradebot components."""
        try:
            # Initialize pipeline with brand-specific settings
            pipeline_config = self._get_pipeline_config()
            self.pipeline = SignalPipeline(**pipeline_config)
            
            # Initialize consensus engine
            consensus_config = self._get_consensus_config()
            self.consensus = EngineConsensus(**consensus_config)
            
            # Initialize quality gate
            quality_gate_config = self._get_quality_gate_config()
            self.quality_gate = QualityGate(**quality_gate_config)
            
            LOG.info("Core components initialized for brand %s", self.current_brand)
            
        except Exception as e:
            LOG.error("Failed to initialize core components: %s", e)
            raise
    
    def _validate_brand_config(self) -> bool:
        """Validate brand configuration."""
        if not self.brand_config:
            return False
        
        # Check required features
        if not self.brand_config.is_feature_enabled("signal_generation", "core"):
            LOG.error("Signal generation feature not enabled for brand %s", 
                     self.current_brand)
            return False
        
        # Check confidence threshold
        if self.brand_config.features.min_confidence_threshold < 0 or \
           self.brand_config.features.min_confidence_threshold > 1:
            LOG.error("Invalid confidence threshold for brand %s", 
                     self.current_brand)
            return False
        
        # Check rate limits
        if any(v <= 0 for v in self.brand_config.rate_limits.values()):
            LOG.error("Invalid rate limits for brand %s", self.current_brand)
            return False
        
        return True
    
    def _apply_brand_filters(self, ticks: list[Tick]) -> list[Tick]:
        """Apply brand-specific filters to ticks."""
        filtered_ticks = ticks.copy()
        
        # Apply confidence threshold
        min_confidence = self.brand_config.features.min_confidence_threshold
        filtered_ticks = [tick for tick in filtered_ticks 
                        if tick.confidence >= min_confidence]
        
        # Apply symbol filters
        if self.brand_config.features.signal_providers:
            filtered_ticks = [tick for tick in filtered_ticks 
                            if tick.symbol in self.brand_config.features.signal_providers]
        
        # Apply time-based filters
        current_time = time.time()
        for tick in filtered_ticks:
            if tick.timestamp and (current_time - tick.timestamp) > 3600:  # 1 hour
                filtered_ticks.remove(tick)
        
        return filtered_ticks
    
    def _apply_confidence_filter(self, signals: list[Signal]) -> list[Signal]:
        """Apply confidence filtering based on brand settings."""
        if not self.brand_config:
            return signals
        
        min_confidence = self.brand_config.features.min_confidence_threshold
        return [signal for signal in signals if signal.confidence >= min_confidence]
    
    def _get_pipeline_config(self) -> dict:
        """Get pipeline configuration for current brand."""
        return {
            "middleware_config": {
                "rate_limit": {
                    "requests_per_minute": self.brand_config.rate_limits["api_requests_per_minute"]
                },
                "quality_gate": {
                    "min_confidence": self.brand_config.features.min_confidence_threshold
                }
            }
        }
    
    def _get_consensus_config(self) -> dict:
        """Get consensus configuration for current brand."""
        return {
            "engine_weights": self._get_engine_weights(),
            "confidence_threshold": self.brand_config.features.min_confidence_threshold,
            "min_valid_engines": max(1, len(self.brand_config.features.signal_providers) // 2)
        }
    
    def _get_quality_gate_config(self) -> dict:
        """Get quality gate configuration for current brand."""
        return {
            "min_confidence": self.brand_config.features.min_confidence_threshold,
            "max_latency_ms": 1000,
            "retry_attempts": 3,
            "fallback_strategies": ["conservative", "aggressive"]
        }
    
    def _get_engine_weights(self) -> dict:
        """Get engine weights based on brand configuration."""
        # Default weights
        weights = {
            "rsi": 1.0,
            "macd": 1.0,
            "sma": 1.0,
            "ema": 1.0,
            "bollinger_bands": 1.0,
            "stochastic": 0.8,
            "atr": 0.8,
            "ichimoku": 0.7,
            "supertrend": 0.7,
            "vwap": 0.6,
        }
        
        # Adjust weights based on brand-specific indicators
        if self.brand_config.features.custom_indicators:
            for indicator in self.brand_config.features.custom_indicators:
                weights[indicator] = 1.5
        
        return weights
    
    def set_signal_callback(self, callback: Callable[[Signal], Any]) -> None:
        """
        Set signal callback for processing.
        
        Args:
            callback: Signal callback function
        """
        self.signal_callback = callback
    
    def get_metrics(self) -> dict:
        """
        Get engine metrics.
        
        Returns:
            Engine metrics
        """
        return {
            "brand_id": self.current_brand,
            "metrics": self.metrics.snapshot(),
            "brand_config": self.brand_config.to_dict() if self.brand_config else None
        }
    
    async def health_check(self) -> dict:
        """
        Perform health check.
        
        Returns:
            Health check results
        """
        health_status = {
            "status": "healthy",
            "components": {},
            "errors": [],
        }
        
        try:
            # Check core components
            if self.pipeline:
                health_status["components"]["pipeline"] = "healthy"
            else:
                health_status["components"]["pipeline"] = "unhealthy"
                health_status["errors"].append("Pipeline not initialized")
            
            if self.consensus:
                health_status["components"]["consensus"] = "healthy"
            else:
                health_status["components"]["consensus"] = "unhealthy"
                health_status["errors"].append("Consensus not initialized")
            
            if self.quality_gate:
                health_status["components"]["quality_gate"] = "healthy"
            else:
                health_status["components"]["quality_gate"] = "unhealthy"
                health_status["errors"].append("Quality gate not initialized")
            
            # Check brand configuration
            if not self.brand_config:
                health_status["status"] = "unhealthy"
                health_status["errors"].append("Brand not configured")
            
            # Check rate limiting
            if not self.rate_limiter.is_healthy():
                health_status["components"]["rate_limiter"] = "unhealthy"
                health_status["errors"].append("Rate limiter error")
            else:
                health_status["components"]["rate_limiter"] = "healthy"
            
        except Exception as e:
            health_status["status"] = "unhealthy"
            health_status["errors"].append(f"Health check error: {e}")
        
        return health_status
    
    async def shutdown(self) -> None:
        """Shutdown engine."""
        try:
            # Cleanup resources
            if self.pipeline:
                await self.pipeline.shutdown()
            
            if self.consensus:
                await self.consensus.shutdown()
            
            if self.quality_gate:
                await self.quality_gate.shutdown()
            
            LOG.info("UnifiedSignalEngine shutdown completed for brand %s", 
                    self.current_brand)
            
        except Exception as e:
            LOG.error("Error during engine shutdown: %s", e)
class RateLimiter:
    """Rate limiter for unified signal engine."""
    
    def __init__(self):
        self.requests = {}
        self.lock = asyncio.Lock()
    
    async def can_process(self, brand_id: str, limit: int = 60) -> bool:
        """
        Check if request can be processed.
        
        Args:
            brand_id: Brand identifier
            limit: Request limit per minute
            
        Returns:
            True if request can be processed, False otherwise
        """
        async with self.lock:
            current_time = time.time()
            minute_start = int(current_time // 60)
            
            if brand_id not in self.requests:
                self.requests[brand_id] = {}
            
            if minute_start not in self.requests[brand_id]:
                self.requests[brand_id][minute_start] = 0
            
            if self.requests[brand_id][minute_start] >= limit:
                return False
            
            self.requests[brand_id][minute_start] += 1
            return True
    
    def is_healthy(self) -> bool:
        """
        Check if rate limiter is healthy.
        
        Returns:
            True if rate limiter is healthy, False otherwise
        """
        return True

# QualityGate import (defined in separate file to avoid circular imports)
from unified_bot.core.quality_gate import QualityGate
class UnifiedEngineException(Exception):
    """Base exception for unified engine errors."""
    pass
class EngineInitializationError(UnifiedEngineException):
    """Engine initialization error."""
    pass
class BrandConfigurationError(UnifiedEngineException):
    """Brand configuration error."""
    pass