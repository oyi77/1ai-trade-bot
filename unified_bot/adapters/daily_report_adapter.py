"""
Daily Report Adapter — wraps scripts/daily_report.py.

Generates daily performance reports by querying the bridge API.
Returns formatted report strings for Telegram delivery.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

LOG = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))


@dataclass
class ReportConfig:
    bridge_url: str = "http://localhost:8765"
    target_server_cost: int = 2_500_000  # Rp
    report_timeout: int = 10


@dataclass
class TradeSummary:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    avg_pnl: float = 0.0


@dataclass
class DailyReport:
    date: str = ""
    server_uptime: str = ""
    active_users: int = 0
    signals_generated: int = 0
    trades: TradeSummary = field(default_factory=TradeSummary)
    revenue: int = 0
    server_cost_pct: float = 0.0
    raw_stats: dict = field(default_factory=dict)
    formatted: str = ""
    errors: list[str] = field(default_factory=list)


class DailyReportAdapter:
    """
    Adapter wrapping daily_report.py logic.

    Usage in UnifiedBot:
        dr = DailyReportAdapter(config)
        await dr.initialize()
        report = await dr.generate()
        print(report.formatted)  # or send via Telegram
    """

    def __init__(self, config: Optional[ReportConfig] = None):
        self.config = config or ReportConfig()
        self._initialized = False

    async def initialize(self) -> bool:
        try:
            self._initialized = True
            LOG.info("DailyReportAdapter initialized (bridge: %s)", self.config.bridge_url)
            return True
        except Exception as e:
            LOG.error("DailyReportAdapter init failed: %s", e)
            return False

    async def generate(self) -> DailyReport:
        """Generate the daily report."""
        if not self._initialized:
            await self.initialize()

        report = DailyReport(
            date=datetime.now(WIB).strftime("%Y-%m-%d"),
        )

        try:
            stats = await asyncio.to_thread(self._fetch_json, "/api/dash-stats")
            report.raw_stats = stats if isinstance(stats, dict) else {}

            if isinstance(stats, dict) and not stats.get("_error"):
                report.active_users = stats.get("active_users", 0)
                report.signals_generated = stats.get("signals_today", 0)
                report.server_uptime = stats.get("uptime", "unknown")

                trades_data = stats.get("trades_today", [])
                if trades_data:
                    report.trades = self._compute_trade_summary(trades_data)

                revenue = stats.get("revenue_today", 0)
                report.revenue = int(revenue) if revenue else 0
                if self.config.target_server_cost > 0:
                    report.server_cost_pct = round(
                        report.revenue / self.config.target_server_cost * 100, 1
                    )
            elif isinstance(stats, dict) and stats.get("_error"):
                report.errors.append(f"API error: {stats['_error']}")

            report.formatted = self._format_report(report)
        except Exception as e:
            report.errors.append(str(e))
            LOG.error("Daily report generation error: %s", e)
            report.formatted = f"Report generation failed: {e}"

        return report

    def _fetch_json(self, path: str) -> dict | list | None:
        url = f"{self.config.bridge_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.config.report_timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            return {"_error": str(e)}

    def _compute_trade_summary(self, trades: list[dict]) -> TradeSummary:
        total = len(trades)
        wins = sum(1 for t in trades if t.get("result") == "win" or t.get("pnl", 0) > 0)
        losses = total - wins
        pnls = [t.get("pnl", 0) for t in trades]
        total_pnl = sum(pnls)
        return TradeSummary(
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate=round(wins / total * 100, 1) if total else 0.0,
            total_pnl=round(total_pnl, 2),
            best_trade=round(max(pnls), 2) if pnls else 0.0,
            worst_trade=round(min(pnls), 2) if pnls else 0.0,
            avg_pnl=round(total_pnl / total, 2) if total else 0.0,
        )

    def _format_report(self, report: DailyReport) -> str:
        """Format the report as a Telegram-ready message."""
        t = report.trades
        lines = [
            f"DAILY REPORT — {report.date}",
            f"{'='*30}",
            f"Server Uptime: {report.server_uptime}",
            f"Active Users: {report.active_users}",
            f"Signals Today: {report.signals_generated}",
            "",
            f"TRADES TODAY:",
            f"  Total: {t.total_trades}",
            f"  Wins: {t.wins} | Losses: {t.losses}",
            f"  Win Rate: {t.win_rate}%",
            f"  Total PnL: ${t.total_pnl}",
            f"  Best: ${t.best_trade} | Worst: ${t.worst_trade}",
            f"  Avg: ${t.avg_pnl}",
            "",
            f"REVENUE: Rp{report.revenue:,}",
            f"Server Cost: {report.server_cost_pct}%",
        ]
        if report.errors:
            lines.append(f"\nErrors: {len(report.errors)}")
        return "\n".join(lines)

    async def shutdown(self) -> None:
        self._initialized = False
        LOG.info("DailyReportAdapter shutdown")
