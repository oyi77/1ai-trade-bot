# Unified Bot Main Application
# Entry point for the unified multi-brand trading bot

"""
Unified Bot Main Application

This module is the entry point for the unified multi-brand trading bot,
combining the core engine, API layer, and whitelabel management system.
"""

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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

LOG = logging.getLogger(__name__)
class UnifiedBot:
    """
    Unified trading bot for multi-brand operations.
    
    This class combines the core signal engine, API layer, and whitelabel management
    system into a single unified trading bot application.
    """
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self.brand_manager = BrandManager()
        self.metrics_collector = MetricsCollector()
        self.signal_engine: UnifiedSignalEngine
        self.api_service: UnifiedAPIService
        
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