#!/usr/bin/env python3
"""
Data caching and persistence layer
SOLID: Single Responsibility - handles all data caching
DRY: Reusable caching logic for notifications and signals
KISS: Simple JSON-based storage
"""

from typing import List, Dict, Optional, Set
from pathlib import Path
from dataclasses import asdict
from datetime import datetime
import json
import logging

from .models import Notification, Signal

logger = logging.getLogger(__name__)


class NotificationCache:
    """Persistent notification cache with full history"""
    
    def __init__(self, cache_file: str = ".notifications_history.json"):
        self.cache_file = Path(cache_file)
        self.notifications: List[Dict] = []
        self.notification_ids: Set[str] = set()
        self._load()
    
    def _load(self) -> None:
        """Load notifications from cache file"""
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text())
                self.notifications = data.get('notifications', [])
                self.notification_ids = {n['notification_id'] for n in self.notifications}
                logger.info(f"✅ Loaded {len(self.notifications)} notifications from cache")
            except Exception as e:
                logger.error(f"Failed to load cache: {e}")
                self.notifications = []
                self.notification_ids = set()
    
    def _save(self) -> None:
        """Save notifications to cache file"""
        try:
            data = {
                'last_updated': datetime.now().isoformat(),
                'total_count': len(self.notifications),
                'notifications': self.notifications
            }
            self.cache_file.write_text(json.dumps(data, indent=2))
            logger.debug(f"💾 Saved {len(self.notifications)} notifications to cache")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
    
    def add(self, notification: Notification) -> bool:
        """Add notification to cache if new"""
        if notification.notification_id in self.notification_ids:
            return False
        
        notif_dict = {
            'notification_id': notification.notification_id,
            'content': notification.content,
            'symbol': notification.symbol,
            'timestamp': notification.timestamp,  # Already a string from Firebase
            'cached_at': datetime.now().isoformat()
        }
        
        self.notifications.append(notif_dict)
        self.notification_ids.add(notification.notification_id)
        self._save()
        return True
    
    def add_batch(self, notifications: List[Notification]) -> int:
        """Add multiple notifications, return count of new ones"""
        new_count = 0
        for notif in notifications:
            if self.add(notif):
                new_count += 1
        return new_count
    
    def get_all(self) -> List[Dict]:
        """Get all cached notifications"""
        return self.notifications
    
    def get_latest(self, limit: int = 50) -> List[Dict]:
        """Get latest N notifications"""
        return self.notifications[-limit:] if self.notifications else []
    
    def is_cached(self, notification_id: str) -> bool:
        """Check if notification is already cached"""
        return notification_id in self.notification_ids
    
    def clear(self) -> None:
        """Clear all cached notifications"""
        self.notifications = []
        self.notification_ids = set()
        self._save()
        logger.info("🗑️  Cleared notification cache")


class SignalCache:
    """Cache for trading signals with deduplication"""
    
    def __init__(self, cache_file: str = ".signals_cache.json"):
        self.cache_file = Path(cache_file)
        self.signals: Dict[str, Dict] = {}
        self._load()
    
    def _load(self) -> None:
        """Load signals from cache"""
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text())
                self.signals = data.get('signals', {})
                logger.info(f"✅ Loaded {len(self.signals)} signals from cache")
            except Exception as e:
                logger.error(f"Failed to load signal cache: {e}")
                self.signals = {}
    
    def _save(self) -> None:
        """Save signals to cache"""
        try:
            data = {
                'last_updated': datetime.now().isoformat(),
                'total_count': len(self.signals),
                'signals': self.signals
            }
            self.cache_file.write_text(json.dumps(data, indent=2))
            logger.debug(f"💾 Saved {len(self.signals)} signals to cache")
        except Exception as e:
            logger.error(f"Failed to save signal cache: {e}")
    
    def add(self, signal: Signal) -> bool:
        """Add signal to cache if new"""
        if signal.signal_id in self.signals:
            return False
        
        self.signals[signal.signal_id] = {
            'signal_id': signal.signal_id,
            'symbol': signal.symbol,
            'side': signal.side,
            'entry_price': signal.entry_price,
            'stop_loss': signal.stop_loss,
            'take_profit': signal.take_profit,
            'type': signal.type,
            'timestamp': signal.timestamp,  # Already a string from Firebase
            'cached_at': datetime.now().isoformat()
        }
        
        self._save()
        return True
    
    def is_cached(self, signal_id: str) -> bool:
        """Check if signal is cached"""
        return signal_id in self.signals
    
    def get_all(self) -> Dict[str, Dict]:
        """Get all cached signals"""
        return self.signals


class DataCache:
    """Unified cache manager for all data types"""
    
    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path)
        self.notifications = NotificationCache(self.base_path / ".notifications_history.json")
        self.signals = SignalCache(self.base_path / ".signals_cache.json")
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            'total_notifications': len(self.notifications.notifications),
            'total_signals': len(self.signals.signals)
        }

