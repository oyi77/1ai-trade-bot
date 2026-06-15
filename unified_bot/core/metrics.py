"""Processing and brand-specific metrics collection."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from unified_bot.whitelabel import BrandConfiguration

LOG = logging.getLogger(__name__)
class MetricType(str, Enum):
    PROCESSING_TIME = "processing_time"
    CONFIDENCE_SCORE = "confidence_score"
    QUALITY_SCORE = "quality_score"
    ENGINE_SCORE = "engine_score"
    ERROR_COUNT = "error_count"
    SUCCESS_RATE = "success_rate"
    BRAND_USAGE = "brand_usage"
    SIGNAL_VOLUME = "signal_volume"
class MetricPeriod(str, Enum):
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
@dataclass
class MetricDataPoint:
    """Individual metric data point."""
    
    timestamp: datetime
    value: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "metadata": self.metadata,
        }
@dataclass
class ProcessingMetrics:
    """Processing performance metrics."""
    
    total_signals_processed: int = 0
    total_signals_accepted: int = 0
    total_signals_rejected: int = 0
    total_errors: int = 0
    total_processing_time_ms: float = 0.0
    max_processing_time_ms: float = 0.0
    min_processing_time_ms: float = float("inf")
    
    def record_processing(self, signals_processed: int, signals_accepted: int, 
                         processing_time_ms: float) -> None:
        """Record processing metrics."""
        self.total_signals_processed += signals_processed
        self.total_signals_accepted += signals_accepted
        self.total_signals_rejected += (signals_processed - signals_accepted)
        self.total_processing_time_ms += processing_time_ms
        self.max_processing_time_ms = max(self.max_processing_time_ms, processing_time_ms)
        self.min_processing_time_ms = min(self.min_processing_time_ms, processing_time_ms)
    
    def record_error(self) -> None:
        """Record error."""
        self.total_errors += 1
    
    def success_rate(self) -> float:
        """Get success rate."""
        if self.total_signals_processed == 0:
            return 0.0
        return self.total_signals_accepted / self.total_signals_processed
    
    def average_processing_time_ms(self) -> float:
        """Get average processing time."""
        if self.total_signals_accepted == 0:
            return 0.0
        return self.total_processing_time_ms / self.total_signals_accepted
    
    def snapshot(self) -> Dict[str, Any]:
        """Get metrics snapshot."""
        return {
            "total_signals_processed": self.total_signals_processed,
            "total_signals_accepted": self.total_signals_accepted,
            "total_signals_rejected": self.total_signals_rejected,
            "total_errors": self.total_errors,
            "success_rate": self.success_rate(),
            "average_processing_time_ms": self.average_processing_time_ms(),
            "max_processing_time_ms": self.max_processing_time_ms,
            "min_processing_time_ms": self.min_processing_time_ms if self.min_processing_time_ms != float("inf") else 0,
        }
@dataclass
class BrandMetrics:
    """Brand-specific metrics."""
    
    brand_id: str
    signals_processed: int = 0
    signals_accepted: int = 0
    total_revenue: float = 0.0
    user_count: int = 0
    active_subscriptions: int = 0
    last_activity: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "brand_id": self.brand_id,
            "signals_processed": self.signals_processed,
            "signals_accepted": self.signals_accepted,
            "success_rate": self.success_rate(),
            "total_revenue": self.total_revenue,
            "user_count": self.user_count,
            "active_subscriptions": self.active_subscriptions,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }
    
    def success_rate(self) -> float:
        """Get success rate."""
        if self.signals_processed == 0:
            return 0.0
        return self.signals_accepted / self.signals_processed
class MetricsCollector:
    """Collects processing and brand metrics with retention-based cleanup."""
    
    def __init__(self, retention_hours: int = 24):
        self.retention_hours = retention_hours
        self.processing_metrics: Dict[str, ProcessingMetrics] = {}
        self.brand_metrics: Dict[str, BrandMetrics] = {}
        self.raw_metrics: List[MetricDataPoint] = []
        self.lock = asyncio.Lock()
    
    async def record_processing(self, brand_id: str, signals_processed: int, 
                               signals_accepted: int, processing_time_ms: float) -> None:
        """
        Record processing metrics.
        
        Args:
            brand_id: Brand identifier
            signals_processed: Number of signals processed
            signals_accepted: Number of signals accepted
            processing_time_ms: Processing time in milliseconds
        """
        async with self.lock:
            if brand_id not in self.processing_metrics:
                self.processing_metrics[brand_id] = ProcessingMetrics()
            
            self.processing_metrics[brand_id].record_processing(
                signals_processed, signals_accepted, processing_time_ms
            )
            
            # Update brand metrics
            if brand_id not in self.brand_metrics:
                self.brand_metrics[brand_id] = BrandMetrics(brand_id=brand_id)
            
            self.brand_metrics[brand_id].signals_processed += signals_processed
            self.brand_metrics[brand_id].signals_accepted += signals_accepted
            self.brand_metrics[brand_id].last_activity = datetime.now()
    
    async def record_error(self, brand_id: str) -> None:
        """
        Record error.
        
        Args:
            brand_id: Brand identifier
        """
        async with self.lock:
            if brand_id not in self.processing_metrics:
                self.processing_metrics[brand_id] = ProcessingMetrics()
            
            self.processing_metrics[brand_id].record_error()
    
    async def record_metric(self, metric_type: MetricType, value: float, 
                           brand_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Record custom metric.
        
        Args:
            metric_type: Metric type
            value: Metric value
            brand_id: Brand identifier
            metadata: Optional metadata
        """
        async with self.lock:
            data_point = MetricDataPoint(
                timestamp=datetime.now(),
                value=value,
                metadata=metadata or {}
            )
            self.raw_metrics.append(data_point)
    
    def get_processing_metrics(self, brand_id: str) -> Dict[str, Any]:
        """
        Get processing metrics for a brand.
        
        Args:
            brand_id: Brand identifier
            
        Returns:
            Processing metrics
        """
        if brand_id in self.processing_metrics:
            return self.processing_metrics[brand_id].snapshot()
        return {}
    
    def get_brand_metrics(self, brand_id: str) -> Dict[str, Any]:
        """
        Get brand metrics.
n        Args:
            brand_id: Brand identifier
            
        Returns:
            Brand metrics
        """
        if brand_id in self.brand_metrics:
            return self.brand_metrics[brand_id].to_dict()
        return {}
    
    def get_all_processing_metrics(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all processing metrics.
        
        Returns:
            All processing metrics
        """
        return {
            brand_id: metrics.snapshot()
            for brand_id, metrics in self.processing_metrics.items()
        }
    
    def get_all_brand_metrics(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all brand metrics.
        
        Returns:
            All brand metrics
        """
        return {
            brand_id: metrics.to_dict()
            for brand_id, metrics in self.brand_metrics.items()
        }
    
    async def cleanup_old_metrics(self) -> None:
        """
        Clean up old metrics based on retention period.
        """
        async with self.lock:
            cutoff_time = datetime.now() - timedelta(hours=self.retention_hours)
            
            # Clean raw metrics
            self.raw_metrics = [
                metric for metric in self.raw_metrics
                if metric.timestamp >= cutoff_time
            ]
    
    async def generate_report(self, brand_id: Optional[str] = None, 
                             period_hours: int = 24) -> Dict[str, Any]:
        """
        Generate metrics report.
        
        Args:
            brand_id: Brand identifier (optional)
            period_hours: Report period in hours
            
        Returns:
            Metrics report
        """
        cutoff_time = datetime.now() - timedelta(hours=period_hours)
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "period_hours": period_hours,
            "processing_metrics": {},
            "brand_metrics": {},
        }
        
        if brand_id:
            # Single brand report
            if brand_id in self.processing_metrics:
                report["processing_metrics"][brand_id] = self.processing_metrics[brand_id].snapshot()
            
            if brand_id in self.brand_metrics:
                report["brand_metrics"][brand_id] = self.brand_metrics[brand_id].to_dict()
        else:
            # All brands report
            report["processing_metrics"] = self.get_all_processing_metrics()
            report["brand_metrics"] = self.get_all_brand_metrics()
        
        return report
    
    async def shutdown(self) -> None:
        """Shutdown metrics collector."""
        async with self.lock:
            # Clean up resources
            self.processing_metrics.clear()
            self.brand_metrics.clear()
            self.raw_metrics.clear()
        
        LOG.info("MetricsCollector shutdown completed")