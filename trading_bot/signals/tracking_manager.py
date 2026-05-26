#!/usr/bin/env python3
"""
Comprehensive tracking system for positions, balance, and trading history
SOLID: Single Responsibility, Open/Closed
DRY: Reusable tracking logic
KISS: Simple tracking management
SoC: Separation of concerns
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from pathlib import Path
import uuid

from .models import Position, Trade, BalanceSnapshot, PortfolioMetrics, Signal
from .utils import SymbolUtils

logger = logging.getLogger(__name__)


class TrackingManager:
    """Comprehensive tracking system for positions, balance, and trading history"""
    
    def __init__(self, data_dir: str = "."):
        """
        Initialize tracking manager
        
        Args:
            data_dir: Directory to store tracking data
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        # Data file paths
        self.positions_file = self.data_dir / "positions.json"
        self.trades_file = self.data_dir / "trades.json"
        self.balance_history_file = self.data_dir / "balance_history.json"
        self.portfolio_metrics_file = self.data_dir / "portfolio_metrics.json"
        
        # In-memory data
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.balance_history: List[BalanceSnapshot] = []
        self.portfolio_metrics = PortfolioMetrics()
        
        # Load existing data
        self._load_data()
    
    def _load_data(self):
        """Load existing tracking data from files"""
        try:
            # Load positions
            if self.positions_file.exists():
                with open(self.positions_file, 'r') as f:
                    positions_data = json.load(f)
                    for pos_id, pos_data in positions_data.items():
                        self.positions[pos_id] = Position(**pos_data)
                logger.info(f"📊 Loaded {len(self.positions)} positions from storage")
            
            # Load trades
            if self.trades_file.exists():
                with open(self.trades_file, 'r') as f:
                    trades_data = json.load(f)
                    self.trades = [Trade(**trade_data) for trade_data in trades_data]
                logger.info(f"📈 Loaded {len(self.trades)} completed trades from storage")
            
            # Load balance history
            if self.balance_history_file.exists():
                with open(self.balance_history_file, 'r') as f:
                    balance_data = json.load(f)
                    self.balance_history = [BalanceSnapshot(**snapshot) for snapshot in balance_data]
                logger.info(f"💰 Loaded {len(self.balance_history)} balance snapshots from storage")
            
            # Load portfolio metrics
            if self.portfolio_metrics_file.exists():
                with open(self.portfolio_metrics_file, 'r') as f:
                    metrics_data = json.load(f)
                    self.portfolio_metrics = PortfolioMetrics(**metrics_data)
                logger.info(f"📊 Loaded portfolio metrics from storage")
                
        except Exception as e:
            logger.error(f"Failed to load tracking data: {e}")
    
    def _save_data(self):
        """Save tracking data to files"""
        try:
            # Save positions
            positions_data = {pos_id: pos.to_dict() for pos_id, pos in self.positions.items()}
            with open(self.positions_file, 'w') as f:
                json.dump(positions_data, f, indent=2)
            
            # Save trades
            trades_data = [trade.to_dict() for trade in self.trades]
            with open(self.trades_file, 'w') as f:
                json.dump(trades_data, f, indent=2)
            
            # Save balance history
            balance_data = [snapshot.to_dict() for snapshot in self.balance_history]
            with open(self.balance_history_file, 'w') as f:
                json.dump(balance_data, f, indent=2)
            
            # Save portfolio metrics
            with open(self.portfolio_metrics_file, 'w') as f:
                json.dump(self.portfolio_metrics.to_dict(), f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save tracking data: {e}")
    
    def add_position(self, signal: Signal, order_id: str, entry_price: float, quantity: float) -> str:
        """
        Add a new position to tracking
        
        Args:
            signal: Trading signal
            order_id: Order ID from exchange
            entry_price: Entry price
            quantity: Position quantity
            
        Returns:
            Position ID
        """
        try:
            position_id = str(uuid.uuid4())
            current_time = datetime.now(timezone.utc).isoformat()
            
            position = Position(
                position_id=position_id,
                symbol=signal.symbol,
                side=signal.side,
                entry_price=entry_price,
                quantity=quantity,
                signal_id=signal.signal_id,
                opened_at=current_time,
                position_type=signal.signal_type.value,
                current_price=entry_price,
                position_value=entry_price * quantity
            )
            
            self.positions[position_id] = position
            self._save_data()
            
            logger.info(f"📊 Added position: {signal.symbol} - {signal.side} @ ${entry_price}")
            return position_id
            
        except Exception as e:
            logger.error(f"Failed to add position: {e}")
            return ""
    
    def close_position(self, position_id: str, exit_price: float, exit_reason: str = "manual") -> Optional[Trade]:
        """
        Close a position and create a completed trade
        
        Args:
            position_id: Position ID to close
            exit_price: Exit price
            exit_reason: Reason for closing
            
        Returns:
            Completed trade object or None if failed
        """
        try:
            if position_id not in self.positions:
                logger.warning(f"Position {position_id} not found")
                return None
            
            position = self.positions[position_id]
            current_time = datetime.now(timezone.utc).isoformat()
            
            # Calculate realized PnL
            if position.side.upper() == 'BUY':
                realized_pnl = (exit_price - position.entry_price) * position.quantity
            else:
                realized_pnl = (position.entry_price - exit_price) * position.quantity
            
            # Create completed trade
            trade = Trade(
                trade_id=str(uuid.uuid4()),
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                quantity=position.quantity,
                signal_id=position.signal_id,
                opened_at=position.opened_at,
                closed_at=current_time,
                position_type=position.position_type,
                realized_pnl=realized_pnl,
                trade_value=exit_price * position.quantity,
                fees=0.0  # TODO: Calculate actual fees
            )
            
            # Add to trades list
            self.trades.append(trade)
            
            # Remove from active positions
            del self.positions[position_id]
            
            # Update portfolio metrics
            self._update_portfolio_metrics()
            
            # Save data
            self._save_data()
            
            logger.info(f"📈 Closed position: {position.symbol} - PnL: ${realized_pnl:.2f}")
            return trade
            
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return None
    
    def update_position_prices(self, exchange):
        """
        Update current prices and unrealized PnL for all positions
        
        Args:
            exchange: CCXT exchange instance
        """
        try:
            for position_id, position in self.positions.items():
                try:
                    # Get current market price
                    symbol = SymbolUtils.format_symbol_for_exchange(
                        position.symbol, position.position_type, "bybit"
                    )
                    ticker = exchange.fetch_ticker(symbol)
                    current_price = ticker['last']
                    
                    # Update position
                    position.current_price = current_price
                    position.position_value = current_price * position.quantity
                    
                    # Calculate unrealized PnL
                    if position.side.upper() == 'BUY':
                        position.unrealized_pnl = (current_price - position.entry_price) * position.quantity
                    else:
                        position.unrealized_pnl = (position.entry_price - current_price) * position.quantity
                        
                except Exception as e:
                    logger.debug(f"Could not update price for {position.symbol}: {e}")
            
            # Save updated data
            self._save_data()
            
        except Exception as e:
            logger.error(f"Failed to update position prices: {e}")
    
    def add_balance_snapshot(self, exchange):
        """
        Add a balance snapshot to history
        
        Args:
            exchange: CCXT exchange instance
        """
        try:
            balance = exchange.fetch_balance()
            current_time = datetime.now(timezone.utc).isoformat()
            
            # Calculate total unrealized PnL
            total_unrealized_pnl = sum(pos.unrealized_pnl for pos in self.positions.values())
            
            # Calculate total realized PnL
            total_realized_pnl = sum(trade.realized_pnl for trade in self.trades)
            
            snapshot = BalanceSnapshot(
                timestamp=current_time,
                total_balance=balance.get('USDT', {}).get('total', 0),
                spot_balance=balance.get('USDT', {}).get('free', 0),
                futures_balance=balance.get('USDT', {}).get('used', 0),
                available_balance=balance.get('USDT', {}).get('free', 0),
                used_balance=balance.get('USDT', {}).get('used', 0),
                unrealized_pnl=total_unrealized_pnl,
                realized_pnl=total_realized_pnl
            )
            
            self.balance_history.append(snapshot)
            
            # Keep only last 1000 snapshots to prevent file from growing too large
            if len(self.balance_history) > 1000:
                self.balance_history = self.balance_history[-1000:]
            
            self._save_data()
            
        except Exception as e:
            logger.error(f"Failed to add balance snapshot: {e}")
    
    def _update_portfolio_metrics(self):
        """Update portfolio performance metrics"""
        try:
            if not self.trades:
                return
            
            # Basic metrics
            total_trades = len(self.trades)
            winning_trades = sum(1 for trade in self.trades if trade.realized_pnl > 0)
            losing_trades = sum(1 for trade in self.trades if trade.realized_pnl < 0)
            
            # Calculate win rate
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            # Calculate PnL metrics
            total_pnl = sum(trade.realized_pnl for trade in self.trades)
            total_fees = sum(trade.fees for trade in self.trades)
            net_profit = total_pnl - total_fees
            
            # Calculate average win/loss
            wins = [trade.realized_pnl for trade in self.trades if trade.realized_pnl > 0]
            losses = [trade.realized_pnl for trade in self.trades if trade.realized_pnl < 0]
            
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            
            # Calculate profit factor
            gross_profit = sum(wins) if wins else 0
            gross_loss = abs(sum(losses)) if losses else 0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
            
            # Calculate max drawdown (simplified)
            cumulative_pnl = []
            running_total = 0
            for trade in self.trades:
                running_total += trade.realized_pnl
                cumulative_pnl.append(running_total)
            
            max_drawdown = 0
            peak = 0
            for pnl in cumulative_pnl:
                if pnl > peak:
                    peak = pnl
                drawdown = peak - pnl
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
            
            # Update metrics
            self.portfolio_metrics = PortfolioMetrics(
                total_trades=total_trades,
                winning_trades=winning_trades,
                losing_trades=losing_trades,
                win_rate=win_rate,
                total_pnl=total_pnl,
                total_fees=total_fees,
                net_profit=net_profit,
                max_drawdown=max_drawdown,
                sharpe_ratio=0.0,  # TODO: Calculate Sharpe ratio
                avg_win=avg_win,
                avg_loss=avg_loss,
                profit_factor=profit_factor,
                last_updated=datetime.now(timezone.utc).isoformat()
            )
            
        except Exception as e:
            logger.error(f"Failed to update portfolio metrics: {e}")
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get comprehensive portfolio summary"""
        try:
            # Current positions summary
            total_unrealized_pnl = sum(pos.unrealized_pnl for pos in self.positions.values())
            total_position_value = sum(pos.position_value for pos in self.positions.values())
            
            # Recent balance
            current_balance = 0
            if self.balance_history:
                current_balance = self.balance_history[-1].total_balance
            
            return {
                'current_balance': current_balance,
                'active_positions': len(self.positions),
                'total_position_value': total_position_value,
                'unrealized_pnl': total_unrealized_pnl,
                'portfolio_metrics': self.portfolio_metrics.to_dict(),
                'recent_trades': [trade.to_dict() for trade in self.trades[-10:]],  # Last 10 trades
                'active_positions_detail': [pos.to_dict() for pos in self.positions.values()]
            }
            
        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return {}
    
    def get_trading_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trading history with optional limit"""
        try:
            return [trade.to_dict() for trade in self.trades[-limit:]]
        except Exception as e:
            logger.error(f"Failed to get trading history: {e}")
            return []
    
    def get_balance_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get balance history with optional limit"""
        try:
            return [snapshot.to_dict() for snapshot in self.balance_history[-limit:]]
        except Exception as e:
            logger.error(f"Failed to get balance history: {e}")
            return []
    
    def export_data(self, export_dir: str = None) -> Dict[str, str]:
        """
        Export all tracking data to files
        
        Args:
            export_dir: Directory to export data (defaults to data_dir)
            
        Returns:
            Dictionary of exported file paths
        """
        try:
            export_path = Path(export_dir) if export_dir else self.data_dir / "exports"
            export_path.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            exported_files = {}
            
            # Export positions
            positions_file = export_path / f"positions_{timestamp}.json"
            with open(positions_file, 'w') as f:
                json.dump({pos_id: pos.to_dict() for pos_id, pos in self.positions.items()}, f, indent=2)
            exported_files['positions'] = str(positions_file)
            
            # Export trades
            trades_file = export_path / f"trades_{timestamp}.json"
            with open(trades_file, 'w') as f:
                json.dump([trade.to_dict() for trade in self.trades], f, indent=2)
            exported_files['trades'] = str(trades_file)
            
            # Export balance history
            balance_file = export_path / f"balance_history_{timestamp}.json"
            with open(balance_file, 'w') as f:
                json.dump([snapshot.to_dict() for snapshot in self.balance_history], f, indent=2)
            exported_files['balance_history'] = str(balance_file)
            
            # Export portfolio metrics
            metrics_file = export_path / f"portfolio_metrics_{timestamp}.json"
            with open(metrics_file, 'w') as f:
                json.dump(self.portfolio_metrics.to_dict(), f, indent=2)
            exported_files['portfolio_metrics'] = str(metrics_file)
            
            logger.info(f"📊 Exported tracking data to {export_path}")
            return exported_files
            
        except Exception as e:
            logger.error(f"Failed to export data: {e}")
            return {}