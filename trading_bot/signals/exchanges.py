#!/usr/bin/env python3
"""
Exchange executors for cryptocurrency trading
Implements real trading on Bitget, Bybit, and other exchanges using ccxt
SOLID, DRY, KISS, SoC principles applied
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import logging
import ccxt

from .models import Signal, Position
from .executors import ITradeExecutor
from .utils import SignalType, ExchangeType, ExchangeDetector
from .risk_manager import RiskManager
from .order_manager import OrderManager
from .position_manager import PositionManager
from .config_manager import ExchangeConfig, TradingConfig

logger = logging.getLogger(__name__)


class BaseExchangeExecutor(ITradeExecutor):
    """
    Refactored base class for exchange executors using SOLID principles
    Single Responsibility: Each manager handles one concern
    Open/Closed: Extensible through strategy pattern
    Dependency Inversion: Depends on abstractions, not concretions
    """
    
    def __init__(
        self,
        api_key_or_config=None,
        api_secret_or_trading_config=None,
        passphrase: Optional[str] = None,
        sandbox: bool = True,
        risk_percentage: float = 0.02,
        default_leverage: int = 10,
        max_leverage: int = 20,
        margin_mode: str = "isolated",
        margin_buffer: float = 1.1,
        **kwargs
    ):
        """
        Initialize exchange executor with backward compatibility
        
        Args:
            api_key_or_config: Either API key string or ExchangeConfig object
            api_secret_or_trading_config: Either API secret string or TradingConfig object
            passphrase: API passphrase (for exchanges that require it)
            sandbox: Use testnet/sandbox mode
            risk_percentage: Risk per trade (default 2%)
            default_leverage: Default leverage for futures (default 10x)
            max_leverage: Maximum allowed leverage (default 20x)
            margin_mode: Margin mode - 'isolated' or 'cross' (default isolated)
            margin_buffer: Margin buffer multiplier (default 1.1x)
        """
        # Check if using new config format
        if isinstance(api_key_or_config, ExchangeConfig):
            exchange_config = api_key_or_config
            trading_config = api_secret_or_trading_config
        else:
            # Handle backward compatibility with keyword arguments
            if 'api_key' in kwargs:
                api_key = kwargs['api_key']
                api_secret = kwargs.get('api_secret', '')
                passphrase = kwargs.get('passphrase', passphrase)
                sandbox = kwargs.get('sandbox', sandbox)
                risk_percentage = kwargs.get('risk_percentage', risk_percentage)
                default_leverage = kwargs.get('default_leverage', default_leverage)
                max_leverage = kwargs.get('max_leverage', max_leverage)
                margin_mode = kwargs.get('margin_mode', margin_mode)
                margin_buffer = kwargs.get('margin_buffer', margin_buffer)
            else:
                api_key = api_key_or_config
                api_secret = api_secret_or_trading_config or ''
            
            # Convert to new config format for backward compatibility
            exchange_config = ExchangeConfig(
                api_key=api_key,
                secret=api_secret,
                passphrase=passphrase,
                sandbox=sandbox
            )
            
            trading_config = TradingConfig(
                risk_percentage=risk_percentage,
                default_leverage=default_leverage,
                max_leverage=max_leverage,
                margin_mode=margin_mode,
                margin_buffer=margin_buffer
            )
        
        self.exchange_config = exchange_config
        self.trading_config = trading_config
        self.exchange: Optional[ccxt.Exchange] = None
        
        # Initialize managers (Dependency Injection)
        self.risk_manager = RiskManager(
            risk_percentage=trading_config.risk_percentage,
            default_leverage=trading_config.default_leverage,
            max_leverage=trading_config.max_leverage,
            margin_buffer=trading_config.margin_buffer
        )
        
        self._initialize_exchange()
        
        # Initialize managers after exchange is ready
        self.order_manager = OrderManager(self.exchange)
        self.position_manager = PositionManager(self.exchange)
    
    @abstractmethod
    def _initialize_exchange(self):
        """Initialize specific exchange - must be implemented by subclasses"""
        pass
    
    def get_balance(self) -> float:
        """Get USDT balance using position manager"""
        return self.position_manager.get_balance()
    
    def calculate_position_size(self, signal: Signal) -> float:
        """
        Calculate position size using risk manager
        
        Args:
            signal: Trading signal
            
        Returns:
            Calculated position size
        """
        try:
            balance = self.get_balance()
            if balance <= 0:
                logger.warning(f"❌ Insufficient balance: ${balance}")
                return 0.0
            
            # Get exchange type and markets
            exchange_type = ExchangeDetector.get_exchange_type(self.exchange)
            markets = self.exchange.markets if hasattr(self.exchange, 'markets') else {}
            
            # Calculate position size using risk manager
            risk_calculation = self.risk_manager.calculate_position_size(
                signal, balance, exchange_type, markets
            )
            
            if not risk_calculation.is_valid:
                logger.error(f"❌ {risk_calculation.error_message}")
                return 0.0
            
            return risk_calculation.position_size
            
        except Exception as e:
            logger.error(f"Failed to calculate position size: {e}")
            return 0.0
    
    def execute_trade(self, signal: Signal) -> bool:
        """
        Execute trade using order manager with proper position management
        
        Args:
            signal: Trading signal
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check for existing position and handle partial liquidations
            existing_position = self.position_manager.get_existing_position(signal.symbol)
            if existing_position:
                # Check if we need to close existing position before opening new one
                if self._should_close_existing_position(existing_position, signal):
                    logger.warning(f"🔄 Closing existing position before opening new one: {signal.symbol}")
                    logger.warning(f"   Existing: {existing_position.side} {existing_position.quantity} @ ${existing_position.entry_price}")
                    logger.warning(f"   New Signal: {signal.side} @ ${signal.entry_price}")
                    
                    # Close existing position
                    if not self.order_manager.close_position(
                        signal.symbol, 
                        signal.signal_type, 
                        existing_position.quantity
                    ):
                        logger.error(f"❌ Failed to close existing position for {signal.symbol}")
                        return False
                    
                    logger.info(f"✅ Closed existing position for {signal.symbol}")
                else:
                    logger.info(f"⏭️  Skipping {signal.symbol} - position already open on exchange")
                    return False
            
            # Calculate position size
            amount = self.calculate_position_size(signal)
            if amount <= 0:
                return False
            
            # Validate symbol
            symbol = self._format_symbol(signal.symbol, signal.signal_type)
            if not self._validate_symbol(symbol):
                logger.error(f"❌ Invalid symbol: {symbol}")
                return False
            
            # Set leverage for futures
            if signal.signal_type == SignalType.FUTURES:
                self._set_leverage(symbol, signal.leverage or self.trading_config.default_leverage)
                self._set_margin_mode(symbol, self.trading_config.margin_mode)
            
            # Execute main order
            order = self.order_manager.place_main_order(signal, amount)
            if not order:
                return False
            
            # Place stop loss (non-critical)
            try:
                sl_success = self.order_manager.place_stop_loss(signal, amount)
                if not sl_success:
                    logger.warning(f"⚠️  Stop loss placement skipped for {signal.symbol} (insufficient balance or validation failed)")
            except Exception as sl_error:
                logger.warning(f"⚠️  Failed to place stop loss for {signal.symbol}: {sl_error}")
            
            # Place take profits (non-critical)
            try:
                tp_results = self.order_manager.place_take_profits(signal, amount)
                successful_tps = sum(1 for result in tp_results if result)
                total_tps = len(tp_results)
                if successful_tps < total_tps:
                    logger.warning(f"⚠️  Only {successful_tps}/{total_tps} take profits placed for {signal.symbol}")
            except Exception as tp_error:
                logger.warning(f"⚠️  Failed to place take profits for {signal.symbol}: {tp_error}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to execute trade for {signal.symbol}: {e}")
            return False
    
    def _should_close_existing_position(self, existing_position, signal: Signal) -> bool:
        """
        Determine if existing position should be closed before opening new one
        
        Args:
            existing_position: Current open position
            signal: New trading signal
            
        Returns:
            True if position should be closed, False otherwise
        """
        try:
            # If positions are in opposite directions, close existing one
            if existing_position.side != signal.side:
                logger.info(f"🔄 Opposite direction detected: {existing_position.side} vs {signal.side}")
                return True
            
            # If positions are in same direction, keep existing one
            logger.info(f"📈 Same direction: keeping existing {existing_position.side} position")
            return False
            
        except Exception as e:
            logger.error(f"Failed to determine if position should be closed: {e}")
            return False
    
    def close_position(self, symbol: str, reason: str = "manual") -> bool:
        """
        Close position using order manager
        
        Args:
            symbol: Trading symbol
            reason: Reason for closing
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Find existing position
            position = self.position_manager.get_position_by_symbol(symbol)
            if not position:
                logger.warning(f"⚠️  No position found for {symbol}")
                return False
            
            # Determine signal type
            signal_type = SignalType.FUTURES if position.position_type == 'futures' else SignalType.SPOT
            
            # Close position
            success = self.order_manager.close_position(symbol, signal_type, position.quantity)
            
            if success:
                logger.info(f"✅ Position closed: {symbol} ({reason})")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            return False
    
    def get_open_positions(self) -> List[Position]:
        """Get open positions using position manager"""
        return self.position_manager.get_open_positions()
    
    def _format_symbol(self, symbol: str, signal_type: SignalType) -> str:
        """Format symbol for exchange"""
        from .utils import SymbolUtils
        exchange_type = ExchangeDetector.get_exchange_type(self.exchange)
        return SymbolUtils.format_symbol_for_exchange(symbol, signal_type, exchange_type)
    
    def _validate_symbol(self, symbol: str) -> bool:
        """Validate symbol exists on exchange"""
        from .utils import ValidationUtils
        markets = self.exchange.markets if hasattr(self.exchange, 'markets') else {}
        return ValidationUtils.validate_symbol(symbol, markets)
    
    def _set_leverage(self, symbol: str, leverage: int):
        """Set leverage for futures trading"""
        try:
            if not hasattr(self.exchange, 'set_leverage'):
                logger.debug(f"Exchange {self.exchange.id} does not support leverage setting")
                return
            
            # Check if symbol supports leverage (futures only)
            if symbol in self.exchange.markets:
                market = self.exchange.markets[symbol]
                if market.get('type') != 'future':
                    logger.debug(f"Symbol {symbol} is not a futures contract, skipping leverage")
                    return
            
            # Try to set leverage
            self.exchange.set_leverage(leverage, symbol)
            logger.info(f"⚙️  Leverage set to {leverage}x for {symbol}")
            
        except Exception as e:
            error_msg = str(e).lower()
            if 'only support linear and inverse market' in error_msg:
                logger.debug(f"Symbol {symbol} does not support leverage setting")
            elif 'leverage not allowed' in error_msg:
                logger.warning(f"Leverage {leverage}x not allowed for {symbol}")
            else:
                logger.warning(f"Could not set leverage for {symbol}: {e}")
    
    def _set_margin_mode(self, symbol: str, margin_mode: str):
        """Set margin mode for futures trading"""
        try:
            if not hasattr(self.exchange, 'set_margin_mode'):
                logger.debug(f"Exchange {self.exchange.id} does not support margin mode setting")
                return
            
            # Check if symbol supports margin mode (futures only)
            if symbol in self.exchange.markets:
                market = self.exchange.markets[symbol]
                if market.get('type') != 'future':
                    logger.debug(f"Symbol {symbol} is not a futures contract, skipping margin mode")
                    return
            
            # Try to set margin mode
            self.exchange.set_margin_mode(margin_mode, symbol)
            logger.info(f"⚙️  Margin mode set to {margin_mode} for {symbol}")
            
        except Exception as e:
            error_msg = str(e).lower()
            if 'only support linear and inverse market' in error_msg:
                logger.debug(f"Symbol {symbol} does not support margin mode setting")
            elif 'margin mode not allowed' in error_msg:
                logger.warning(f"Margin mode {margin_mode} not allowed for {symbol}")
            else:
                logger.warning(f"Could not set margin mode for {symbol}: {e}")


class BitgetExecutor(BaseExchangeExecutor):
    """Bitget exchange executor"""
    
    def _initialize_exchange(self):
        """Initialize Bitget exchange"""
        try:
            config = {
                'apiKey': self.exchange_config.api_key,
                'secret': self.exchange_config.secret,
                'enableRateLimit': True,
            }
            
            if self.exchange_config.passphrase:
                config['password'] = self.exchange_config.passphrase
            
            self.exchange = ccxt.bitget(config)
            
            if self.exchange_config.sandbox:
                self.exchange.set_sandbox_mode(True)
            
            self.exchange.load_markets()
            logger.info("✅ Bitget exchange initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Bitget: {e}")
            raise


class BybitExecutor(BaseExchangeExecutor):
    """Bybit exchange executor"""
    
    def _initialize_exchange(self):
        """Initialize Bybit exchange"""
        try:
            config = {
                'apiKey': self.exchange_config.api_key,
                'secret': self.exchange_config.secret,
                'enableRateLimit': True,
            }
            
            self.exchange = ccxt.bybit(config)
            
            if self.exchange_config.sandbox:
                self.exchange.set_sandbox_mode(True)
            
            self.exchange.load_markets()
            logger.info("✅ Bybit exchange initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Bybit: {e}")
            raise


class BinanceExecutor(BaseExchangeExecutor):
    """Binance exchange executor"""
    
    def _initialize_exchange(self):
        """Initialize Binance exchange"""
        try:
            config = {
                'apiKey': self.exchange_config.api_key,
                'secret': self.exchange_config.secret,
                'enableRateLimit': True,
            }
            
            self.exchange = ccxt.binance(config)
            
            if self.exchange_config.sandbox:
                self.exchange.set_sandbox_mode(True)
            
            self.exchange.load_markets()
            logger.info("✅ Binance exchange initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Binance: {e}")
            raise
