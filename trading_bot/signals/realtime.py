#!/usr/bin/env python3
"""
Real-time data listeners for Firebase
SOLID: Single Responsibility - handles real-time data streaming
DRY: Reusable listener patterns
KISS: Simple event-driven architecture
"""

from typing import Callable, Optional, List, Dict
from datetime import datetime
from threading import Thread, Event
from queue import Queue
import time
import logging

from .models import Signal, Notification
from .auth import FirebaseClient
from .cache import DataCache

logger = logging.getLogger(__name__)


class RealtimeListener:
    """Base real-time listener with event-driven callbacks"""
    
    def __init__(
        self,
        client: FirebaseClient,
        collection: str,
        poll_interval: float = 2.0,
        page_size: int = 50
    ):
        self.client = client
        self.collection = collection
        self.poll_interval = poll_interval
        self.page_size = page_size
        self.running = False
        self.stop_event = Event()
        self.thread: Optional[Thread] = None
        self.last_check: Optional[datetime] = None
    
    def start(self) -> None:
        """Start the real-time listener"""
        if self.running:
            logger.warning(f"{self.collection} listener already running")
            return
        
        self.running = True
        self.stop_event.clear()
        self.thread = Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        logger.info(f"🎧 Started real-time listener for {self.collection}")
    
    def stop(self) -> None:
        """Stop the real-time listener"""
        if not self.running:
            return
        
        self.running = False
        self.stop_event.set()
        
        if self.thread:
            self.thread.join(timeout=5)
        
        logger.info(f"⏹️  Stopped listener for {self.collection}")
    
    def _listen_loop(self) -> None:
        """Main listening loop"""
        while self.running and not self.stop_event.is_set():
            try:
                self._fetch_and_process()
            except Exception as e:
                logger.error(f"Listener error: {e}")
            
            # Short sleep for near real-time updates
            self.stop_event.wait(self.poll_interval)
    
    def _fetch_and_process(self) -> None:
        """Fetch and process new data - override in subclasses"""
        raise NotImplementedError


class NotificationListener(RealtimeListener):
    """Real-time notification listener with caching"""
    
    def __init__(
        self,
        client: FirebaseClient,
        cache: DataCache,
        on_notification: Optional[Callable[[Notification], None]] = None,
        poll_interval: float = 2.0
    ):
        super().__init__(client, 'notifications', poll_interval, page_size=100)
        self.cache = cache
        self.on_notification = on_notification
        self.queue = Queue()
    
    def _fetch_and_process(self) -> None:
        """Fetch new notifications and trigger callbacks"""
        result = self.client.get_notifications(page_size=self.page_size)
        
        if not result:
            return
        
        docs = result.get('documents', [])
        
        if not docs:
            return
        
        new_count = 0
        for doc in docs:
            try:
                parsed = self.client.parse_document(doc)
                notification = Notification.from_firestore(parsed)
                
                # Check if new (not in cache)
                if not self.cache.notifications.is_cached(notification.notification_id):
                    # Add to cache
                    self.cache.notifications.add(notification)
                    
                    # Trigger callback
                    if self.on_notification:
                        self.on_notification(notification)
                    
                    # Add to queue for batch processing
                    self.queue.put(notification)
                    new_count += 1
                    
            except Exception as e:
                logger.error(f"Failed to process notification: {e}")
        
        if new_count > 0:
            logger.info(f"🔔 {new_count} new notification(s)")
    
    def get_queued(self) -> List[Notification]:
        """Get all queued notifications"""
        notifications = []
        while not self.queue.empty():
            notifications.append(self.queue.get())
        return notifications


class SignalListener(RealtimeListener):
    """Real-time signal listener for spot/futures"""
    
    def __init__(
        self,
        client: FirebaseClient,
        collection: str,
        signal_type: str,
        cache: DataCache,
        on_signal: Optional[Callable[[Signal], None]] = None,
        poll_interval: float = 3.0
    ):
        super().__init__(client, collection, poll_interval, page_size=50)
        self.signal_type = signal_type
        self.cache = cache
        self.on_signal = on_signal
        self.queue = Queue()
    
    def _fetch_and_process(self) -> None:
        """Fetch new signals and trigger callbacks"""
        result = self.client.get_collection(self.collection, page_size=self.page_size)
        
        if not result:
            return
        
        docs = result.get('documents', [])
        
        if not docs:
            return
        
        new_count = 0
        for doc in docs:
            try:
                parsed = self.client.parse_document(doc)
                parsed['type'] = self.signal_type
                signal = Signal.from_firestore(parsed)
                
                # Check if new (not in cache)
                if not self.cache.signals.is_cached(signal.signal_id):
                    # Add to cache
                    self.cache.signals.add(signal)
                    
                    # Trigger callback
                    if self.on_signal:
                        self.on_signal(signal)
                    
                    # Add to queue
                    self.queue.put(signal)
                    new_count += 1
                    
            except Exception as e:
                logger.error(f"Failed to process signal: {e}")
        
        if new_count > 0:
            logger.info(f"📊 {new_count} new {self.signal_type} signal(s)")
    
    def get_queued(self) -> List[Signal]:
        """Get all queued signals"""
        signals = []
        while not self.queue.empty():
            signals.append(self.queue.get())
        return signals


class RealtimeManager:
    """Manages all real-time listeners"""
    
    def __init__(
        self,
        client: FirebaseClient,
        cache: DataCache,
        on_notification: Optional[Callable[[Notification], None]] = None,
        on_spot_signal: Optional[Callable[[Signal], None]] = None,
        on_futures_signal: Optional[Callable[[Signal], None]] = None
    ):
        self.client = client
        self.cache = cache
        
        # Create listeners
        self.notification_listener = NotificationListener(
            client, cache, on_notification, poll_interval=1.5
        )
        
        self.spot_listener = SignalListener(
            client, 'signals', 'spot', cache, on_spot_signal, poll_interval=3.0
        )
        
        self.futures_listener = SignalListener(
            client, 'futures', 'futures', cache, on_futures_signal, poll_interval=3.0
        )
        
        self.listeners = [
            self.notification_listener,
            self.spot_listener,
            self.futures_listener
        ]
    
    def start_all(self, enable_futures: bool = True, enable_notifications: bool = True) -> None:
        """Start all enabled listeners"""
        self.spot_listener.start()
        
        if enable_futures:
            self.futures_listener.start()
        
        if enable_notifications:
            self.notification_listener.start()
        
        logger.info("🚀 All real-time listeners started")
    
    def stop_all(self) -> None:
        """Stop all listeners"""
        for listener in self.listeners:
            listener.stop()
        
        logger.info("⏹️  All listeners stopped")
    
    def get_queued_data(self) -> Dict[str, List]:
        """Get all queued data from listeners"""
        return {
            'notifications': self.notification_listener.get_queued(),
            'spot_signals': self.spot_listener.get_queued(),
            'futures_signals': self.futures_listener.get_queued()
        }

