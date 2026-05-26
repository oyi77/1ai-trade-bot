#!/usr/bin/env python3
"""
Risk management system
SOLID: Single Responsibility, Open/Closed
DRY: Reusable risk calculations
KISS: Simple risk management
SoC: Separation of concerns
"""

from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
import logging

from .utils import SignalType, ExchangeType, QuantityUtils, ValidationUtils

logger = logging.getLogger(__name__)


@dataclass
class RiskCalculation:
    """Risk calculation result"""
    position_size: float
    position_value: float
    required_margin: float
    risk_amount: float
    leverage: int
    is_valid: bool
    error_message: Optional[str] = None


class RiskManager:
    """Manages risk calculations and position sizing (SoC)"""
    
    def __init__(
        self,
        risk_percentage: float = 0.02,
        default_leverage: int = 10,
        max_leverage: int = 20,
        margin_buffer: float = 1.1
    ):
        """
        Initialize risk manager
        
        Args:
            risk_percentage: Risk per trade (default 2%)
            default_leverage: Default leverage for futures (default 10x)
            max_leverage: Maximum allowed leverage (default 20x)
            margin_buffer: Margin buffer multiplier (default 1.1x)
        """
        self.risk_percentage = risk_percentage
        self.default_leverage = default_leverage
        self.max_leverage = max_leverage
        self.margin_buffer = margin_buffer
    
    def calculate_position_size(
        self,
        signal,
        balance: float,
        exchange_type: ExchangeType,
        markets: Dict[str, Any]
    ) -> RiskCalculation:
        """
        Calculate position size based on risk management rules
        
        Args:
            signal: Trading signal
            balance: Available balance
            exchange_type: Exchange type
            markets: Exchange markets data
            
        Returns:
            RiskCalculation with position details
        """
        try:
            # Determine leverage
            leverage = self._determine_leverage(signal)
            
            # Calculate risk amount
            risk_amount = balance * self.risk_percentage
            
            # Calculate position value based on signal type
            if signal.signal_type == SignalType.FUTURES:
                return self._calculate_futures_position(
                    signal, balance, risk_amount, leverage, exchange_type, markets
                )
            else:
                return self._calculate_spot_position(
                    signal, balance, risk_amount, markets
                )
                
        except Exception as e:
            logger.error(f"Failed to calculate position size: {e}")
            return RiskCalculation(
                position_size=0,
                position_value=0,
                required_margin=0,
                risk_amount=0,
                leverage=0,
                is_valid=False,
                error_message=str(e)
            )
    
    def _determine_leverage(self, signal) -> int:
        """Determine appropriate leverage for signal"""
        if signal.signal_type == SignalType.SPOT:
            return 1  # No leverage for spot
        
        # Use signal leverage or default
        leverage = signal.leverage or self.default_leverage
        
        # Cap at maximum leverage
        return min(leverage, self.max_leverage)
    
    def _calculate_futures_position(
        self,
        signal,
        balance: float,
        risk_amount: float,
        leverage: int,
        exchange_type: ExchangeType,
        markets: Dict[str, Any]
    ) -> RiskCalculation:
        """Calculate futures position size"""
        try:
            # Calculate position value with leverage
            position_value = risk_amount * leverage
            
            # Calculate required margin
            required_margin = position_value / leverage
            
            # Apply margin buffer
            margin_with_buffer = required_margin * self.margin_buffer
            
            # Check if sufficient balance
            if not ValidationUtils.validate_balance_sufficient(balance, margin_with_buffer):
                return RiskCalculation(
                    position_size=0,
                    position_value=0,
                    required_margin=required_margin,
                    risk_amount=risk_amount,
                    leverage=leverage,
                    is_valid=False,
                    error_message=f"Insufficient balance: Required ${margin_with_buffer:.2f}, Available ${balance:.2f}"
                )
            
            # Calculate position size
            position_size = position_value / signal.entry_price
            
            # Adjust for exchange precision
            if signal.symbol in markets:
                market = markets[signal.symbol]
                if market.get('type') == 'future':
                    step_size = market.get('precision', {}).get('amount', 0.01)
                    min_amount = market.get('limits', {}).get('amount', {}).get('min', 0.01)
                    
                    position_size = QuantityUtils.adjust_quantity_for_precision(
                        position_size, step_size, min_amount
                    )
            
            # Validate position size
            if not ValidationUtils.validate_position_size(position_size, 0.01):
                return RiskCalculation(
                    position_size=0,
                    position_value=0,
                    required_margin=required_margin,
                    risk_amount=risk_amount,
                    leverage=leverage,
                    is_valid=False,
                    error_message="Position size too small"
                )
            
            logger.info(f"📊 Futures calculation:")
            logger.info(f"   Balance: ${balance:.2f}")
            logger.info(f"   Risk amount: ${risk_amount:.2f} ({self.risk_percentage*100}%)")
            logger.info(f"   Leverage: {leverage}x")
            logger.info(f"   Position value: ${position_value:.2f}")
            logger.info(f"   Required margin: ${required_margin:.2f}")
            logger.info(f"   Position size: {position_size:.2f} tokens")
            
            return RiskCalculation(
                position_size=position_size,
                position_value=position_value,
                required_margin=required_margin,
                risk_amount=risk_amount,
                leverage=leverage,
                is_valid=True
            )
            
        except Exception as e:
            logger.error(f"Failed to calculate futures position: {e}")
            return RiskCalculation(
                position_size=0,
                position_value=0,
                required_margin=0,
                risk_amount=risk_amount,
                leverage=leverage,
                is_valid=False,
                error_message=str(e)
            )
    
    def _calculate_spot_position(
        self,
        signal,
        balance: float,
        risk_amount: float,
        markets: Dict[str, Any]
    ) -> RiskCalculation:
        """Calculate spot position size"""
        try:
            # Calculate position value (no leverage for spot)
            position_value = risk_amount
            
            # Check if sufficient balance
            if not ValidationUtils.validate_balance_sufficient(balance, position_value):
                return RiskCalculation(
                    position_size=0,
                    position_value=0,
                    required_margin=position_value,
                    risk_amount=risk_amount,
                    leverage=1,
                    is_valid=False,
                    error_message=f"Insufficient balance: Required ${position_value:.2f}, Available ${balance:.2f}"
                )
            
            # Calculate position size
            position_size = position_value / signal.entry_price
            
            # Adjust for exchange precision
            if signal.symbol in markets:
                market = markets[signal.symbol]
                step_size = market.get('precision', {}).get('amount', 0.01)
                min_amount = market.get('limits', {}).get('amount', {}).get('min', 0.01)
                
                position_size = QuantityUtils.adjust_quantity_for_precision(
                    position_size, step_size, min_amount
                )
            
            # Validate position size
            if not ValidationUtils.validate_position_size(position_size, 0.01):
                return RiskCalculation(
                    position_size=0,
                    position_value=0,
                    required_margin=position_value,
                    risk_amount=risk_amount,
                    leverage=1,
                    is_valid=False,
                    error_message="Position size too small"
                )
            
            logger.info(f"📊 Spot calculation:")
            logger.info(f"   Balance: ${balance:.2f}")
            logger.info(f"   Risk amount: ${risk_amount:.2f} ({self.risk_percentage*100}%)")
            logger.info(f"   Position value: ${position_value:.2f}")
            logger.info(f"   Position size: {position_size:.2f} tokens")
            
            return RiskCalculation(
                position_size=position_size,
                position_value=position_value,
                required_margin=position_value,
                risk_amount=risk_amount,
                leverage=1,
                is_valid=True
            )
            
        except Exception as e:
            logger.error(f"Failed to calculate spot position: {e}")
            return RiskCalculation(
                position_size=0,
                position_value=0,
                required_margin=0,
                risk_amount=risk_amount,
                leverage=1,
                is_valid=False,
                error_message=str(e)
            )
    
    def calculate_take_profit_amounts(self, total_amount: float) -> Tuple[float, float, float]:
        """Calculate take profit amounts based on distribution"""
        return QuantityUtils.calculate_tp_amounts(total_amount)
    
    def validate_risk_parameters(self, signal) -> bool:
        """Validate risk parameters for signal"""
        try:
            # Check if leverage is within limits for futures
            if signal.signal_type == SignalType.FUTURES:
                leverage = signal.leverage or self.default_leverage
                if leverage > self.max_leverage:
                    logger.warning(f"Leverage {leverage}x exceeds maximum {self.max_leverage}x")
                    return False
            
            # Check if risk percentage is reasonable
            if self.risk_percentage > 0.1:  # 10% max risk
                logger.warning(f"Risk percentage {self.risk_percentage*100}% is very high")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to validate risk parameters: {e}")
            return False
