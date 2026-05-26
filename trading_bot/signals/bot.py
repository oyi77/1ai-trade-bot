#!/usr/bin/env python3
"""
Main trading bot orchestrator with real-time listeners
SOLID: Single Responsibility, Open/Closed
DRY: Reusable bot logic
KISS: Event-driven architecture
"""

from typing import Dict, Optional, Set, List, Any
import logging
import time

from .models import Signal, Notification
from .executors import ITradeExecutor
from .auth import FirebaseClient
from .cache import DataCache
from .realtime import RealtimeManager
from .tracking_manager import TrackingManager
from .utils import SignalType

logger = logging.getLogger(__name__)


class TradingBot:
    """
    Real-time trading bot with event-driven architecture
    Uses Firebase listeners instead of polling with sleep
    """
    
    def __init__(
        self,
        firebase_client: FirebaseClient,
        trade_executor: ITradeExecutor,
        enable_spot: bool = True,
        enable_futures: bool = False,
        enable_notifications: bool = True,
        cache_path: str = "."
    ):
        """
        Initialize trading bot with real-time capabilities
        
        Args:
            firebase_client: Firebase client for data access
            trade_executor: Trade execution handler
            enable_spot: Whether to trade spot
            enable_futures: Whether to trade futures
            enable_notifications: Whether to enable notifications
            cache_path: Path for cache files
        """
        self.client = firebase_client
        self.trade_executor = trade_executor
        self.enable_spot = enable_spot
        self.enable_futures = enable_futures
        self.enable_notifications = enable_notifications
        
        # Cache for all data with history
        self.cache = DataCache(cache_path)
        
        # Tracking manager for positions, balance, and trading history
        self.tracking = TrackingManager(cache_path)
        
        # Real-time manager with callbacks
        self.realtime = RealtimeManager(
            client=firebase_client,
            cache=self.cache,
            on_notification=self._on_notification,
            on_spot_signal=self._on_spot_signal,
            on_futures_signal=self._on_futures_signal
        )
        
        self.processed_signals: Set[str] = set()
        self.running = False
        
        # Start periodic tracking updates
        self._start_tracking_updates()
    
    def _on_notification(self, notification: Notification) -> None:
        """Real-time notification callback with immediate stop handling"""
        logger.info(f"🔔 NEW: {notification.content[:80]}{'...' if len(notification.content) > 80 else ''}")
        
        # Check for immediate stop/close signals
        if notification.is_stop_signal:
            symbol = notification.extracted_symbol
            
            if symbol:
                logger.warning(f"⚠️  STOP SIGNAL DETECTED for {symbol}!")
                logger.warning(f"   Reason: {notification.content}")
                
                # Determine close reason
                if notification.is_stop_loss:
                    reason = f"STOP LOSS - {notification.content}"
                else:
                    reason = f"CLOSE - {notification.content}"
                
                # Immediately close position
                if self.trade_executor.close_position(symbol, reason):
                    logger.info(f"✅ Position {symbol} closed due to notification")
                    # Track the position close
                    self.track_position_close(symbol, 0.0, reason)
                else:
                    logger.warning(f"⚠️  No open position found for {symbol}")
            else:
                # No symbol in notification - try smart fallback
                logger.warning("⚠️  Stop signal detected but no symbol identified")
                logger.warning(f"   Content: {notification.content}")
                
                # FALLBACK: If only ONE open position, close it
                open_positions = self.trade_executor.get_open_positions()
                
                if len(open_positions) == 1:
                    fallback_symbol = open_positions[0].symbol
                    logger.warning(f"🔄 FALLBACK: Only 1 open position, assuming {fallback_symbol}")
                    
                    if notification.is_stop_loss:
                        reason = f"STOP LOSS (auto-matched) - {notification.content}"
                    else:
                        reason = f"CLOSE (auto-matched) - {notification.content}"
                    
                    if self.trade_executor.close_position(fallback_symbol, reason):
                        logger.info(f"✅ Position {fallback_symbol} closed (auto-matched)")
                        # Track the position close
                        self.track_position_close(fallback_symbol, 0.0, reason)
                    else:
                        logger.error(f"❌ Failed to close {fallback_symbol}")
                elif len(open_positions) > 1:
                    logger.error(f"❌ Cannot determine which position to close!")
                    logger.error(f"   {len(open_positions)} open positions: {[p.symbol for p in open_positions]}")
                    logger.error(f"   Manual intervention required!")
                else:
                    logger.info(f"ℹ️  No open positions to close")
        
        if notification.symbol:
            logger.info(f"   Symbol: {notification.symbol}")
    
    def _on_spot_signal(self, signal: Signal) -> None:
        """Real-time spot signal callback"""
        if self.enable_spot:
            logger.info(f"📊 NEW SPOT SIGNAL: {signal.symbol} - {signal.side}")
            self._process_signal(signal)
    
    def _on_futures_signal(self, signal: Signal) -> None:
        """Real-time futures signal callback"""
        if self.enable_futures:
            logger.info(f"📊 NEW FUTURES SIGNAL: {signal.symbol} - {signal.side}")
            self._process_signal(signal)
    
    def _process_signal(self, signal: Signal) -> None:
        """Process trading signal with tracking"""
        try:
            # Avoid duplicate processing
            if signal.signal_id in self.processed_signals:
                logger.debug(f"Signal {signal.signal_id} already processed")
                return
            
            # Execute trade
            success = self.trade_executor.execute_trade(signal)
            
            if success:
                logger.info(f"✅ Executed {signal.signal_type.value} trade: {signal.symbol}")
                
                # Track the trade execution
                # Note: We need to get the actual order details from the executor
                # For now, we'll use placeholder values
                self.track_trade_execution(signal, "placeholder_order_id", signal.entry_price, 1.0)
                
                # Mark as processed
                self.processed_signals.add(signal.signal_id)
            else:
                logger.warning(f"⚠️  Failed to execute trade for {signal.symbol}")
                
        except Exception as e:
            logger.error(f"Failed to process signal {signal.symbol}: {e}")
    
    def start(self) -> None:
        """Start the trading bot with real-time listeners"""
        try:
            logger.info("🤖 Trading bot started in REAL-TIME mode")
            logger.info(f"Futures trading: {'enabled' if self.enable_futures else 'disabled'}")
            logger.info(f"Notifications: {'enabled' if self.enable_notifications else 'disabled'}")
            
            # Load cached data
            notifications_count = len(self.cache.notifications_history)
            signals_count = len(self.cache.signals_history)
            logger.info(f"📊 Cache: {notifications_count} notifications, {signals_count} signals")
            
            # Get current positions
            open_positions = self.trade_executor.get_open_positions()
            logger.info(f"📈 Open Positions: {len(open_positions)}")
            for position in open_positions:
                logger.info(f"   - {position.symbol} ({position.side}) @ ${position.entry_price}")
            
            # Process cached signals
            logger.info("🔄 Processing cached signals...")
            processed_count = 0
            
            for signal in self.cache.signals_history:
                if signal.signal_id not in self.processed_signals:
                    self._process_signal(signal)
                    processed_count += 1
            
            logger.info(f"✅ Processed {processed_count} cached signals")
            
            # Start real-time listeners
            self.realtime.start_listeners()
            self.running = True
            
            logger.info("")
            logger.info("============================================================")
            logger.info("🎧 Real-time listeners ACTIVE - monitoring Firebase...")
            logger.info("Press Ctrl+C to stop")
            logger.info("============================================================")
            logger.info("")
            
        except Exception as e:
            logger.error(f"Failed to start bot: {e}")
            raise
    
    def stop(self) -> None:
        """Stop the trading bot"""
        try:
            logger.info("🛑 Stopping trading bot...")
            self.running = False
            
            # Stop real-time listeners
            self.realtime.stop_listeners()
            
            # Export final tracking data
            self.export_tracking_data()
            
            logger.info("✅ Trading bot stopped")
            
        except Exception as e:
            logger.error(f"Failed to stop bot: {e}")
    
    def _start_tracking_updates(self):
        """Start periodic tracking updates"""
        try:
            # Update position prices every 30 seconds
            import threading
            def update_tracking():
                while self.running:
                    try:
                        if hasattr(self.trade_executor, 'exchange'):
                            self.tracking.update_position_prices(self.trade_executor.exchange)
                            self.tracking.add_balance_snapshot(self.trade_executor.exchange)
                    except Exception as e:
                        logger.debug(f"Tracking update failed: {e}")
                    time.sleep(30)
            
            tracking_thread = threading.Thread(target=update_tracking, daemon=True)
            tracking_thread.start()
            logger.info("📊 Started tracking updates")
            
        except Exception as e:
            logger.error(f"Failed to start tracking updates: {e}")
    
    def track_trade_execution(self, signal: Signal, order_id: str, entry_price: float, quantity: float):
        """Track a new trade execution"""
        try:
            position_id = self.tracking.add_position(signal, order_id, entry_price, quantity)
            logger.info(f"📊 Tracked new position: {signal.symbol} - ID: {position_id}")
            return position_id
        except Exception as e:
            logger.error(f"Failed to track trade execution: {e}")
            return ""
    
    def track_position_close(self, symbol: str, exit_price: float, exit_reason: str = "manual"):
        """Track a position close"""
        try:
            # Find position by symbol
            for position_id, position in self.tracking.positions.items():
                if position.symbol == symbol:
                    trade = self.tracking.close_position(position_id, exit_price, exit_reason)
                    if trade:
                        logger.info(f"📈 Tracked position close: {symbol} - PnL: ${trade.realized_pnl:.2f}")
                        return trade
            logger.warning(f"No active position found for {symbol}")
            return None
        except Exception as e:
            logger.error(f"Failed to track position close: {e}")
            return None
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get comprehensive portfolio summary"""
        try:
            return self.tracking.get_portfolio_summary()
        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return {}
    
    def get_trading_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trading history"""
        try:
            return self.tracking.get_trading_history(limit)
        except Exception as e:
            logger.error(f"Failed to get trading history: {e}")
            return []
    
    def export_tracking_data(self, export_dir: str = None) -> Dict[str, str]:
        """Export all tracking data"""
        try:
            return self.tracking.export_data(export_dir)
        except Exception as e:
            logger.error(f"Failed to export tracking data: {e}")
            return {}