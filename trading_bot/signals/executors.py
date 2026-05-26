#!/usr/bin/env python3
"""
Trade executors for different execution strategies
SOLID: Interface Segregation, Liskov Substitution
DRY: Reusable executor implementations
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import logging
import time

from .models import Signal, Position

logger = logging.getLogger(__name__)


class ITradeExecutor(ABC):
    """Interface for trade execution (Dependency Inversion)"""
    
    @abstractmethod
    def execute_trade(self, signal: Signal) -> bool:
        """Execute a trade based on signal"""
        pass
    
    @abstractmethod
    def close_position(self, symbol: str, reason: str = "manual") -> bool:
        """Close an open position immediately"""
        pass
    
    @abstractmethod
    def get_open_positions(self) -> List[Position]:
        """Get all open positions"""
        pass
    
    @abstractmethod
    def get_balance(self) -> float:
        """Get current balance"""
        pass


class DemoTradeExecutor(ITradeExecutor):
    """Demo/paper trading executor with position tracking"""
    
    def __init__(self, initial_balance: float = 10000):
        self.balance = initial_balance
        self.trades = []
        self.open_positions: Dict[str, Position] = {}  # symbol -> Position
    
    def execute_trade(self, signal: Signal) -> bool:
        """Simulate trade execution with position checking"""
        logger.info(f"📝 DEMO: Would execute {signal.signal_type} trade")
        logger.info(f"   Symbol: {signal.symbol}")
        logger.info(f"   Entry: ${signal.entry_price}")
        logger.info(f"   Stop: ${signal.stop_loss}")
        logger.info(f"   TP1: ${signal.take_profit_1}")
        if signal.take_profit_2:
            logger.info(f"   TP2: ${signal.take_profit_2}")
        if signal.take_profit_3:
            logger.info(f"   TP3: ${signal.take_profit_3}")
        
        # Check for existing position to prevent double opening
        symbol_upper = signal.symbol.upper().replace('/USDT', '')
        if symbol_upper in self.open_positions:
            logger.info(f"⏭️  Skipping {signal.symbol} - position already open in demo")
            logger.info(f"   Existing position: {self.open_positions[symbol_upper].side} {self.open_positions[symbol_upper].quantity} @ ${self.open_positions[symbol_upper].entry_price}")
            return False
        
        # Create position
        position = Position(
            position_id=f"{signal.symbol}_{int(time.time())}",
            symbol=signal.symbol,
            side=signal.side,
            entry_price=signal.entry_price,
            quantity=100.0,  # Demo quantity
            signal_id=signal.signal_id,
            opened_at=time.strftime('%Y-%m-%d %H:%M:%S'),
            position_type=signal.signal_type
        )
        
        # Track open position (use normalized symbol)
        self.open_positions[symbol_upper] = position
        
        self.trades.append({
            'signal': signal,
            'position': position,
            'timestamp': time.time(),
            'status': 'open'
        })
        
        logger.info(f"✅ Position opened: {signal.symbol} ({len(self.open_positions)} open positions)")
        return True
    
    def close_position(self, symbol: str, reason: str = "manual") -> bool:
        """Close an open position immediately"""
        symbol_upper = symbol.upper().replace('/USDT', '')
        
        if symbol_upper not in self.open_positions:
            logger.warning(f"⚠️  No open position for {symbol_upper}")
            return False
        
        position = self.open_positions[symbol_upper]
        
        logger.info(f"🔴 CLOSING POSITION: {symbol_upper}")
        logger.info(f"   Reason: {reason}")
        logger.info(f"   Entry: ${position.entry_price}")
        logger.info(f"   Side: {position.side}")
        logger.info(f"   Type: {position.position_type}")
        
        # Remove from open positions
        del self.open_positions[symbol_upper]
        
        # Update trade status
        for trade in self.trades:
            if trade.get('position') and trade['position'].symbol == symbol_upper:
                trade['status'] = 'closed'
                trade['close_reason'] = reason
                trade['closed_at'] = time.time()
        
        logger.info(f"✅ Position closed: {symbol_upper} ({len(self.open_positions)} remaining)")
        return True
    
    def get_open_positions(self) -> List[Position]:
        """Get all open positions"""
        return list(self.open_positions.values())
    
    def get_balance(self) -> float:
        """Get current balance"""
        return self.balance

