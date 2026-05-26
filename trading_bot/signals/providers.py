#!/usr/bin/env python3
"""
Signal providers for fetching trading signals
SOLID: Interface Segregation, Dependency Inversion
DRY: Reusable provider implementations
"""

from abc import ABC, abstractmethod
from typing import List, Optional
import logging

from .models import Signal, Notification
from .auth import FirebaseClient

logger = logging.getLogger(__name__)


class ISignalProvider(ABC):
    """Interface for signal providers (Dependency Inversion)"""
    
    @abstractmethod
    def get_signals(self, limit: int = 10) -> List[Signal]:
        """Get spot signals"""
        pass
    
    @abstractmethod
    def get_futures_signals(self, limit: int = 10) -> List[Signal]:
        """Get futures signals"""
        pass
    
    @abstractmethod
    def get_notifications(self, limit: int = 50) -> List[Notification]:
        """Get notifications"""
        pass


class FirebaseSignalProvider(ISignalProvider):
    """Firebase-based signal provider with auto-refresh"""
    
    def __init__(self, api_key: str, project_id: str, refresh_token: Optional[str] = None):
        """
        Initialize provider
        
        Args:
            api_key: Firebase API key
            project_id: Firebase project ID
            refresh_token: Optional initial refresh token
        """
        self.client = FirebaseClient(api_key, project_id, refresh_token)
    
    def get_signals(self, limit: int = 10) -> List[Signal]:
        """Get spot trading signals"""
        docs = self.client.get_collection('signals', page_size=limit)
        if not docs:
            return []
        
        return self._parse_signals(docs, 'spot')
    
    def get_futures_signals(self, limit: int = 10) -> List[Signal]:
        """Get futures trading signals"""
        docs = self.client.get_collection('futures', page_size=limit)
        if not docs:
            return []
        
        return self._parse_signals(docs, 'futures')
    
    def get_notifications(self, limit: int = 50) -> List[Notification]:
        """Get real-time notifications"""
        docs = self.client.get_notifications(page_size=limit)
        if not docs:
            return []
        
        notifications = []
        for doc in docs:
            parsed = self.client.parse_document(doc)
            try:
                notif = Notification.from_firestore(parsed)
                notifications.append(notif)
            except Exception as e:
                logger.error(f"Failed to parse notification: {e}")
        
        return notifications
    
    def _parse_signals(self, docs: List, signal_type: str) -> List[Signal]:
        """Parse signal documents (DRY)"""
        signals = []
        for doc in docs:
            parsed = self.client.parse_document(doc)
            parsed['type'] = signal_type
            try:
                signal = Signal.from_firestore(parsed)
                signals.append(signal)
            except Exception as e:
                logger.error(f"Failed to parse signal: {e}")
        
        return signals

