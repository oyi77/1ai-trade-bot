#!/usr/bin/env python3
"""
Data models for trading signals and notifications
SOLID: Single Responsibility - Each class has one purpose
DRY: Reusable data structures
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any
from .utils import SignalType


@dataclass
class Signal:
    """Trading signal data structure"""
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    leverage: Optional[int] = None
    is_premium: bool = False
    type: SignalType = SignalType.SPOT  # Use enum for type safety
    signal_id: str = ''
    timestamp: Optional[str] = None
    
    @property
    def side(self) -> str:
        """Determine buy/sell from entry vs stop"""
        if self.entry_price > self.stop_loss:
            return 'BUY'
        else:
            return 'SELL'
    
    @property
    def take_profit(self) -> float:
        """Primary take profit (alias for take_profit_1)"""
        return self.take_profit_1
    
    @property
    def signal_type(self) -> SignalType:
        """Alias for type (backward compatibility)"""
        return self.type
    
    @classmethod
    def from_firestore(cls, doc: Dict[str, Any]) -> 'Signal':
        """
        Create Signal from Firestore document
        
        Args:
            doc: Firestore document dictionary
            
        Returns:
            Signal instance
        """
        # Futures signals use 'pair' field, spot signals use 'symbol' field
        # If no symbol, extract from pair (e.g., XPINUSDT -> XPIN)
        symbol = doc.get('symbol', '')
        if not symbol:
            pair = doc.get('pair', '')
            # Remove common quote currencies to get base symbol
            symbol = pair.replace('USDT', '').replace('BUSD', '').replace('USD', '')
        
        # Spot signals use 'buy', futures use 'entry'
        entry_price = float(doc.get('buy', doc.get('entry', 0)))
        
        # Both have stop, tp1, tp2, tp3
        return cls(
            symbol=symbol.upper(),
            entry_price=entry_price,
            stop_loss=float(doc.get('stop', 0)),
            take_profit_1=float(doc.get('tp1', 0)),
            take_profit_2=float(doc.get('tp2')) if doc.get('tp2') else None,
            take_profit_3=float(doc.get('tp3')) if doc.get('tp3') else None,
            leverage=int(doc.get('leverage', 1)) if doc.get('leverage') else None,
            is_premium=doc.get('isPremium', False),
            type=SignalType(doc.get('type', 'spot')),
            signal_id=doc.get('_id', ''),
            timestamp=doc.get('createdAt')
        )
    
    def is_valid(self) -> bool:
        """Validate signal data"""
        return all([
            self.symbol,
            self.entry_price > 0,
            self.stop_loss > 0,
            self.take_profit_1 > 0
        ])


@dataclass
class Notification:
    """Notification data structure"""
    notification_id: str
    content: str
    symbol: Optional[str] = None
    pair: Optional[str] = None
    notification_type: Optional[str] = None
    created_at: Optional[str] = None
    
    @property
    def timestamp(self) -> Optional[str]:
        """Alias for created_at (for compatibility)"""
        return self.created_at
    
    @property
    def is_stop_signal(self) -> bool:
        """Check if notification indicates immediate stop/close"""
        if not self.content:
            return False
        
        content_lower = self.content.lower()
        stop_keywords = [
            'stop with',
            'closed with',
            'contract closed',
            'signal closed',
            'close immediately',
            'exit now',
            'emergency stop'
        ]
        
        return any(keyword in content_lower for keyword in stop_keywords)
    
    @property
    def is_stop_loss(self) -> bool:
        """Check if notification indicates stop loss (negative)"""
        if not self.is_stop_signal:
            return False
        
        content_lower = self.content.lower()
        
        # Check for explicit "without loss" or "no loss" patterns
        if 'without loss' in content_lower or 'no loss' in content_lower:
            return False
        
        # Check for loss indicators
        return 'loss' in content_lower or '% loss' in content_lower or '-' in self.content
    
    @property
    def extracted_symbol(self) -> Optional[str]:
        """Extract symbol from notification (symbol field or pair field)"""
        if self.symbol:
            return self.symbol.upper()
        
        if self.pair:
            # Remove common quote currencies
            pair = self.pair.upper()
            return pair.replace('USDT', '').replace('BUSD', '').replace('USD', '')
        
        return None
    
    @classmethod
    def from_firestore(cls, doc: Dict[str, Any]) -> 'Notification':
        """
        Create Notification from Firestore document
        
        Args:
            doc: Firestore document dictionary
            
        Returns:
            Notification instance
        """
        return cls(
            notification_id=doc.get('_id', ''),
            content=doc.get('content', ''),
            symbol=doc.get('symbol'),
            pair=doc.get('pair'),
            notification_type=doc.get('type'),
            created_at=doc.get('createdAt')
        )


@dataclass
class Position:
    """Open position tracking"""
    position_id: str
    symbol: str
    side: str
    entry_price: float
    quantity: float
    signal_id: str
    opened_at: str
    position_type: str = 'spot'  # spot or futures
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    position_value: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            'position_id': self.position_id,
            'symbol': self.symbol,
            'side': self.side,
            'entry_price': self.entry_price,
            'quantity': self.quantity,
            'signal_id': self.signal_id,
            'opened_at': self.opened_at,
            'position_type': self.position_type,
            'current_price': self.current_price,
            'unrealized_pnl': self.unrealized_pnl,
            'position_value': self.position_value
        }


@dataclass
class Trade:
    """Completed trade tracking"""
    trade_id: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    signal_id: str
    opened_at: str
    closed_at: str
    position_type: str = 'spot'  # spot or futures
    realized_pnl: float = 0.0
    trade_value: float = 0.0
    fees: float = 0.0
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            'trade_id': self.trade_id,
            'symbol': self.symbol,
            'side': self.side,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'quantity': self.quantity,
            'signal_id': self.signal_id,
            'opened_at': self.opened_at,
            'closed_at': self.closed_at,
            'position_type': self.position_type,
            'realized_pnl': self.realized_pnl,
            'trade_value': self.trade_value,
            'fees': self.fees,
            'stop_loss_price': self.stop_loss_price,
            'take_profit_price': self.take_profit_price
        }


@dataclass
class BalanceSnapshot:
    """Balance tracking snapshot"""
    timestamp: str
    total_balance: float
    spot_balance: float
    futures_balance: float
    available_balance: float
    used_balance: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            'timestamp': self.timestamp,
            'total_balance': self.total_balance,
            'spot_balance': self.spot_balance,
            'futures_balance': self.futures_balance,
            'available_balance': self.available_balance,
            'used_balance': self.used_balance,
            'unrealized_pnl': self.unrealized_pnl,
            'realized_pnl': self.realized_pnl
        }


@dataclass
class PortfolioMetrics:
    """Portfolio performance metrics"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    net_profit: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    last_updated: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.win_rate,
            'total_pnl': self.total_pnl,
            'total_fees': self.total_fees,
            'net_profit': self.net_profit,
            'max_drawdown': self.max_drawdown,
            'sharpe_ratio': self.sharpe_ratio,
            'avg_win': self.avg_win,
            'avg_loss': self.avg_loss,
            'profit_factor': self.profit_factor,
            'last_updated': self.last_updated
        }

