#!/usr/bin/env python3
"""
Position management system
SOLID: Single Responsibility, Open/Closed
DRY: Reusable position tracking logic
KISS: Simple position management
SoC: Separation of concerns
"""

from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod
import logging

from .utils import ExchangeType, ExchangeDetector, SymbolUtils, ValidationUtils
from .models import Position

logger = logging.getLogger(__name__)


class IPositionStrategy(ABC):
    """Strategy interface for exchange-specific position tracking (Strategy Pattern)"""
    
    @abstractmethod
    def fetch_futures_positions(self, exchange) -> List[Dict[str, Any]]:
        """Fetch futures positions from exchange"""
        pass
    
    @abstractmethod
    def fetch_spot_positions(self, exchange) -> List[Dict[str, Any]]:
        """Fetch spot positions from exchange"""
        pass
    
    @abstractmethod
    def parse_futures_position(self, pos_data: Dict[str, Any]) -> Optional[Position]:
        """Parse futures position data"""
        pass
    
    @abstractmethod
    def parse_spot_position(self, currency: str, amount: float) -> Optional[Position]:
        """Parse spot position data"""
        pass


class BybitPositionStrategy(IPositionStrategy):
    """Bybit-specific position tracking strategy"""
    
    def fetch_futures_positions(self, exchange) -> List[Dict[str, Any]]:
        """Fetch futures positions from Bybit"""
        try:
            if hasattr(exchange, 'fetch_positions'):
                return exchange.fetch_positions()
            return []
        except Exception as e:
            logger.debug(f"Could not fetch futures positions: {e}")
            return []
    
    def fetch_spot_positions(self, exchange) -> List[Dict[str, Any]]:
        """Fetch spot positions from Bybit"""
        try:
            balance = exchange.fetch_balance()
            positions = []
            
            for currency, amounts in balance.items():
                # Skip non-currency keys
                if currency in ['info', 'timestamp', 'datetime', 'free', 'used', 'total', 'debt']:
                    continue
                
                # Handle different balance structures
                if isinstance(amounts, dict):
                    free_amount = float(amounts.get('free', 0))
                else:
                    free_amount = float(amounts) if amounts else 0
                
                if currency not in ['USDT', 'USDC'] and free_amount > 0.001:
                    positions.append({
                        'currency': currency,
                        'amount': free_amount
                    })
            
            return positions
            
        except Exception as e:
            logger.debug(f"Could not fetch spot positions: {e}")
            return []
    
    def parse_futures_position(self, pos_data: Dict[str, Any]) -> Optional[Position]:
        """Parse Bybit futures position data"""
        try:
            contracts = float(pos_data.get('contracts', 0))
            if contracts == 0:
                return None
            
            symbol = pos_data.get('symbol', '').replace('/USDT', '')
            
            return Position(
                position_id=pos_data.get('id', ''),
                symbol=symbol,
                side='BUY' if pos_data.get('side') == 'long' else 'SELL',
                entry_price=float(pos_data.get('entryPrice', 0)),
                quantity=contracts,
                signal_id='',
                opened_at=pos_data.get('timestamp', ''),
                position_type='futures'
            )
            
        except Exception as e:
            logger.error(f"Failed to parse futures position: {e}")
            return None
    
    def parse_spot_position(self, currency: str, amount: float) -> Optional[Position]:
        """Parse Bybit spot position data"""
        try:
            return Position(
                position_id=f"spot_{currency}",
                symbol=currency,
                side='BUY',
                entry_price=0.0,  # We don't know the entry price from balance
                quantity=amount,
                signal_id='',
                opened_at='',
                position_type='spot'
            )
            
        except Exception as e:
            logger.error(f"Failed to parse spot position: {e}")
            return None


