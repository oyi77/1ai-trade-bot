"""
TradeTracker — SQLite-backed trade outcome tracking.

Tracks win rate, P&L, streaks, and trade history in a local SQLite
database. Provides both raw data access and formatted reports.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradebot.config import settings
from tradebot.storage.sqlite import SQLiteStorage

LOG = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
USD_IDR = 16350


@dataclass
class TradeRecord:
    """Single trade record from the database."""

    trade_id: str = ""
    symbol: str = ""
    action: str = ""  # BUY / SELL / CALL / PUT
    entry_price: float = 0.0
    exit_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    stake: float = 0.0
    outcome: str = ""  # OPEN, TP_HIT, SL_HIT, MANUAL, BREAKEVEN
    pips: float = 0.0
    profit_usd: float = 0.0
    profit_idr: int = 0
    open_time: str = ""
    close_time: str = ""
    source: str = ""
    confidence: float = 0.0
    grade: str = ""
    strategy: str = ""


@dataclass
class TradeStats:
    """Aggregated trade statistics."""

    total: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    total_pips: float = 0.0
    total_profit_usd: float = 0.0
    total_profit_idr: int = 0
    best_win_pips: float = 0.0
    worst_loss_pips: float = 0.0
    avg_win_pips: float = 0.0
    avg_loss_pips: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    open_positions: int = 0
    current_streak: str = "none"
    current_streak_count: int = 0


class TradeTracker:
    """SQLite-backed trade outcome tracker.

    Tracks trade lifecycle (open → close), computes win rate, P&L,
    streaks, and provides both raw data and formatted reports.

    Usage:
        tracker = TradeTracker()
        tracker.open_trade(signal={...}, entry_price=4350.0, symbol="XAUUSD")
        closed = tracker.check_outcomes({"XAUUSD": 4330.0})
        stats = tracker.get_stats()
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._storage = SQLiteStorage(db_path)
        self._init_db()

    # ── Public API ──

    def open_trade(
        self,
        signal: dict,
        entry_price: float,
        symbol: str = "XAUUSD",
        source: str = "ai",
        chat_id: str = "",
    ) -> str | None:
        """Record a new trade when signal is executed.

        Args:
            signal: Signal dict with action, sl, tp, confidence, grade.
            entry_price: Fill price of the trade.
            symbol: Trading symbol.
            source: Signal source label.
            chat_id: Telegram chat ID for context.

        Returns:
            Trade ID string, or None if rejected.
        """
        if not signal or signal.get("action") not in ("BUY", "SELL", "CALL", "PUT"):
            return None

        entry_price = float(entry_price) if entry_price else 0

        # Price sanity checks
        sym_upper = symbol.upper()
        if sym_upper in ("XAUUSD", "GOLD") and (entry_price < 2000 or entry_price > 6000):
            LOG.warning("open_trade REJECTED [%s]: entry=%s out of range", symbol, entry_price)
            return None
        if sym_upper in ("BTCUSD", "BTC") and (entry_price < 10000 or entry_price > 200000):
            LOG.warning("open_trade REJECTED [%s]: entry=%s out of range", symbol, entry_price)
            return None

        trade_id = f"tr_{int(time.time() * 1000)}"

        self._storage.execute(
            """INSERT INTO trades
               (trade_id, symbol, action, entry_price, sl, tp, stake,
                outcome, open_time, source, confidence, grade, chat_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trade_id,
                symbol,
                signal["action"],
                entry_price,
                float(signal.get("sl", 0)),
                float(signal.get("tp", 0)),
                float(signal.get("stake", settings.BROKER_DEFAULT_STAKE)),
                "OPEN",
                datetime.now(WIB).isoformat(),
                source,
                float(signal.get("confidence", 0)),
                str(signal.get("grade", "")),
                str(chat_id),
            ),
        )

        LOG.info("📝 Trade opened: %s %s %s @ %.2f", trade_id, signal["action"], symbol, entry_price)  # noqa: E501
        return trade_id

    def close_trade(
        self,
        trade_id: str,
        close_price: float,
        outcome: str = "MANUAL",
        symbol: str = "XAUUSD",
    ) -> TradeRecord | None:
        """Manually close an open trade.

        Args:
            trade_id: Trade identifier.
            close_price: Exit price.
            outcome: Outcome label (MANUAL, BREAKEVEN, etc.).
            symbol: Trading symbol.

        Returns:
            TradeRecord if found/closed, None otherwise.
        """
        trade = self._get_open_trade(trade_id)
        if trade is None:
            return None

        entry = trade["entry_price"]
        action = trade["action"]
        pips = self._compute_pips(close_price, entry, action, symbol)
        pip_val = self._pip_value(symbol)
        profit_usd = pips * pip_val

        self._storage.execute(
            """UPDATE trades SET
               exit_price=?, outcome=?, close_time=?, pips=?, profit_usd=?, profit_idr=?
               WHERE trade_id=? AND outcome='OPEN'""",
            (
                close_price,
                outcome,
                datetime.now(WIB).isoformat(),
                round(pips, 1),
                round(profit_usd, 2),
                round(profit_usd * USD_IDR),
                trade_id,
            ),
        )

        record = TradeRecord(
            trade_id=trade_id,
            symbol=trade["symbol"],
            action=action,
            entry_price=entry,
            exit_price=close_price,
            sl=trade["sl"],
            tp=trade["tp"],
            stake=trade["stake"],
            outcome=outcome,
            pips=round(pips, 1),
            profit_usd=round(profit_usd, 2),
            profit_idr=round(profit_usd * USD_IDR),
            open_time=trade["open_time"],
            close_time=datetime.now(WIB).isoformat(),
            source=trade["source"],
            confidence=trade["confidence"],
            grade=trade["grade"],
        )

        LOG.info("📝 Trade closed: %s %s | %.1f pips | $%.2f", trade_id, outcome, pips, profit_usd)
        return record

    def check_outcomes(
        self, current_prices: dict[str, float] | None = None
    ) -> list[TradeRecord]:
        """Check all open trades against current prices, close any hit TP/SL.

        Args:
            current_prices: Dict of symbol -> current price.

        Returns:
            List of TradeRecords that were just closed.
        """
        if not current_prices:
            return []

        open_trades = self._get_open_trades()
        closed: list[TradeRecord] = []

        for trade in open_trades:
            symbol = trade["symbol"]
            lookup = symbol.upper()
            price = current_prices.get(lookup, current_prices.get(symbol, 0))
            if not price or price <= 0:
                continue

            action = trade["action"]
            sl = trade["sl"]
            tp = trade["tp"]
            entry = trade["entry_price"]

            hit: str | None = None
            close_price = price

            # Check SL
            if sl > 0 and ((action in ("BUY", "CALL") and price <= sl) or \
                   (action in ("SELL", "PUT") and price >= sl)):
                hit = "SL_HIT"
                close_price = sl

            # Check TP
            if not hit and tp > 0 and ((action in ("BUY", "CALL") and price >= tp) or \
                   (action in ("SELL", "PUT") and price <= tp)):
                hit = "TP_HIT"
                close_price = tp

            if not hit:
                continue

            pips = self._compute_pips(close_price, entry, action, symbol)
            pip_val = self._pip_value(symbol)
            profit_usd = pips * pip_val

            self._storage.execute(
                """UPDATE trades SET
                   exit_price=?, outcome=?, close_time=?, pips=?, profit_usd=?, profit_idr=?
                   WHERE trade_id=? AND outcome='OPEN'""",
                (
                    close_price,
                    hit,
                    datetime.now(WIB).isoformat(),
                    round(pips, 1),
                    round(profit_usd, 2),
                    round(profit_usd * USD_IDR),
                    trade["trade_id"],
                ),
            )

            record = TradeRecord(
                trade_id=trade["trade_id"],
                symbol=symbol,
                action=action,
                entry_price=entry,
                exit_price=close_price,
                sl=sl,
                tp=tp,
                stake=trade["stake"],
                outcome=hit,
                pips=round(pips, 1),
                profit_usd=round(profit_usd, 2),
                profit_idr=round(profit_usd * USD_IDR),
                open_time=trade["open_time"],
                close_time=datetime.now(WIB).isoformat(),
                source=trade["source"],
                confidence=trade["confidence"],
                grade=trade["grade"],
            )
            closed.append(record)

            emoji = "✅" if hit == "TP_HIT" else "❌"
            LOG.info(
                "%s Trade closed: %s %s | %.1f pips | $%.2f",
                emoji, trade["trade_id"], hit, pips, profit_usd,
            )

        return closed

    def get_stats(self) -> TradeStats:
        """Get aggregated trade statistics from the database.

        Returns:
            TradeStats with win rate, P&L, streaks, etc.
        """
        row = self._storage.fetchone(
            """SELECT
               COUNT(*) as total,
               SUM(CASE WHEN outcome IN ('TP_HIT','WIN','WON') THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN outcome IN ('SL_HIT','LOSS','LOST') THEN 1 ELSE 0 END) as losses,
               SUM(CASE WHEN outcome='BREAKEVEN' THEN 1 ELSE 0 END) as breakeven,
               COALESCE(SUM(pips), 0) as total_pips,
               COALESCE(SUM(profit_usd), 0) as total_profit_usd
               FROM trades WHERE outcome != 'OPEN'"""
        )

        open_count = self._storage.fetchone(
            "SELECT COUNT(*) FROM trades WHERE outcome='OPEN'"
        )

        stats = TradeStats()
        if row:
            stats.total = row[0] or 0
            stats.wins = row[1] or 0
            stats.losses = row[2] or 0
            stats.breakeven = row[3] or 0
            stats.total_pips = round(row[4] or 0.0, 1)
            stats.total_profit_usd = round(row[5] or 0.0, 2)

        stats.open_positions = open_count[0] if open_count else 0
        total_decided = stats.wins + stats.losses
        stats.win_rate = round(stats.wins / total_decided * 100, 1) if total_decided > 0 else 0.0
        stats.total_profit_idr = round(stats.total_profit_usd * USD_IDR)

        # Best win / worst loss
        best = self._storage.fetchone(
            "SELECT MAX(pips) FROM trades WHERE outcome IN ('TP_HIT','WIN')"
        )
        if best and best[0]:
            stats.best_win_pips = round(best[0], 1)

        worst = self._storage.fetchone(
            "SELECT MIN(pips) FROM trades WHERE outcome IN ('SL_HIT','LOSS')"
        )
        if worst and worst[0]:
            stats.worst_loss_pips = round(abs(worst[0]), 1)

        # Averages
        if stats.wins > 0:
            avg_win = self._storage.fetchone(
                "SELECT AVG(pips) FROM trades WHERE outcome IN ('TP_HIT','WIN')"
            )
            if avg_win and avg_win[0]:
                stats.avg_win_pips = round(avg_win[0], 1)

        if stats.losses > 0:
            avg_loss = self._storage.fetchone(
                "SELECT AVG(pips) FROM trades WHERE outcome IN ('SL_HIT','LOSS')"
            )
            if avg_loss and avg_loss[0]:
                stats.avg_loss_pips = round(abs(avg_loss[0]), 1)

        # Strengths
        stats.max_consecutive_wins = self._compute_max_streak("TP_HIT", "WIN")
        stats.max_consecutive_losses = self._compute_max_streak("SL_HIT", "LOSS")

        # Current streak
        stats.current_streak, stats.current_streak_count = self._compute_current_streak()

        return stats

    def get_recent_trades(self, limit: int = 10) -> list[TradeRecord]:
        """Get most recent closed trades.

        Args:
            limit: Max number of trades to return.

        Returns:
            List of TradeRecord objects.
        """
        rows = self._storage.fetchall(
            """SELECT trade_id, symbol, action, entry_price, exit_price, sl, tp, stake,
                      outcome, pips, profit_usd, profit_idr, open_time, close_time,
                      source, confidence, grade
               FROM trades
               WHERE outcome != 'OPEN'
               ORDER BY close_time DESC
               LIMIT ?""",
            (limit,),
        )
        return [self._row_to_record(r) for r in rows]

    def get_open_trades(self) -> list[TradeRecord]:
        """Get all currently open trades.

        Returns:
            List of TradeRecord objects with outcome='OPEN'.
        """
        rows = self._storage.fetchall(
            """SELECT trade_id, symbol, action, entry_price, exit_price, sl, tp, stake,
                      outcome, pips, profit_usd, profit_idr, open_time, close_time,
                      source, confidence, grade
               FROM trades
               WHERE outcome='OPEN'
               ORDER BY open_time DESC"""
        )
        return [self._row_to_record(r) for r in rows]

    def get_daily_trades(self, date_str: str = "") -> dict[str, Any]:
        """Get all trades for a specific date (YYYY-MM-DD, WIB).

        Args:
            date_str: Date in YYYY-MM-DD format (defaults to today WIB).

        Returns:
            Dict with trades, counts, and pair breakdown.
        """
        if not date_str:
            date_str = datetime.now(WIB).strftime("%Y-%m-%d")

        rows = self._storage.fetchall(
            """SELECT trade_id, symbol, action, entry_price, exit_price, sl, tp, stake,
                      outcome, pips, profit_usd, profit_idr, open_time, close_time,
                      source, confidence, grade
               FROM trades
               WHERE open_time LIKE ?""",
            (f"{date_str}%",),
        )

        trades = [self._row_to_record(r) for r in rows]
        wins = [t for t in trades if t.outcome in ("TP_HIT", "WIN", "WON")]
        losses = [t for t in trades if t.outcome in ("SL_HIT", "LOSS", "LOST")]
        open_pos = [t for t in trades if t.outcome == "OPEN"]

        total_pips = sum(
            t.pips for t in trades if t.outcome not in ("OPEN", "")
        )

        # Pair breakdown
        pairs: dict[str, dict[str, Any]] = {}
        for t in trades:
            sym = t.symbol
            if sym not in pairs:
                pairs[sym] = {"total": 0, "wins": 0, "losses": 0, "pips": 0.0}
            pairs[sym]["total"] += 1
            if t.outcome in ("TP_HIT", "WIN", "WON"):
                pairs[sym]["wins"] += 1
            elif t.outcome in ("SL_HIT", "LOSS", "LOST"):
                pairs[sym]["losses"] += 1
            pairs[sym]["pips"] += t.pips

        return {
            "date": date_str,
            "trades": [asdict(t) for t in trades],
            "total_signals": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "open": len(open_pos),
            "total_pips": round(total_pips, 1),
            "win_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
            "micro_profit": round(sum(t.profit_usd for t in trades if t.outcome not in ("OPEN",)), 2),
            "micro_profit_idr": sum(t.profit_idr for t in trades if t.outcome not in ("OPEN",)),
            "pairs": pairs,
        }

    def format_winrate(self) -> str:
        """Format win rate summary for Telegram.

        Returns:
            HTML-formatted string.
        """
        stats = self.get_stats()
        perf = "🟢"
        if stats.win_rate < 40:
            perf = "🔴"
        elif stats.win_rate < 60:
            perf = "🟡"

        lines = [
            "📊 <b>TRADE PERFORMANCE</b>",
            "━" * 20,
            f"{perf} Win Rate: <b>{stats.win_rate:.1f}%</b> ({stats.wins}W / {stats.losses}L)",
            f"📈 Total Trades: {stats.total} | Open: {stats.open_positions}",
            "━" * 20,
            f"💰 Total Pips: {stats.total_pips:+.1f}",
            f"💵 Profit: <b>${stats.total_profit_usd:+,.2f}</b> (Rp {stats.total_profit_idr:+,})",
        ]

        if stats.best_win_pips:
            lines.append(f"✅ Best Win: +{stats.best_win_pips:.1f} pips")
        if stats.worst_loss_pips:
            lines.append(f"❌ Worst Loss: -{stats.worst_loss_pips:.1f} pips")
        if stats.avg_win_pips:
            lines.append(f"📈 Avg Win: {stats.avg_win_pips:+.1f} pips")
        if stats.avg_loss_pips:
            lines.append(f"📉 Avg Loss: {stats.avg_loss_pips:.1f} pips")
        if stats.max_consecutive_wins > 1:
            lines.append(f"🔥 Max Win Streak: {stats.max_consecutive_wins}")
        if stats.max_consecutive_losses > 1:
            lines.append(f"❄️ Max Loss Streak: {stats.max_consecutive_losses}")

        return "\n".join(lines)

    def format_history(self, limit: int = 10) -> str:
        """Format recent trade history for Telegram.

        Args:
            limit: Number of trades to show.

        Returns:
            HTML-formatted string.
        """
        trades = self.get_recent_trades(limit)
        if not trades:
            return "📭 No trade history yet."

        lines = ["📋 <b>TRADE HISTORY</b>", "━" * 20]
        for t in trades[:limit]:
            emoji = "✅" if t.outcome in ("TP_HIT", "WIN") else "❌" if t.outcome in ("SL_HIT", "LOSS") else "⚪"  # noqa: E501
            close_t = t.close_time[:16].replace("T", " ") if t.close_time else ""
            lines.append(
                f"{emoji} {t.action} {t.symbol} | {t.outcome}\n"
                f"   Pips: {t.pips:+.1f} | ${t.profit_usd:+.2f} (Rp {t.profit_idr:+,})\n"
                f"   {close_t}"
            )

        return "\n".join(lines)

    # ── Internal Helpers ──

    def _init_db(self) -> None:
        self._storage.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE NOT NULL,
                symbol TEXT NOT NULL DEFAULT 'XAUUSD',
                action TEXT NOT NULL,
                entry_price REAL NOT NULL DEFAULT 0,
                exit_price REAL DEFAULT 0,
                sl REAL DEFAULT 0,
                tp REAL DEFAULT 0,
                stake REAL DEFAULT 0,
                outcome TEXT NOT NULL DEFAULT 'OPEN',
                pips REAL DEFAULT 0,
                profit_usd REAL DEFAULT 0,
                profit_idr INTEGER DEFAULT 0,
                open_time TEXT NOT NULL,
                close_time TEXT,
                source TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                grade TEXT DEFAULT '',
                chat_id TEXT DEFAULT '',
                strategy TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        LOG.debug("Trade tracker DB initialized")

    def _get_open_trade(self, trade_id: str) -> dict | None:
        row = self._storage.fetchone(
            """SELECT trade_id, symbol, action, entry_price, sl, tp, stake,
                      open_time, source, confidence, grade
               FROM trades WHERE trade_id=? AND outcome='OPEN'""",
            (trade_id,),
        )
        if not row:
            LOG.warning("Open trade not found: %s", trade_id)
            return None
        return {
            "trade_id": row[0],
            "symbol": row[1],
            "action": row[2],
            "entry_price": row[3],
            "sl": row[4],
            "tp": row[5],
            "stake": row[6],
            "open_time": row[7],
            "source": row[8],
            "confidence": row[9],
            "grade": row[10],
        }

    def _get_open_trades(self) -> list[dict]:
        rows = self._storage.fetchall(
            """SELECT trade_id, symbol, action, entry_price, sl, tp, stake,
                      open_time, source, confidence, grade
               FROM trades WHERE outcome='OPEN'"""
        )
        return [
            {
                "trade_id": r[0], "symbol": r[1], "action": r[2],
                "entry_price": r[3], "sl": r[4], "tp": r[5], "stake": r[6],
                "open_time": r[7], "source": r[8], "confidence": r[9],
                "grade": r[10],
            }
            for r in rows
        ]

    @staticmethod
    def _row_to_record(row: tuple) -> TradeRecord:
        return TradeRecord(
            trade_id=row[0],
            symbol=row[1],
            action=row[2],
            entry_price=row[3] or 0.0,
            exit_price=row[4] or 0.0,
            sl=row[5] or 0.0,
            tp=row[6] or 0.0,
            stake=row[7] or 0.0,
            outcome=row[8],
            pips=row[9] or 0.0,
            profit_usd=row[10] or 0.0,
            profit_idr=row[11] or 0,
            open_time=row[12] or "",
            close_time=row[13] or "",
            source=row[14] or "",
            confidence=row[15] or 0.0,
            grade=row[16] or "",
        )

    @staticmethod
    def _compute_pips(close_price: float, entry: float, action: str, symbol: str) -> float:
        diff = close_price - entry if action in ("BUY", "CALL") else entry - close_price
        ps = TradeTracker._pip_size(symbol)
        return diff / ps if ps > 0 else 0.0

    @staticmethod
    def _pip_size(symbol: str) -> float:
        s = symbol.upper()
        if s in ("XAUUSD", "GOLD"):
            return 0.1
        if s in ("BTCUSD", "BTC"):
            return 1.0
        if s in ("ETHUSD", "ETH"):
            return 0.01
        if s.endswith("JPY"):
            return 0.01
        if s in ("USOIL", "OIL", "CL"):
            return 0.01
        return 0.0001

    @staticmethod
    def _pip_value(symbol: str) -> float:
        s = symbol.upper()
        if s in ("XAUUSD", "GOLD"):
            return 1.0
        if s in ("BTCUSD", "BTC"):
            return 1.0
        if s in ("ETHUSD", "ETH"):
            return 0.01
        if s.endswith("JPY"):
            return 9.0
        return 10.0

    def _compute_max_streak(self, *outcomes: str) -> int:
        rows = self._storage.fetchall(
            """SELECT outcome FROM trades
               WHERE outcome != 'OPEN'
               ORDER BY id ASC"""
        )
        max_streak = 0
        current = 0
        for row in rows:
            if row[0] in outcomes:
                current += 1
                if current > max_streak:
                    max_streak = current
            else:
                current = 0
        return max_streak

    def _compute_current_streak(self) -> tuple[str, int]:
        rows = self._storage.fetchall(
            """SELECT outcome FROM trades
               WHERE outcome != 'OPEN'
               ORDER BY id DESC"""
        )
        if not rows:
            return "none", 0

        first = rows[0][0]
        if first in ("TP_HIT", "WIN"):
            streak_type = "win"
        elif first in ("SL_HIT", "LOSS"):
            streak_type = "loss"
        else:
            return "none", 0

        count = 0
        for row in rows:
            if (streak_type == "win" and row[0] in ("TP_HIT", "WIN")) or \
               (streak_type == "loss" and row[0] in ("SL_HIT", "LOSS")):
                count += 1
            else:
                break
        return streak_type, count


__all__ = [
    "TradeTracker",
    "TradeRecord",
    "TradeStats",
]
