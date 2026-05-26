#!/usr/bin/env python3
"""
Configuration management system
SOLID: Single Responsibility, Open/Closed
DRY: Reusable configuration logic
KISS: Simple configuration management
SoC: Separation of concerns
"""

from typing import Dict, Any, Optional, Union
from dataclasses import dataclass, field
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class ExchangeConfig:
    """Exchange configuration"""
    api_key: str
    secret: str
    passphrase: Optional[str] = None
    sandbox: bool = True


@dataclass
class TradingConfig:
    """Trading configuration"""
    risk_percentage: float = 0.02
    default_leverage: int = 10
    max_leverage: int = 20
    margin_mode: str = "isolated"
    margin_buffer: float = 1.1


@dataclass
class BotConfig:
    """Bot configuration"""
    enable_spot: bool = True
    enable_futures: bool = False
    enable_notifications: bool = True
    cache_path: str = "."
    log_level: str = "INFO"


@dataclass
class FirebaseConfig:
    """Firebase configuration"""
    project_id: str
    api_key: str
    auth_domain: str
    database_url: str
    storage_bucket: str
    messaging_sender_id: str
    app_id: str


@dataclass
class AppConfig:
    """Complete application configuration"""
    exchange: ExchangeConfig
    trading: TradingConfig = field(default_factory=TradingConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    firebase: Optional[FirebaseConfig] = None
    sandbox: bool = True
    
    def __post_init__(self):
        """Post-initialization validation"""
        self._validate_config()
    
    def _validate_config(self):
        """Validate configuration values"""
        # Validate risk percentage
        if not 0 < self.trading.risk_percentage <= 1:
            raise ValueError(f"Risk percentage must be between 0 and 1, got {self.trading.risk_percentage}")
        
        # Validate leverage
        if not 1 <= self.trading.default_leverage <= self.trading.max_leverage:
            raise ValueError(f"Default leverage must be between 1 and {self.trading.max_leverage}")
        
        # Validate margin mode
        if self.trading.margin_mode not in ["isolated", "cross"]:
            raise ValueError(f"Margin mode must be 'isolated' or 'cross', got {self.trading.margin_mode}")
        
        # Validate margin buffer
        if self.trading.margin_buffer < 1.0:
            raise ValueError(f"Margin buffer must be >= 1.0, got {self.trading.margin_buffer}")


class ConfigManager:
    """Manages application configuration (SoC)"""
    
    def __init__(self, config_path: str = "config.json"):
        """
        Initialize configuration manager
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = Path(config_path)
        self._config: Optional[AppConfig] = None
    
    def load_config(self) -> AppConfig:
        """
        Load configuration from file
        
        Returns:
            Application configuration
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        try:
            if not self.config_path.exists():
                raise FileNotFoundError(f"Config file not found: {self.config_path}")
            
            with open(self.config_path, 'r') as f:
                config_data = json.load(f)
            
            self._config = self._parse_config(config_data)
            logger.info(f"✅ Configuration loaded from {self.config_path}")
            return self._config
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in config file: {e}")
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise
    
    def _parse_config(self, config_data: Dict[str, Any]) -> AppConfig:
        """Parse configuration data into AppConfig object"""
        try:
            # Parse exchange config
            exchange_data = config_data.get('exchange', {})
            exchange_config = ExchangeConfig(
                api_key=exchange_data.get('api_key', ''),
                secret=exchange_data.get('secret', ''),
                passphrase=exchange_data.get('passphrase'),
                sandbox=exchange_data.get('sandbox', True)
            )
            
            # Parse trading config
            trading_data = config_data.get('trading', {})
            trading_config = TradingConfig(
                risk_percentage=trading_data.get('risk_percentage', 0.02),
                default_leverage=trading_data.get('default_leverage', 10),
                max_leverage=trading_data.get('max_leverage', 20),
                margin_mode=trading_data.get('margin_mode', 'isolated'),
                margin_buffer=trading_data.get('margin_buffer', 1.1)
            )
            
            # Parse bot config
            bot_data = config_data.get('bot', {})
            bot_config = BotConfig(
                enable_spot=bot_data.get('enable_spot', True),
                enable_futures=bot_data.get('enable_futures', False),
                enable_notifications=bot_data.get('enable_notifications', True),
                cache_path=bot_data.get('cache_path', '.'),
                log_level=bot_data.get('log_level', 'INFO')
            )
            
            # Parse Firebase config (optional)
            firebase_config = None
            firebase_data = config_data.get('firebase')
            if firebase_data:
                firebase_config = FirebaseConfig(
                    project_id=firebase_data.get('project_id', ''),
                    api_key=firebase_data.get('api_key', ''),
                    auth_domain=firebase_data.get('auth_domain', ''),
                    database_url=firebase_data.get('database_url', ''),
                    storage_bucket=firebase_data.get('storage_bucket', ''),
                    messaging_sender_id=firebase_data.get('messaging_sender_id', ''),
                    app_id=firebase_data.get('app_id', '')
                )
            
            # Parse sandbox setting - check both root level and exchange level
            sandbox = config_data.get('sandbox', exchange_data.get('sandbox', True))
            
            return AppConfig(
                exchange=exchange_config,
                trading=trading_config,
                bot=bot_config,
                firebase=firebase_config,
                sandbox=sandbox
            )
            
        except Exception as e:
            raise ValueError(f"Failed to parse configuration: {e}")
    
    def save_config(self, config: AppConfig) -> None:
        """
        Save configuration to file
        
        Args:
            config: Application configuration to save
        """
        try:
            config_data = self._serialize_config(config)
            
            with open(self.config_path, 'w') as f:
                json.dump(config_data, f, indent=2)
            
            logger.info(f"✅ Configuration saved to {self.config_path}")
            
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            raise
    
    def _serialize_config(self, config: AppConfig) -> Dict[str, Any]:
        """Serialize AppConfig object to dictionary"""
        return {
            'exchange': {
                'api_key': config.exchange.api_key,
                'secret': config.exchange.secret,
                'passphrase': config.exchange.passphrase,
                'sandbox': config.exchange.sandbox
            },
            'trading': {
                'risk_percentage': config.trading.risk_percentage,
                'default_leverage': config.trading.default_leverage,
                'max_leverage': config.trading.max_leverage,
                'margin_mode': config.trading.margin_mode,
                'margin_buffer': config.trading.margin_buffer
            },
            'bot': {
                'enable_spot': config.bot.enable_spot,
                'enable_futures': config.bot.enable_futures,
                'enable_notifications': config.bot.enable_notifications,
                'cache_path': config.bot.cache_path,
                'log_level': config.bot.log_level
            },
            'firebase': {
                'project_id': config.firebase.project_id,
                'api_key': config.firebase.api_key,
                'auth_domain': config.firebase.auth_domain,
                'database_url': config.firebase.database_url,
                'storage_bucket': config.firebase.storage_bucket,
                'messaging_sender_id': config.firebase.messaging_sender_id,
                'app_id': config.firebase.app_id
            } if config.firebase else None,
            'sandbox': config.sandbox
        }
    
    def get_config(self) -> Optional[AppConfig]:
        """Get current configuration"""
        return self._config
    
    def update_config(self, **kwargs) -> None:
        """
        Update configuration values
        
        Args:
            **kwargs: Configuration values to update
        """
        if not self._config:
            raise ValueError("No configuration loaded")
        
        # Update exchange config
        if 'exchange' in kwargs:
            exchange_updates = kwargs['exchange']
            for key, value in exchange_updates.items():
                if hasattr(self._config.exchange, key):
                    setattr(self._config.exchange, key, value)
        
        # Update trading config
        if 'trading' in kwargs:
            trading_updates = kwargs['trading']
            for key, value in trading_updates.items():
                if hasattr(self._config.trading, key):
                    setattr(self._config.trading, key, value)
        
        # Update bot config
        if 'bot' in kwargs:
            bot_updates = kwargs['bot']
            for key, value in bot_updates.items():
                if hasattr(self._config.bot, key):
                    setattr(self._config.bot, key, value)
        
        # Validate updated config
        self._config._validate_config()
    
    def create_default_config(self) -> AppConfig:
        """Create default configuration"""
        exchange_config = ExchangeConfig(
            api_key="your_api_key_here",
            secret="your_secret_here",
            sandbox=True
        )
        
        trading_config = TradingConfig()
        bot_config = BotConfig()
        
        return AppConfig(
            exchange=exchange_config,
            trading=trading_config,
            bot=bot_config,
            sandbox=True
        )
