#!/usr/bin/env python3
"""
Trading utilities and enums
SOLID: Single Responsibility, Open/Closed
DRY: Reusable utility functions
KISS: Simple, focused utilities
SoC: Separation of concerns
"""

from enum import Enum, auto
from typing import Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class OrderType(Enum):
    """Order types for trading"""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class OrderSide(Enum):
    """Order sides"""
    BUY = "buy"
    SELL = "sell"


class PositionSide(Enum):
    """Position sides"""
    LONG = "long"
    SHORT = "short"


class SignalType(Enum):
    """Signal types"""
    SPOT = "spot"
    FUTURES = "futures"


class ExchangeType(Enum):
    """Exchange types"""
    BYBIT = "bybit"
    BITGET = "bitget"
    BINANCE = "binance"


class SymbolUtils:
    """Utility class for symbol operations (DRY principle)"""
    
    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """
        Normalize symbol by removing common suffixes
        
        Args:
            symbol: Raw symbol (e.g., "BTC/USDT", "BTC/USDT:USDT", "BTC:USDT")
            
        Returns:
            Normalized symbol (e.g., "BTC")
        """
        if not symbol:
            return ""
        
        # Remove common suffixes
        normalized = symbol.upper()
        suffixes = ['/USDT:USDT', '/USDT', ':USDT', '/USDC', ':USDC', '/BTC', ':BTC']
        
        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
                break
        
        return normalized
    
    @staticmethod
    def format_symbol_for_exchange(symbol: str, signal_type: SignalType, exchange_type: ExchangeType) -> str:
        """
        Format symbol for specific exchange and signal type
        
        Args:
            symbol: Base symbol (e.g., "BTC")
            signal_type: Spot or futures
            exchange_type: Exchange type
            
        Returns:
            Formatted symbol for exchange
        """
        if '/' in symbol:
            return symbol  # Already formatted
        
        if signal_type == SignalType.FUTURES:
            if exchange_type == ExchangeType.BYBIT:
                return f"{symbol}/USDT:USDT"
            else:
                return f"{symbol}/USDT"
        else:
            return f"{symbol}/USDT"
    
    @staticmethod
    def extract_base_symbol(symbol: str) -> str:
        """Extract base symbol from formatted symbol"""
        if '/' in symbol:
            return symbol.split('/')[0]
        return symbol


class OrderParamsBuilder:
    """Builder for order parameters (DRY principle)"""
    
    @staticmethod
    def build_futures_market_order_params(exchange_type) -> Dict[str, Any]:
        """Build parameters for futures market order"""
        return {'type': 'future'}
    
    @staticmethod
    def build_futures_stop_loss_params(
        exchange_type, 
        stop_price: float, 
        trigger_by: str = "LastPrice"
    ) -> Dict[str, Any]:
        """Build parameters for futures stop loss order"""
        if exchange_type == ExchangeType.BYBIT:
            return {
                'type': 'future',
                'timeInForce': 'GTC',
                'stopPrice': stop_price,
                'triggerPrice': stop_price,
                'triggerDirection': 'descending',
                'triggerBy': trigger_by
            }
        return {}
    
    @staticmethod
    def build_futures_take_profit_params(exchange_type) -> Dict[str, Any]:
        """Build parameters for futures take profit order"""
        if exchange_type == ExchangeType.BYBIT:
            return {'type': 'future'}
        return {}
    
    @staticmethod
    def build_spot_stop_loss_params(
        exchange_type: ExchangeType,
        stop_price: float,
        trigger_by: str = "LastPrice"
    ) -> Dict[str, Any]:
        """Build parameters for spot stop loss order"""
        if exchange_type == ExchangeType.BYBIT:
            return {
                'timeInForce': 'GTC',
                'stopPrice': stop_price,
                'triggerPrice': stop_price,
                'triggerDirection': 'descending',
                'triggerBy': trigger_by
            }
        return {}


class QuantityUtils:
    """Utility class for quantity calculations (DRY principle)"""
    
    @staticmethod
    def adjust_quantity_for_precision(
        quantity: float, 
        step_size: float, 
        min_amount: float
    ) -> float:
        """
        Adjust quantity based on exchange precision requirements
        
        Args:
            quantity: Original quantity
            step_size: Exchange step size
            min_amount: Minimum amount
            
        Returns:
            Adjusted quantity
        """
        if step_size > 0:
            # Round to step size
            adjusted = round(quantity / step_size) * step_size
        else:
            adjusted = quantity
        
        # Ensure minimum amount
        if adjusted < min_amount:
            adjusted = min_amount
        
        return adjusted
    
    @staticmethod
    def calculate_tp_amounts(total_amount: float) -> Tuple[float, float, float]:
        """
        Calculate take profit amounts based on distribution
        
        Args:
            total_amount: Total position amount
            
        Returns:
            Tuple of (tp1_amount, tp2_amount, tp3_amount)
        """
        tp1_amount = total_amount * 0.5  # 50%
        tp2_amount = total_amount * 0.3  # 30%
        tp3_amount = total_amount * 0.2  # 20%
        
        return tp1_amount, tp2_amount, tp3_amount


class ExchangeDetector:
    """Utility to detect exchange type from ccxt exchange object"""
    
    @staticmethod
    def get_exchange_type(exchange) -> ExchangeType:
        """Get exchange type from ccxt exchange object"""
        exchange_id = exchange.id.lower()
        
        if exchange_id == 'bybit':
            return ExchangeType.BYBIT
        elif exchange_id == 'bitget':
            return ExchangeType.BITGET
        elif exchange_id == 'binance':
            return ExchangeType.BINANCE
        else:
            raise ValueError(f"Unsupported exchange: {exchange_id}")


class ValidationUtils:
    """Utility class for validation (DRY principle)"""
    
    # Minimum order values based on trading rules
    MIN_ORDER_VALUE_TESTNET = 10.0  # $10 for testnet
    MIN_ORDER_VALUE_LIVE = 5.0      # $5 for live trading
    
    @staticmethod
    def validate_symbol(symbol: str, markets: Dict) -> bool:
        """Validate if symbol exists in markets"""
        return symbol in markets
    
    @staticmethod
    def validate_balance_sufficient(balance: float, required: float, buffer: float = 1.1) -> bool:
        """Validate if balance is sufficient with buffer"""
        return balance >= (required * buffer)
    
    @staticmethod
    def validate_position_size(size: float, min_size: float) -> bool:
        """Validate if position size meets minimum requirements"""
        return size >= min_size
    
    @staticmethod
    def validate_order_value(order_value: float, is_testnet: bool = False) -> bool:
        """Validate if order value meets minimum requirements"""
        min_value = ValidationUtils.MIN_ORDER_VALUE_TESTNET if is_testnet else ValidationUtils.MIN_ORDER_VALUE_LIVE
        return order_value >= min_value
    
    @staticmethod
    def get_minimum_order_value(is_testnet: bool = False) -> float:
        """Get minimum order value based on trading mode"""
        return ValidationUtils.MIN_ORDER_VALUE_TESTNET if is_testnet else ValidationUtils.MIN_ORDER_VALUE_LIVE
