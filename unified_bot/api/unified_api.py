"""FastAPI REST endpoints: signals, brands, metrics, health."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import RequestResponseEndpoint

from unified_bot.whitelabel import BrandManager, BrandConfiguration, BrandStatus
from unified_bot.core.engine import UnifiedSignalEngine
from unified_bot.core.metrics import MetricsCollector

LOG = logging.getLogger(__name__)
class APIConfig(BaseModel):
    """API configuration."""
    
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=False)
    workers: int = Field(default=1)
    timeout: int = Field(default=30)
    max_request_size: int = Field(default=10 * 1024 * 1024)  # 10MB
class SignalRequest(BaseModel):
    """Signal generation request."""
    
    symbol: str = Field(..., description="Trading symbol (e.g., BTC/USDT)")
    timeframe: Optional[str] = Field(default="1h", description="Timeframe")
    confidence_threshold: Optional[float] = Field(default=0.7, description="Minimum confidence threshold")
    provider: Optional[str] = Field(default="unified", description="Signal provider")
    brand_id: str = Field(..., description="Brand identifier")
class SignalResponse(BaseModel):
    """Signal generation response."""
    
    success: bool = Field(..., description="Request success status")
    message: Optional[str] = Field(None, description="Response message")
    data: Optional[Dict[str, Any]] = Field(None, description="Signal data")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata")
class BrandConfigRequest(BaseModel):
    """Brand configuration request."""
    
    brand_id: str = Field(..., description="Brand identifier")
    domain: str = Field(..., description="Brand domain")
    name: str = Field(..., description="Brand name")
    logo_url: Optional[str] = Field(None, description="Brand logo URL")
    primary_color: Optional[str] = Field(None, description="Primary color")
    features: Optional[Dict[str, Any]] = Field(None, description="Brand features")
    pricing: Optional[Dict[str, Any]] = Field(None, description="Brand pricing")
class HealthCheckResponse(BaseModel):
    """Health check response."""
    
    status: str = Field(..., description="Overall status")
    version: str = Field(..., description="Application version")
    timestamp: str = Field(..., description="Timestamp")
    components: Dict[str, Dict[str, str]] = Field(..., description="Component status")
class MetricsResponse(BaseModel):
    """Metrics response."""
    
    processing_metrics: Dict[str, Dict[str, Any]] = Field(..., description="Processing metrics")
    brand_metrics: Dict[str, Dict[str, Any]] = Field(..., description="Brand metrics")
class UnifiedAPIService:
    """FastAPI service exposing signal generation, brand CRUD, and metrics."""
    
    def __init__(self, config: APIConfig, brand_manager: BrandManager, 
                 signal_engine: UnifiedSignalEngine, metrics_collector: MetricsCollector):
        self.config = config
        self.brand_manager = brand_manager
        self.signal_engine = signal_engine
        self.metrics_collector = metrics_collector
        
        # Initialize FastAPI
        self.app = FastAPI(
            title="Unified Trading Bot API",
            description="API for unified multi-brand trading bot",
            version="1.0.0",
            debug=config.debug
        )
        
        # Add middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Initialize routes
        self._init_routes()
        
        LOG.info("UnifiedAPIService initialized")
    
    def _init_routes(self) -> None:
        """Initialize API routes."""
        
        @self.app.get("/")
        async def root() -> Dict[str, str]:
            """Root endpoint."""
            return {
                "service": "Unified Trading Bot API",
                "version": "1.0.0",
                "status": "running"
            }
        
        @self.app.get("/health")
        async def health_check() -> HealthCheckResponse:
            """Health check endpoint."""
            # Check engine health
            engine_health = await self.signal_engine.health_check()
            
            return HealthCheckResponse(
                status=engine_health["status"],
                version="1.0.0",
                timestamp=engine_health.get("timestamp", ""),
                components={
                    "engine": engine_health["components"],
                }
            )
        
        @self.app.post("/signals/generate")
        async def generate_signal(
            request: SignalRequest,
            background_tasks: BackgroundTasks
        ) -> SignalResponse:
            """Generate signal endpoint."""
            try:
                # Validate brand
                if not self.brand_manager.is_brand_active(request.brand_id):
                    return SignalResponse(
                        success=False,
                        message=f"Brand {request.brand_id} is not active"
                    )
                
                # Generate signal
                signals = await self.signal_engine.process_ticks(
                    ticks=[],  # TODO: Get actual ticks
                    brand_id=request.brand_id
                )
                
                if signals:
                    return SignalResponse(
                        success=True,
                        message="Signal generated successfully",
                        data={
                            "signals": [signal.dict() for signal in signals],
                            "count": len(signals),
                            "brand_id": request.brand_id,
                            "timestamp": time.time()
                        }
                    )
                else:
                    return SignalResponse(
                        success=False,
                        message="No signals generated"
                    )
                
            except Exception as e:
                LOG.error("Error generating signal: %s", e)
                return SignalResponse(
                    success=False,
                    message=str(e)
                )
        
        @self.app.get("/signals/queue")
        async def get_signal_queue(
            brand_id: str,
            limit: int = 10
        ) -> SignalResponse:
            """Get signal queue endpoint."""
            try:
                # TODO: Implement signal queue logic
                return SignalResponse(
                    success=True,
                    message="Signal queue retrieved successfully",
                    data={
                        "brand_id": brand_id,
                        "queue_length": 0,
                        "signals": []
                    }
                )
                
            except Exception as e:
                LOG.error("Error getting signal queue: %s", e)
                return SignalResponse(
                    success=False,
                    message=str(e)
                )
        
        @self.app.get("/brands/{brand_id}")
        async def get_brand_info(brand_id: str) -> SignalResponse:
            """Get brand information endpoint."""
            try:
                brand = self.brand_manager.get_brand(brand_id)
                if not brand:
                    return SignalResponse(
                        success=False,
                        message=f"Brand {brand_id} not found"
                    )
                
                return SignalResponse(
                    success=True,
                    message="Brand information retrieved successfully",
                    data=brand.to_dict()
                )
                
            except Exception as e:
                LOG.error("Error getting brand info: %s", e)
                return SignalResponse(
                    success=False,
                    message=str(e)
                )
        
        @self.app.get("/metrics")
        async def get_metrics() -> MetricsResponse:
            """Get metrics endpoint."""
            try:
                processing_metrics = self.metrics_collector.get_all_processing_metrics()
                brand_metrics = self.metrics_collector.get_all_brand_metrics()
                
                return MetricsResponse(
                    processing_metrics=processing_metrics,
                    brand_metrics=brand_metrics
                )
                
            except Exception as e:
                LOG.error("Error getting metrics: %s", e)
                return MetricsResponse(
                    processing_metrics={},
                    brand_metrics={}
                )
        
        @self.app.get("/metrics/{brand_id}")
        async def get_brand_metrics(brand_id: str) -> SignalResponse:
            """Get brand metrics endpoint."""
            try:
                metrics = self.metrics_collector.get_processing_metrics(brand_id)
                brand_metrics = self.metrics_collector.get_brand_metrics(brand_id)
                
                return SignalResponse(
                    success=True,
                    message="Brand metrics retrieved successfully",
                    data={
                        "processing_metrics": metrics,
                        "brand_metrics": brand_metrics
                    }
                )
                
            except Exception as e:
                LOG.error("Error getting brand metrics: %s", e)
                return SignalResponse(
                    success=False,
                    message=str(e)
                )
        
        @self.app.post("/brands")
        async def register_brand(config: BrandConfigRequest) -> SignalResponse:
            """Register brand endpoint."""
            try:
                # Create brand configuration
                brand_config = BrandConfiguration(
                    brand_id=config.brand_id,
                    domain=config.domain,
                    branding=BrandBranding(
                        brand_name=config.name,
                        domain=config.domain
                    ),
                    features=BrandFeatures(),
                    pricing=BrandPricing()
                )
                
                # Register brand
                success = self.brand_manager.register_brand(brand_config)
                if success:
                    return SignalResponse(
                        success=True,
                        message="Brand registered successfully",
                        data=brand_config.to_dict()
                    )
                else:
                    return SignalResponse(
                        success=False,
                        message="Brand registration failed"
                    )
                
            except Exception as e:
                LOG.error("Error registering brand: %s", e)
                return SignalResponse(
                    success=False,
                    message=str(e)
                )
        
        @self.app.get("/brands")
        async def get_all_brands() -> SignalResponse:
            """Get all brands endpoint."""
            try:
                brands = self.brand_manager.get_all_brands()
                return SignalResponse(
                    success=True,
                    message="Brands retrieved successfully",
                    data=[brand.to_dict() for brand in brands]
                )

            except Exception as e:
                LOG.error("Error getting brands: %s", e)
                return SignalResponse(
                    success=False,
                    message=str(e)
                )

        # ── Scalev Webhook ──────────────────────────────────────────

        @self.app.post("/api/payments/notify")
        async def scalev_webhook(request: Request):
            """Scalev payment callback webhook.

            Verifies HMAC-SHA256 signature, extracts order_id + status,
            and marks the order as PAID in the local store."""
            try:
                body = await request.body()
                signature = request.headers.get("X-Scalev-Signature", "")

                from unified_bot.adapters.payment_adapter import ScalevAdapter
                adapter = ScalevAdapter()
                await adapter.initialize()

                if not adapter.verify_webhook_signature(body, signature):
                    return JSONResponse(
                        {"success": False, "error": "Invalid signature"},
                        status_code=403,
                    )

                data = json.loads(body) if body else {}
                order_id = data.get("order_id") or data.get("data", {}).get("order_id", "")
                status = data.get("status") or data.get("data", {}).get("status", "")
                variant_id = data.get("variant_id") or data.get("data", {}).get("variant_id", 0)

                if not order_id:
                    return JSONResponse(
                        {"success": False, "error": "Missing order_id"},
                        status_code=400,
                    )

                if status.upper() == "PAID":
                    plan_key = adapter.variant_id_to_plan(int(variant_id))
                    ok, row = adapter.mark_order_paid(order_id, plan_key)
                    LOG.info(
                        "Scalev webhook: order=%s status=%s plan=%s",
                        order_id, status, plan_key,
                    )
                    return {"success": True, "paid": True, "order_id": order_id, "plan_key": plan_key}

                LOG.info("Scalev webhook: order=%s status=%s (not PAID, skipped)", order_id, status)
                return {"success": True, "paid": False, "order_id": order_id}

            except Exception as exc:
                LOG.warning("Scalev webhook error: %s", exc)
                return JSONResponse(
                    {"success": False, "error": str(exc)[:200]},
                    status_code=400,
                )
    
    async def start(self) -> None:
        """Start API service."""
        LOG.info("Starting Unified API service on %s:%s", self.config.host, self.config.port)
        
        # TODO: Start FastAPI server
        # This would normally use uvicorn or similar ASGI server
        # For now, we'll just log that it's starting
        
        LOG.info("Unified API service started")
    
    async def shutdown(self) -> None:
        """Shutdown API service."""
        LOG.info("Unified API service shutdown completed")
# BrandBranding and BrandFeatures imports (defined in separate file to avoid circular imports)
from unified_bot.whitelabel import BrandBranding, BrandFeatures

import time