class GenericPositionStrategy(IPositionStrategy):
    """Generic position tracking strategy for other exchanges"""
    
    def fetch_futures_positions(self, exchange) -> List[Dict[str, Any]]:
        """Fetch futures positions using generic methods"""
        try:
            if hasattr(exchange, 'fetch_positions'):
                return exchange.fetch_positions()
            return []
        except Exception as e:
            logger.debug(f"Could not fetch futures positions: {e}")
            return []
    
    def fetch_spot_positions(self, exchange) -> List[Dict[str, Any]]:
        """Fetch spot positions using generic methods"""
        try:
            balance = exchange.fetch_balance()
            positions = []
            
            for currency, amounts in balance.items():
                if currency in ['info', 'timestamp', 'datetime', 'free', 'used', 'total', 'debt']:
                    continue
                
                if isinstance(amounts, dict):
                    free_amount = float(amounts.get('free', 0))
                else:
                    free_amount = float(amounts) if amounts else 0
                
                if currency not in ['USDT', 'USDC'] and free_amount > 0.001:
                    positions.append({
                        'currency': currency,
                        'amount': free_amount
                    })
            
            return positions
            
        except Exception as e:
            logger.debug(f"Could not fetch spot positions: {e}")
            return []
    
    def parse_futures_position(self, pos_data: Dict[str, Any]) -> Optional[Position]:
        """Parse futures position data using generic format"""
        try:
            contracts = float(pos_data.get('contracts', 0))
            if contracts == 0:
                return None
            
            symbol = pos_data.get('symbol', '').replace('/USDT', '')
            
            return Position(
                position_id=pos_data.get('id', ''),
                symbol=symbol,
                side='BUY' if pos_data.get('side') == 'long' else 'SELL',
                entry_price=float(pos_data.get('entryPrice', 0)),
                quantity=contracts,
                signal_id='',
                opened_at=pos_data.get('timestamp', ''),
                position_type='futures'
            )
            
        except Exception as e:
            logger.error(f"Failed to parse futures position: {e}")
            return None
    
    def parse_spot_position(self, currency: str, amount: float) -> Optional[Position]:
        """Parse spot position data using generic format"""
        try:
            return Position(
                position_id=f"spot_{currency}",
                symbol=currency,
                side='BUY',
                entry_price=0.0,
                quantity=amount,
                signal_id='',
                opened_at='',
                position_type='spot'
            )
            
        except Exception as e:
            logger.error(f"Failed to parse spot position: {e}")
            return None


