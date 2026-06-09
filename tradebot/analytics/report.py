"""
ReportGenerator — daily recap, trade performance, and backtest report formatting.

Produces structured text/markdown reports from analysis and trade data,
suitable for Telegram, console output, or export.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from tradebot.analytics.backtest import BacktestResult

LOG = logging.getLogger(__name__)

WIB_OFFSET_HOURS = 7


class ReportGenerator:
    """Generates formatted reports from trade data and analysis results.

    Usage:
        report = ReportGenerator()
        recap = report.daily_recap(trades=today_trades)
        perf = report.trade_performance_report(stats=my_stats)
        bt_report = report.backtest_report(result=backtest_result)
    """

    # ── Daily Recap ──

    @staticmethod
    def daily_recap(
        trades: list[dict],
        date_str: str = "",
        micro_lot: bool = True,
    ) -> str:
        """Generate a daily recap/report from trade data.

        Args:
            trades: List of trade dicts with keys like:
                outcome, action, symbol, pips, profit_usd, open_time, etc.
            date_str: Date string (YYYY-MM-DD). Auto-detected if empty.
            micro_lot: Whether to include $100 micro-lot simulation.

        Returns:
            Formatted recap string (HTML-ready for Telegram).
        """
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        daily = [t for t in trades if t.get("open_time", "").startswith(date_str)]
        wins = [t for t in daily if t.get("outcome") in ("TP_HIT", "WIN")]
        losses = [t for t in daily if t.get("outcome") in ("SL_HIT", "LOSS")]
        open_positions = [t for t in daily if t.get("outcome") in ("OPEN", None)]

        total = len(daily)
        win_count = len(wins)
        loss_count = len(losses)
        wr = round(win_count / max(win_count + loss_count, 1) * 100, 1)

        total_pips = sum(float(t.get("pips", 0)) for t in wins) - abs(
            sum(float(t.get("pips", 0)) for t in losses)
        )

        # Pair breakdown
        pairs: dict[str, dict[str, Any]] = {}
        for t in daily:
            sym = t.get("symbol", "?")
            if sym not in pairs:
                pairs[sym] = {"total": 0, "wins": 0, "losses": 0, "pips": 0.0}
            pairs[sym]["total"] += 1
            if t.get("outcome") in ("TP_HIT", "WIN"):
                pairs[sym]["wins"] += 1
            elif t.get("outcome") in ("SL_HIT", "LOSS"):
                pairs[sym]["losses"] += 1
            pairs[sym]["pips"] += float(t.get("pips", 0))

        # Format date
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday",
                         "Friday", "Saturday", "Sunday"]
            date_display = f"{day_names[dt.weekday()]}, {dt.strftime('%d %B %Y')}"
        except Exception:
            date_display = date_str

        # Micro lot simulation ($100 capital, 0.01 lot)
        micro_profit = 0.0
        if micro_lot:
            pip_values = {
                "XAUUSD": 0.10, "GOLD": 0.10,
                "EURUSD": 0.10, "GBPUSD": 0.10, "USDJPY": 0.09,
                "BTCUSD": 0.01, "USOIL": 0.10,
            }
            for t in daily:
                if t.get("outcome") in ("OPEN", None):
                    continue
                sym = t.get("symbol", "XAUUSD").upper()
                pip_val = pip_values.get(sym, 0.10)
                micro_profit += float(t.get("pips", 0)) * pip_val

        micro_pct = round(micro_profit / 100 * 100, 1)

        perf_emoji = "🟢" if micro_profit > 0 else "🔴" if micro_profit < 0 else "⚪"
        perf_label = "PROFIT" if micro_profit > 0 else "LOSS" if micro_profit < 0 else "FLAT"

        lines = [
            "📊 <b>DAILY SIGNAL RECAP</b>",
            f"🗓 {date_display}",
            "━" * 20,
            "",
            f"📡 <b>Total Signals:</b> {total}",
            f"✅ Win: {win_count} | ❌ Loss: {loss_count} | 📊 WR: <b>{wr:.1f}%</b>",
            f"📐 Total Pips: {total_pips:+.1f}",
            f"🔄 Open Positions: {len(open_positions)}",
            "",
        ]

        # Pair breakdown
        if pairs:
            lines.append("━" * 20)
            lines.append("💱 <b>Pair Breakdown:</b>")
            for sym, st in sorted(pairs.items()):
                emoji = "✅" if st["pips"] >= 0 else "❌"
                lines.append(
                    f"   {emoji} {sym}: {st['total']} signals | "
                    f"{st['pips']:+.1f} pips | {st['wins']}W/{st['losses']}L"
                )
            lines.append("")

        # Micro lot simulation
        if micro_lot:
            lines.extend([
                "━" * 20,
                "💵 <b>$100 MICRO LOT SIMULATION (0.01 lot)</b>",
                "",
                f"{perf_emoji} <b>{perf_label}:</b> ${micro_profit:+.2f}",
                f"Return: <b>{micro_pct:+.1f}%</b> in 1 day",
                "",
                "━" * 20,
                "⚡ <i>This is a projection — not actual trading results.</i>",
            ])

        return "\n".join(lines)

    # ── Trade Performance Report ──

    @staticmethod
    def trade_performance_report(stats: dict[str, Any]) -> str:
        """Generate a trade performance summary from stats dict.

        Expected keys:
            total, wins, losses, win_rate, total_pips, total_profit_usd,
            open_positions, best_win_pips, worst_loss_pips,
            max_consecutive_wins, max_consecutive_losses, avg_win, avg_loss

        Returns:
            Formatted performance report string.
        """
        total = stats.get("total", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        wr = stats.get("win_rate", 0.0)
        pips = stats.get("total_pips", 0.0)
        profit = stats.get("total_profit_usd", 0.0)
        open_pos = stats.get("open_positions", 0)

        perf_emoji = "🟢"
        if wr < 40:
            perf_emoji = "🔴"
        elif wr < 60:
            perf_emoji = "🟡"

        lines = [
            "📊 <b>TRADE PERFORMANCE</b>",
            "━" * 20,
            f"{perf_emoji} Win Rate: <b>{wr:.1f}%</b> ({wins}W / {losses}L)",
            f"📈 Total Trades: {total} | Open: {open_pos}",
            "━" * 20,
            f"💰 Total Pips: {pips:+.1f}",
            f"💵 Profit: <b>${profit:+,.2f}</b>",
        ]

        avg_win = stats.get("avg_win", 0)
        avg_loss = stats.get("avg_loss", 0)
        if avg_win:
            lines.append(f"✅ Avg Win: {avg_win:+.1f} pips")
        if avg_loss:
            lines.append(f"❌ Avg Loss: {avg_loss:.1f} pips")

        best_win = stats.get("best_win_pips", 0)
        worst_loss = stats.get("worst_loss_pips", 0)
        if best_win:
            lines.append(f"🏆 Best Win: +{best_win:.1f} pips")
        if worst_loss:
            lines.append(f"💀 Worst Loss: -{worst_loss:.1f} pips")

        max_cons_w = stats.get("max_consecutive_wins", 0)
        max_cons_l = stats.get("max_consecutive_losses", 0)
        if max_cons_w:
            lines.append(f"🔥 Max Consecutive Wins: {max_cons_w}")
        if max_cons_l:
            lines.append(f"❄️ Max Consecutive Losses: {max_cons_l}")

        return "\n".join(lines)

    # ── Backtest Report ──

    @staticmethod
    def backtest_report(result: BacktestResult) -> str:
        """Format a backtest result as a readable report.

        Args:
            result: BacktestResult from BacktestEngine.

        Returns:
            Formatted backtest report string.
        """
        lines = [
            "📊  <b>BACKTEST RESULT</b>",
            "═" * 40,
            f"  Symbol:           {result.symbol}",
            f"  Strategy:         {result.strategy}",
            f"  Duration:         {result.duration_seconds:.1f}s",
            f"  Total ticks:      {result.total_ticks:,}",
            "",
            "─" * 40,
            f"  Total trades:     {result.total_trades}",
            f"  Wins/Losses:      {result.wins}W / {result.losses}L",
            f"  Win rate:         {result.win_rate:.1%}",
            f"  Profit factor:    {result.profit_factor:.2f}",
            f"  Sharpe ratio:     {result.sharpe_ratio:.2f}",
            "",
            "─" * 40,
            f"  Gross profit:     ${result.gross_profit:+,.2f}",
            f"  Gross loss:       ${result.gross_loss:+,.2f}",
            f"  Net PnL:          ${result.net_pnl:+,.2f}",
            f"  Max drawdown:     {result.max_drawdown:.1%}",
            "",
            "─" * 40,
            f"  Avg win:          ${result.avg_win:+.4f}",
            f"  Avg loss:         ${result.avg_loss:.4f}",
            f"  Max consecutive W: {result.max_consecutive_wins}",
            f"  Max consecutive L: {result.max_consecutive_losses}",
            "═" * 40,
        ]
        return "\n".join(lines)

    # ── Status / Snapshot ──

    @staticmethod
    def status_snapshot(
        engine_status: str = "idle",
        open_positions: int = 0,
        last_signal_time: str | None = None,
        balance: float | None = None,
        uptime_seconds: float | None = None,
    ) -> str:
        """Format a quick bot status snapshot."""
        lines = [
            "🤖  <b>BOT STATUS</b>",
            "━" * 20,
            f"  Engine:          {engine_status}",
            f"  Open positions:  {open_positions}",
            f"  Last signal:     {last_signal_time or 'N/A'}",
        ]
        if balance is not None:
            lines.append(f"  Balance:         ${balance:+.2f}")
        if uptime_seconds is not None:
            hours = uptime_seconds / 3600
            lines.append(f"  Uptime:          {hours:.1f}h")

        return "\n".join(lines)


__all__ = [
    "ReportGenerator",
]
