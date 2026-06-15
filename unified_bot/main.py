"""Entry point: initializes engine, API, PoC adapters, and runs the bot loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from unified_bot.whitelabel import BrandManager, BrandConfiguration
from unified_bot.core.engine import UnifiedSignalEngine
from unified_bot.core.metrics import MetricsCollector
from unified_bot.api.unified_api import UnifiedAPIService, APIConfig

# ── PoC Adapters ──
from unified_bot.adapters import (
    SatpamAdapter,
    ScalevAdapter,
    SubscriptionAdapter,
    SignalBridgeAdapter,
    EngineConsensusAdapter,
    LicenseManagerAdapter,
    ExpiryReminderAdapter,
    DailyReportAdapter,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

LOG = logging.getLogger(__name__)
class UnifiedBot:
    """Orchestrates engine, API, adapters and background tasks."""
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self.brand_manager = BrandManager()
        self.metrics_collector = MetricsCollector()
        self.signal_engine: UnifiedSignalEngine
        self.api_service: UnifiedAPIService
        
        # ── PoC Adapters (real instances) ──
        self.satpam: SatpamAdapter = SatpamAdapter()
        self.scalev: ScalevAdapter = ScalevAdapter()
        self.subscription: SubscriptionAdapter = SubscriptionAdapter()
        self.signal_bridge: SignalBridgeAdapter = SignalBridgeAdapter()
        self.engine_consensus: EngineConsensusAdapter = EngineConsensusAdapter()
        self.license_manager: LicenseManagerAdapter = LicenseManagerAdapter()
        self.expiry_reminder: ExpiryReminderAdapter = ExpiryReminderAdapter()
        self.daily_report: DailyReportAdapter = DailyReportAdapter()
        
        # Application state
        self.running = False
        self.tasks = []
        
        LOG.info("UnifiedBot initialized")
    
    async def initialize(self) -> bool:
        """
        Initialize the unified bot.
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Load configuration
            await self._load_config()
            
            # Initialize signal engine
            await self._initialize_signal_engine()
            
            # Initialize API service
            await self._initialize_api_service()
            
            # Initialize PoC adapters
            await self._initialize_adapters()
            
            # Start background services
            await self._start_background_services()
            
            self.running = True
            LOG.info("UnifiedBot initialized successfully")
            return True
            
        except Exception as e:
            LOG.error("Failed to initialize UnifiedBot: %s", e)
            return False
    
    async def _load_config(self) -> None:
        """Load application configuration."""
        # Load brand configurations
        brands = self.brand_manager.get_all_brands()
        if brands:
            LOG.info("Loaded %d brands", len(brands))
        else:
            LOG.warning("No brands found, creating default brand")
            # Create default brand for testing
            default_brand = BrandConfiguration(
                brand_id="default",
                domain="https://default.example.com",
                branding=BrandBranding(
                    brand_name="Default Trading Bot",
                    domain="https://default.example.com"
                ),
                features=BrandFeatures(),
                pricing=BrandPricing()
            )
            self.brand_manager.register_brand(default_brand)
    
    async def _initialize_signal_engine(self) -> None:
        """Initialize signal engine."""
        # Create signal engine
        self.signal_engine = UnifiedSignalEngine(self.brand_manager)
        
        # Initialize signal engine for default brand
        await self.signal_engine.initialize("default")
        
        # Set up signal callback
        async def signal_callback(signal):
            LOG.info("Signal received: %s %s %.2f", 
                    signal.symbol, signal.action, signal.confidence)
            # TODO: Process signal through trading pipeline
        
        self.signal_engine.set_signal_callback(signal_callback)
    
    async def _initialize_api_service(self) -> None:
        """Initialize API service."""
        # Create API configuration
        api_config = APIConfig()
        
        # Create API service
        self.api_service = UnifiedAPIService(
            config=api_config,
            brand_manager=self.brand_manager,
            signal_engine=self.signal_engine,
            metrics_collector=self.metrics_collector
        )
        
        # Start API service
        await self.api_service.start()
    
    async def _initialize_adapters(self) -> None:
        """Initialize all PoC adapters with default configs."""
        LOG.info("Initializing PoC adapters...")
        
        self.satpam = SatpamAdapter()
        self.scalev = ScalevAdapter()
        self.subscription = SubscriptionAdapter()
        self.signal_bridge = SignalBridgeAdapter()
        self.engine_consensus = EngineConsensusAdapter()
        self.license_manager = LicenseManagerAdapter()
        self.expiry_reminder = ExpiryReminderAdapter()
        self.daily_report = DailyReportAdapter()
        
        # Init adapters that need async setup (non-blocking concurrent)
        results = await asyncio.gather(
            self.signal_bridge.initialize(),
            self.subscription.initialize(),
            self.scalev.initialize(),
            self.engine_consensus.initialize(),
            self.license_manager.initialize(),
            self.expiry_reminder.initialize(),
            self.daily_report.initialize(),
            return_exceptions=True,
        )
        
        adapter_names = [
            "signal_bridge", "subscription", "scalev",
            "engine_consensus", "license_manager", "expiry_reminder", "daily_report",
        ]
        for name, result in zip(adapter_names, results):
            if isinstance(result, Exception):
                LOG.warning("Adapter %s init returned error: %s", name, result)
            else:
                LOG.info("Adapter %s initialized: %s", name, result)
        
        LOG.info("PoC adapters initialization complete")
    
    async def _start_background_services(self) -> None:
        """Start background services."""
        # Start signal scheduler
        self.tasks.append(asyncio.create_task(self._signal_scheduler()))
        
        # Start metrics cleanup
        self.tasks.append(asyncio.create_task(self._metrics_cleanup()))
        
        LOG.info("Background services started")
    
    async def _signal_scheduler(self) -> None:
        """Signal scheduler background task."""
        try:
            while self.running:
                # Process signals for all active brands
                for brand in self.brand_manager.get_all_brands():
                    if brand.status == "active":
                        await self.signal_engine.process_ticks(
                            ticks=[],  # TODO: Get actual ticks from data source
                            brand_id=brand.brand_id
                        )
                
                await asyncio.sleep(60)  # Run every minute
                
        except Exception as e:
            LOG.error("Error in signal scheduler: %s", e)
    
    async def _metrics_cleanup(self) -> None:
        """Metrics cleanup background task."""
        try:
            while self.running:
                # Clean up old metrics
                await self.metrics_collector.cleanup_old_metrics()
                
                await asyncio.sleep(3600)  # Run every hour
                
        except Exception as e:
            LOG.error("Error in metrics cleanup: %s", e)
    
    async def shutdown(self) -> None:
        """Shutdown the unified bot."""
        try:
            self.running = False
            
            # Stop all background tasks
            for task in self.tasks:
                task.cancel()
            
            # Wait for tasks to complete
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            
            # Shutdown adapters
            adapter_shutdowns = []
            for attr in (
                "satpam", "scalev", "subscription", "signal_bridge",
                "engine_consensus", "license_manager", "expiry_reminder", "daily_report",
            ):
                adapter = getattr(self, attr, None)
                if adapter:
                    adapter_shutdowns.append(adapter.shutdown())
            if adapter_shutdowns:
                await asyncio.gather(*adapter_shutdowns, return_exceptions=True)
            
            # Shutdown API service
            if self.api_service:
                await self.api_service.shutdown()
            
            # Shutdown signal engine
            if self.signal_engine:
                await self.signal_engine.shutdown()
            
            # Shutdown metrics collector
            if self.metrics_collector:
                await self.metrics_collector.shutdown()
            
            LOG.info("UnifiedBot shutdown completed")
            
        except Exception as e:
            LOG.error("Error during shutdown: %s", e)
    
    def setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        
        def signal_handler(signum, frame):
            LOG.info("Received signal %s, shutting down...", signum)
            asyncio.create_task(self.shutdown())
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def run(self) -> None:
        """Run the unified bot."""
        # Setup signal handlers
        self.setup_signal_handlers()
        
        # Initialize
        success = await self.initialize()
        if not success:
            LOG.error("Failed to initialize UnifiedBot")
            return
        
        try:
            # Run indefinitely
            while self.running:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            LOG.info("Keyboard interrupt received")
        finally:
            # Shutdown
            await self.shutdown()
async def main() -> None:
    """Main entry point."""
    # Create and run unified bot
    bot = UnifiedBot()
    
    # Run the bot
    await bot.run()
if __name__ == "__main__":
    asyncio.run(main())