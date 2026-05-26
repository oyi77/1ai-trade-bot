#!/usr/bin/env python3
"""
Order management system
SOLID: Single Responsibility, Open/Closed
DRY: Reusable order placement logic
KISS: Simple order management
SoC: Separation of concerns
"""

from typing import Dict, Any, Optional, List, Tuple
from abc import ABC, abstractmethod
import logging

from .utils import (
    OrderType, OrderSide, SignalType, ExchangeType, ExchangeDetector,
    SymbolUtils, OrderParamsBuilder, QuantityUtils
)
from .models import Signal

logger = logging.getLogger(__name__)


class IOrderStrategy(ABC):
    """Strategy interface for exchange-specific order placement (Strategy Pattern)"""
    
    @abstractmethod
    def place_market_order(
        self, 
        exchange, 
        symbol: str, 
        side: OrderSide, 
        amount: float, 
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place market order"""
        pass
    
    @abstractmethod
    def place_stop_loss_order(
        self,
        exchange,
        symbol: str,
        side: OrderSide,
        amount: float,
        stop_price: float,
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place stop loss order"""
        pass
    
    @abstractmethod
    def place_take_profit_order(
        self,
        exchange,
        symbol: str,
        side: OrderSide,
        amount: float,
        tp_price: float,
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place take profit order"""
        pass


class BybitOrderStrategy(IOrderStrategy):
    """Bybit-specific order placement strategy"""
    
    def place_market_order(
        self, 
        exchange, 
        symbol: str, 
        side: OrderSide, 
        amount: float, 
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place market order on Bybit"""
        if signal_type == SignalType.FUTURES:
            return exchange.create_order(
                symbol=symbol,
                type=OrderType.MARKET.value,
                side=side.value,
                amount=amount,
                params=OrderParamsBuilder.build_futures_market_order_params(ExchangeType.BYBIT.value)
            )
        else:
            return exchange.create_market_buy_order(symbol, amount)
    
    def place_stop_loss_order(
        self,
        exchange,
        symbol: str,
        side: OrderSide,
        amount: float,
        stop_price: float,
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place stop loss order on Bybit"""
        if signal_type == SignalType.FUTURES:
            return exchange.create_order(
                symbol=symbol,
                type=OrderType.LIMIT.value,
                side=side.value,
                amount=amount,
                price=stop_price,
                params=OrderParamsBuilder.build_futures_stop_loss_params(
                    ExchangeType.BYBIT.value, stop_price
                )
            )
        else:
            return exchange.create_order(
                symbol=symbol,
                type=OrderType.LIMIT.value,
                side=side.value,
                amount=amount,
                price=stop_price,
                params=OrderParamsBuilder.build_spot_stop_loss_params(
                    ExchangeType.BYBIT.value, stop_price
                )
            )
    
    def place_take_profit_order(
        self,
        exchange,
        symbol: str,
        side: OrderSide,
        amount: float,
        tp_price: float,
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place take profit order on Bybit"""
        if signal_type == SignalType.FUTURES:
            return exchange.create_order(
                symbol=symbol,
                type=OrderType.LIMIT.value,
                side=side.value,
                amount=amount,
                price=tp_price,
                params=OrderParamsBuilder.build_futures_take_profit_params(ExchangeType.BYBIT.value)
            )
        else:
            return exchange.create_limit_sell_order(symbol, amount, tp_price)


class GenericOrderStrategy(IOrderStrategy):
    """Generic order placement strategy for other exchanges"""
    
    def place_market_order(
        self, 
        exchange, 
        symbol: str, 
        side: OrderSide, 
        amount: float, 
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place market order using generic methods"""
        if side == OrderSide.BUY:
            return exchange.create_market_buy_order(symbol, amount)
        else:
            return exchange.create_market_sell_order(symbol, amount)
    
    def place_stop_loss_order(
        self,
        exchange,
        symbol: str,
        side: OrderSide,
        amount: float,
        stop_price: float,
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place stop loss order using generic methods"""
        return exchange.create_stop_loss_order(
            symbol=symbol,
            side=side.value,
            amount=amount,
            price=stop_price
        )
    
    def place_take_profit_order(
        self,
        exchange,
        symbol: str,
        side: OrderSide,
        amount: float,
        tp_price: float,
        signal_type: SignalType
    ) -> Dict[str, Any]:
        """Place take profit order using generic methods"""
        if side == OrderSide.SELL:
            return exchange.create_limit_sell_order(symbol, amount, tp_price)
        else:
            return exchange.create_limit_buy_order(symbol, amount, tp_price)


class OrderManager:
    """Manages order placement operations (SoC)"""
    
    def __init__(self, exchange):
        """
        Initialize order manager
        
        Args:
            exchange: CCXT exchange instance
        """
        self.exchange = exchange
        self.exchange_type = ExchangeDetector.get_exchange_type(exchange)
        self.strategy = self._create_order_strategy()
    
    def _create_order_strategy(self) -> IOrderStrategy:
        """Create appropriate order strategy based on exchange type"""
        if self.exchange_type == ExchangeType.BYBIT:
            return BybitOrderStrategy()
        else:
            return GenericOrderStrategy()
    
    def place_main_order(
        self, 
        signal: Signal, 
        amount: float
    ) -> Optional[Dict[str, Any]]:
        """
        Place main market order with validation
        
        Args:
            signal: Trading signal
            amount: Order amount
            
        Returns:
            Order result or None if failed
        """
        try:
            symbol = SymbolUtils.format_symbol_for_exchange(
                signal.symbol, signal.signal_type, self.exchange_type
            )
            
            # Calculate order value to check minimum requirements
            order_value = amount * signal.entry_price
            
            # Check if order value meets minimum requirements
            # Determine if we're in testnet mode (sandbox)
            is_testnet = getattr(self.exchange, 'sandbox', False)
            min_order_value = ValidationUtils.get_minimum_order_value(is_testnet)
            
            if order_value < min_order_value:
                logger.warning(f"⚠️  Order value ${order_value:.2f} below minimum ${min_order_value} for {signal.symbol}")
                return None
            
            logger.info(f"📝 Executing {signal.signal_type.value} trade:")
            logger.info(f"   Symbol: {symbol}")
            logger.info(f"   Amount: {amount}")
            logger.info(f"   Entry: ${signal.entry_price}")
            logger.info(f"   Order Value: ${order_value:.2f}")
            
            order = self.strategy.place_market_order(
                exchange=self.exchange,
                symbol=symbol,
                side=OrderSide.BUY,
                amount=amount,
                signal_type=signal.signal_type
            )
            
            logger.info(f"✅ Order executed: {order['id']}")
            return order
            
        except Exception as e:
            logger.error(f"Failed to execute main order for {signal.symbol}: {e}")
            return None
    
    def place_stop_loss(
        self, 
        signal: Signal, 
        amount: float
    ) -> bool:
        """
        Place stop loss order with price validation and balance checking
        
        Args:
            signal: Trading signal
            amount: Order amount
            
        Returns:
            True if successful, False otherwise
        """
        try:
            symbol = SymbolUtils.format_symbol_for_exchange(
                signal.symbol, signal.signal_type, self.exchange_type
            )
            
            # Validate and adjust stop loss price
            adjusted_stop_loss = self._validate_stop_loss_price(signal, symbol)
            if adjusted_stop_loss is None:
                logger.warning(f"⚠️  Stop loss price validation failed for {signal.symbol}")
                return False
            
            # Check if we have sufficient balance for stop loss order
            # For spot stop loss, we need to ensure we have the tokens to sell
            # For futures stop loss, we need margin for the order
            if not self._validate_stop_loss_balance(signal, amount, adjusted_stop_loss):
                logger.warning(f"⚠️  Insufficient balance for stop loss order on {signal.symbol}")
                return False
            
            order = self.strategy.place_stop_loss_order(
                exchange=self.exchange,
                symbol=symbol,
                side=OrderSide.SELL,
                amount=amount,
                stop_price=adjusted_stop_loss,
                signal_type=signal.signal_type
            )
            
            if adjusted_stop_loss != signal.stop_loss:
                logger.info(f"🛡️  Stop loss adjusted from ${signal.stop_loss} to ${adjusted_stop_loss}")
            else:
                logger.info(f"🛡️  Stop loss placed at ${adjusted_stop_loss}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to place stop loss: {e}")
            return False
    
    def place_take_profits(
        self, 
        signal: Signal, 
        total_amount: float
    ) -> List[bool]:
        """
        Place all take profit orders (TP1, TP2, TP3)
        
        Args:
            signal: Trading signal
            total_amount: Total position amount
            
        Returns:
            List of success status for each TP level
        """
        results = []
        
        # Calculate TP amounts
        tp1_amount, tp2_amount, tp3_amount = QuantityUtils.calculate_tp_amounts(total_amount)
        
        # Place TP1
        if signal.take_profit_1:
            success = self._place_single_take_profit(
                signal, tp1_amount, signal.take_profit_1
            )
            results.append(success)
            if success:
                logger.info(f"💰 TP1 placed: {tp1_amount:.2f} tokens at ${signal.take_profit_1}")
        
        # Place TP2
        if signal.take_profit_2:
            success = self._place_single_take_profit(
                signal, tp2_amount, signal.take_profit_2
            )
            results.append(success)
            if success:
                logger.info(f"💰 TP2 placed: {tp2_amount:.2f} tokens at ${signal.take_profit_2}")
        
        # Place TP3
        if signal.take_profit_3:
            success = self._place_single_take_profit(
                signal, tp3_amount, signal.take_profit_3
            )
            results.append(success)
            if success:
                logger.info(f"💰 TP3 placed: {tp3_amount:.2f} tokens at ${signal.take_profit_3}")
        
        return results
    
    def _place_single_take_profit(
        self, 
        signal: Signal, 
        amount: float, 
        tp_price: float
    ) -> bool:
        """Place a single take profit order with validation"""
        try:
            symbol = SymbolUtils.format_symbol_for_exchange(
                signal.symbol, signal.signal_type, self.exchange_type
            )
            
            # Calculate order value to check minimum requirements
            order_value = amount * tp_price
            
            # Check if order value meets minimum requirements
            # Determine if we're in testnet mode (sandbox)
            is_testnet = getattr(self.exchange, 'sandbox', False)
            min_order_value = ValidationUtils.get_minimum_order_value(is_testnet)
            
            if order_value < min_order_value:
                logger.warning(f"⚠️  Take profit order value ${order_value:.2f} below minimum ${min_order_value} for {signal.symbol}")
                return False
            
            # Check if we have sufficient balance for take profit order
            if not self._validate_take_profit_balance(signal, amount, tp_price):
                logger.warning(f"⚠️  Insufficient balance for take profit order on {signal.symbol}")
                return False
            
            order = self.strategy.place_take_profit_order(
                exchange=self.exchange,
                symbol=symbol,
                side=OrderSide.SELL,
                amount=amount,
                tp_price=tp_price,
                signal_type=signal.signal_type
            )
            
            logger.info(f"💰 Take profit placed at ${tp_price}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to place take profit: {e}")
            return False
    
    def close_position(
        self, 
        symbol: str, 
        signal_type: SignalType, 
        position_size: float
    ) -> bool:
        """
        Close an existing position
        
        Args:
            symbol: Trading symbol
            signal_type: Spot or futures
            position_size: Position size to close
            
        Returns:
            True if successful, False otherwise
        """
        try:
            formatted_symbol = SymbolUtils.format_symbol_for_exchange(
                symbol, signal_type, self.exchange_type
            )
            
            order = self.strategy.place_market_order(
                exchange=self.exchange,
                symbol=formatted_symbol,
                side=OrderSide.SELL,
                amount=position_size,
                signal_type=signal_type
            )
            
            logger.info(f"✅ Position closed: {symbol}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            return False
    
    def _validate_stop_loss_price(self, signal: Signal, symbol: str) -> Optional[float]:
        """
        Validate and adjust stop loss price to meet exchange requirements
        
        Args:
            signal: Trading signal
            symbol: Formatted symbol for exchange
            
        Returns:
            Adjusted stop loss price or None if validation fails
        """
        try:
            # Get current market price
            ticker = self.exchange.fetch_ticker(symbol)
            current_price = ticker['last']
            
            original_stop_loss = signal.stop_loss
            entry_price = signal.entry_price
            
            # For Bybit, use more conservative minimum deviation to avoid price constraint errors
            # Bybit typically requires 0.5% minimum price deviation from current market price
            min_deviation = 0.005  # 0.5%
            
            if signal.side == 'BUY':
                # For long positions, stop loss should be below entry price
                # Calculate minimum allowed stop loss price based on current market price
                min_allowed_stop_price = current_price * (1 - min_deviation)
                
                # Also consider exchange minimum price constraints
                # Bybit often has minimum price requirements that are higher than calculated
                # Use a more conservative approach: ensure stop loss is at least 1% below current price
                conservative_min_stop_price = current_price * 0.99
                
                # For very low-priced assets, ensure we maintain a reasonable minimum distance
                # If current price is very low, we need to be even more conservative
                if current_price < 1.0:  # For assets under $1
                    conservative_min_stop_price = current_price * 0.985  # 1.5% below current price
                
                # Take the higher of the two minimums to ensure exchange compliance
                effective_min_stop_price = max(min_allowed_stop_price, conservative_min_stop_price)
                
                if original_stop_loss < effective_min_stop_price:
                    # Adjust stop loss to meet minimum distance requirement
                    adjusted_stop_loss = effective_min_stop_price
                    
                    # Ensure it's still below entry price (reasonable stop loss)
                    if adjusted_stop_loss >= entry_price:
                        # If adjusted price is above entry, use a more conservative approach
                        # Set stop loss to 2% below entry price to ensure it's reasonable
                        adjusted_stop_loss = entry_price * 0.98
                        
                        # Double-check that this is still above the minimum requirement
                        if adjusted_stop_loss < effective_min_stop_price:
                            # If still too low, try even more conservative approach
                            adjusted_stop_loss = entry_price * 0.985  # 1.5% below entry
                            
                            if adjusted_stop_loss < effective_min_stop_price:
                                # If still too low, skip stop loss placement
                                logger.warning(f"Stop loss cannot be placed for {symbol}: entry price too close to minimum constraints")
                                return None
                    
                    logger.warning(f"Stop loss adjusted from ${original_stop_loss:.6f} to ${adjusted_stop_loss:.6f} for {symbol}")
                    return adjusted_stop_loss
                else:
                    return original_stop_loss
                    
            else:
                # For short positions, stop loss should be above entry price
                min_allowed_stop_price = current_price * (1 + min_deviation)
                
                # Conservative approach: ensure stop loss is at least 1% above current price
                conservative_min_stop_price = current_price * 1.01
                
                # For very low-priced assets, ensure we maintain a reasonable minimum distance
                if current_price < 1.0:  # For assets under $1
                    conservative_min_stop_price = current_price * 1.015  # 1.5% above current price
                
                # Take the higher of the two minimums
                effective_min_stop_price = max(min_allowed_stop_price, conservative_min_stop_price)
                
                if original_stop_loss > effective_min_stop_price:
                    # Adjust stop loss to meet minimum distance requirement
                    adjusted_stop_loss = effective_min_stop_price
                    
                    # Ensure it's still above entry price (reasonable stop loss)
                    if adjusted_stop_loss <= entry_price:
                        # If adjusted price is below entry, use a more conservative approach
                        adjusted_stop_loss = entry_price * 1.02  # 2% above entry
                        
                        # Double-check that this is still above the minimum requirement
                        if adjusted_stop_loss <= effective_min_stop_price:
                            # If still too low, try even more conservative approach
                            adjusted_stop_loss = entry_price * 1.015  # 1.5% above entry
                            
                            if adjusted_stop_loss <= effective_min_stop_price:
                                # If still too low, skip stop loss placement
                                logger.warning(f"Stop loss cannot be placed for {symbol}: entry price too close to minimum constraints")
                                return None
                    
                    logger.warning(f"Stop loss adjusted from ${original_stop_loss:.6f} to ${adjusted_stop_loss:.6f} for {symbol}")
                    return adjusted_stop_loss
                else:
                    return original_stop_loss
                    
        except Exception as e:
            logger.error(f"Failed to validate stop loss price for {symbol}: {e}")
            return None
    
    def _validate_stop_loss_balance(self, signal: Signal, amount: float, stop_price: float) -> bool:
        """
        Validate if we have sufficient balance for stop loss order
        
        Args:
            signal: Trading signal
            amount: Order amount
            stop_price: Stop loss price
            
        Returns:
            True if sufficient balance, False otherwise
        """
        try:
            if signal.signal_type == SignalType.SPOT:
                # For spot stop loss, we need to ensure we have the tokens to sell
                # This is typically not an issue since we just bought them
                # But we can check if we have sufficient balance
                try:
                    balance = self.exchange.fetch_balance()
                    base_symbol = SymbolUtils.extract_base_symbol(signal.symbol)
                    
                    if base_symbol in balance:
                        available_balance = balance[base_symbol]['free']
                        if available_balance >= amount:
                            return True
                        else:
                            logger.warning(f"Insufficient {base_symbol} balance for stop loss: {available_balance} < {amount}")
                            return False
                    else:
                        logger.warning(f"No {base_symbol} balance found for stop loss")
                        return False
                except Exception as e:
                    logger.warning(f"Could not check balance for stop loss: {e}")
                    # Assume we have balance since we just bought the tokens
                    return True
            else:
                # For futures stop loss, we need margin for the order
                # This is typically handled by the exchange automatically
                return True
                
        except Exception as e:
            logger.error(f"Failed to validate stop loss balance: {e}")
            return False
    
    def _validate_take_profit_balance(self, signal: Signal, amount: float, tp_price: float) -> bool:
        """
        Validate if we have sufficient balance for take profit order
        
        Args:
            signal: Trading signal
            amount: Order amount
            tp_price: Take profit price
            
        Returns:
            True if sufficient balance, False otherwise
        """
        try:
            if signal.signal_type == SignalType.SPOT:
                # For spot take profit, we need to ensure we have the tokens to sell
                try:
                    balance = self.exchange.fetch_balance()
                    base_symbol = SymbolUtils.extract_base_symbol(signal.symbol)
                    
                    if base_symbol in balance:
                        available_balance = balance[base_symbol]['free']
                        if available_balance >= amount:
                            return True
                        else:
                            logger.warning(f"Insufficient {base_symbol} balance for take profit: {available_balance} < {amount}")
                            return False
                    else:
                        logger.warning(f"No {base_symbol} balance found for take profit")
                        return False
                except Exception as e:
                    logger.warning(f"Could not check balance for take profit: {e}")
                    # Assume we have balance since we just bought the tokens
                    return True
            else:
                # For futures take profit, we need margin for the order
                # This is typically handled by the exchange automatically
                return True
                
        except Exception as e:
            logger.error(f"Failed to validate take profit balance: {e}")
            return False