class PositionManager:
    """Manages position tracking operations (SoC)"""
    
    def __init__(self, exchange):
        """
        Initialize position manager
        
        Args:
            exchange: CCXT exchange instance
        """
        self.exchange = exchange
        self.exchange_type = ExchangeDetector.get_exchange_type(exchange)
        self.strategy = self._create_position_strategy()
    
    def _create_position_strategy(self) -> IPositionStrategy:
        """Create appropriate position strategy based on exchange type"""
        if self.exchange_type == ExchangeType.BYBIT:
            return BybitPositionStrategy()
        else:
            return GenericPositionStrategy()
    
    def get_open_positions(self) -> List[Position]:
        """
        Get all open positions (futures + spot)
        
        Returns:
            List of open positions
        """
        try:
            positions = []
            
            # Fetch futures positions
            futures_positions = self._fetch_futures_positions()
            positions.extend(futures_positions)
            
            # Fetch spot positions
            spot_positions = self._fetch_spot_positions()
            positions.extend(spot_positions)
            
            logger.info(f"📊 Found {len(positions)} open positions")
            return positions
            
        except Exception as e:
            logger.error(f"Failed to get open positions: {e}")
            return []
    
    def _fetch_futures_positions(self) -> List[Position]:
        """Fetch and parse futures positions"""
        try:
            raw_positions = self.strategy.fetch_futures_positions(self.exchange)
            positions = []
            
            for pos_data in raw_positions:
                position = self.strategy.parse_futures_position(pos_data)
                if position:
                    positions.append(position)
                    logger.info(f"✅ Found futures position: {position.symbol} - {position.side} - {position.quantity}")
            
            return positions
            
        except Exception as e:
            logger.error(f"Failed to fetch futures positions: {e}")
            return []
    
    def _fetch_spot_positions(self) -> List[Position]:
        """Fetch and parse spot positions"""
        try:
            raw_positions = self.strategy.fetch_spot_positions(self.exchange)
            positions = []
            
            for pos_data in raw_positions:
                position = self.strategy.parse_spot_position(
                    pos_data['currency'], pos_data['amount']
                )
                if position:
                    positions.append(position)
                    logger.info(f"✅ Found spot position: {position.symbol} = {position.quantity}")
            
            return positions
            
        except Exception as e:
            logger.error(f"Failed to fetch spot positions: {e}")
            return []
    
    def has_existing_position(self, symbol: str) -> bool:
        """
        Check if we already have an open position for this symbol
        Positions under $1 USD are considered as "no position" to allow re-entry
        
        Args:
            symbol: Trading symbol
            
        Returns:
            True if position exists and is >= $1 USD, False otherwise
        """
        try:
            open_positions = self.get_open_positions()
            
            for position in open_positions:
                # Normalize symbols for comparison
                pos_symbol = SymbolUtils.normalize_symbol(position.symbol)
                sig_symbol = SymbolUtils.normalize_symbol(symbol)
                
                if pos_symbol == sig_symbol:
                    # Calculate position value
                    if position.entry_price > 0:
                        position_value = position.quantity * position.entry_price
                    else:
                        # For spot positions with unknown entry price, estimate using current market price
                        try:
                            # Try to get current market price
                            market_symbol = f"{symbol}/USDT"
                            if hasattr(self.exchange, 'fetch_ticker'):
                                ticker = self.exchange.fetch_ticker(market_symbol)
                                current_price = ticker.get('last', 0)
                                position_value = position.quantity * current_price
                            else:
                                # If we can't get market price, assume it's small if quantity is very small
                                position_value = position.quantity * 0.01  # Conservative estimate
                        except Exception:
                            # If we can't get market price, assume it's small if quantity is very small
                            position_value = position.quantity * 0.01  # Conservative estimate
                    
                    # If position is under $1 USD, consider it as "no position"
                    if position_value < 1.0:
                        logger.info(f"🔄 Ignoring small position {symbol} - value ${position_value:.4f} < $1.00")
                        logger.info(f"   Small position: {position.side} {position.quantity} @ ${position.entry_price}")
                        continue
                    
                    logger.info(f"⏭️  Skipping {symbol} - position already open on exchange")
                    logger.info(f"   Existing position: {position.side} {position.quantity} @ ${position.entry_price} (${position_value:.2f})")
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check existing position for {symbol}: {e}")
            return False
    
    def get_existing_position(self, symbol: str) -> Optional[Position]:
        """
        Get existing position for a symbol if it exists
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Position object if exists, None otherwise
        """
        try:
            open_positions = self.get_open_positions()
            
            for position in open_positions:
                # Normalize symbols for comparison
                pos_symbol = SymbolUtils.normalize_symbol(position.symbol)
                sig_symbol = SymbolUtils.normalize_symbol(symbol)
                
                if pos_symbol == sig_symbol:
                    # Calculate position value
                    if position.entry_price > 0:
                        position_value = position.quantity * position.entry_price
                    else:
                        # For spot positions with unknown entry price, estimate using current market price
                        try:
                            # Try to get current market price
                            market_symbol = f"{symbol}/USDT"
                            if hasattr(self.exchange, 'fetch_ticker'):
                                ticker = self.exchange.fetch_ticker(market_symbol)
                                current_price = ticker.get('last', 0)
                                position_value = position.quantity * current_price
                            else:
                                # If we can't get market price, assume it's small if quantity is very small
                                position_value = position.quantity * 0.01  # Conservative estimate
                        except Exception:
                            # If we can't get market price, assume it's small if quantity is very small
                            position_value = position.quantity * 0.01  # Conservative estimate
                    
                    # If position is under $1 USD, consider it as "no position"
                    if position_value < 1.0:
                        continue
                    
                    return position
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to check open positions: {e}")
            return False
    
    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """
        Get specific position by symbol
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Position if found, None otherwise
        """
        try:
            open_positions = self.get_open_positions()
            
            for position in open_positions:
                pos_symbol = SymbolUtils.normalize_symbol(position.symbol)
                sig_symbol = SymbolUtils.normalize_symbol(symbol)
                
                if pos_symbol == sig_symbol:
                    return position
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get position by symbol: {e}")
            return None
    
    def get_balance(self) -> float:
        """
        Get current USDT balance
        
        Returns:
            USDT balance
        """
        try:
            balance = self.exchange.fetch_balance()
            
            # Handle different balance structures
            if isinstance(balance.get('USDT'), dict):
                return float(balance['USDT'].get('free', 0))
            else:
                return float(balance.get('USDT', 0))
                
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return 0.0
