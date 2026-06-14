#!/usr/bin/env python3
"""
tradebot.cli — Unified CLI Entry Point
=======================================

Single entry point for all tradebot operations.
Mapped to ``tradebot`` console script in pyproject.toml.

Usage::

    tradebot test [symbol]              # Connection + pattern test
    tradebot trade [symbol]             # One live trade cycle
    tradebot stream [symbol]            # Live tick stream (30s)
    tradebot backtest [symbol] [pattern] [count]  # Historical backtest
    tradebot bridge [port]              # HTTP signal bridge server
    tradebot signals                    # Show latest market signal
    tradebot health                     # Run HealthService checks
    tradebot monitor                    # Start HealthProbe HTTP server
    tradebot analytics                  # Daily mapping + session levels
    tradebot bot start/stop             # Manage a trading bot
    tradebot config                     # Show sanitised config
    tradebot version                    # Show version

All config from tradebot.config.settings (pydantic-settings → .env).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from typing import Any

from tradebot.__version__ import __version__
from tradebot.analytics.analyzer import MarketAnalyzer
from tradebot.brokers.deriv import (
    DerivWSClient,
    DigitMartingaleStrategy,
    MomenPatternAnalyzer,
)
from tradebot.brokers.deriv.backtest import DigitBacktestEngine, print_summary
from tradebot.brokers.deriv.config import (
    DEFAULT_SYMBOL,
)
from tradebot.config import settings
from tradebot.logging import setup_logging
from tradebot.monitoring.health import HealthProbe
from tradebot.services.health import HealthService, HealthStatus

# ── Rich output ─────────────────────────────────────────────────────────
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table

    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False

LOG = logging.getLogger("tradebot.cli")

# ── ANSI helpers (fallback when rich is missing) ────────────────────────


def _green(s: str) -> str:
    return f"\033[92m{s}\033[0m"


def _red(s: str) -> str:
    return f"\033[91m{s}\033[0m"


def _yellow(s: str) -> str:
    return f"\033[93m{s}\033[0m"


def _cyan(s: str) -> str:
    return f"\033[96m{s}\033[0m"


def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m"


# ── Rich helpers ────────────────────────────────────────────────────────


def _console() -> Console:
    return Console()


def _print_info(title: str, body: str) -> None:
    if _HAS_RICH:
        _console().print(Panel(body, title=title, border_style="blue"))
    else:
        print(f"\n── {_bold(title)} ──")
        print(body)


def _print_table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    if _HAS_RICH:
        table = Table(title=title, box=box.ROUNDED, header_style="bold cyan")
        for c in columns:
            table.add_column(c)
        for r in rows:
            table.add_row(*r)
        _console().print(table)
    else:
        print(f"\n── {_bold(title)} ──")
        header = " | ".join(columns)
        print(header)
        print("-" * len(header))
        for r in rows:
            print(" | ".join(r))


def _print_json(data: Any) -> None:
    if _HAS_RICH:
        _console().print(
            Syntax(
                json.dumps(data, indent=2, default=str),
                "json",
                theme="monokai",
            )
        )
    else:
        print(json.dumps(data, indent=2, default=str))


# ── Client Factory (reads from settings) ──


def make_client() -> DerivWSClient:
    """Create a DerivWSClient from settings (no hardcoded values)."""
    return DerivWSClient(
        api_token=settings.DERIV_API_TOKEN or "",
        app_id=settings.DERIV_APP_ID or "",
        pat_token=settings.DERIV_PAT_TOKEN or "",
        account_id=settings.DERIV_ACCOUNT_ID or "",
    )


def _resolve_symbol(symbol: str | None) -> str:
    return symbol or settings.DERIV_SYMBOL or DEFAULT_SYMBOL


# ── Command: test ──


async def cmd_test(symbol: str) -> int:
    """Connection test — connect, show balance, run pattern analysis."""
    client = make_client()
    ok = await client.connect()
    if not ok:
        _print_info("Connection", "Connect failed")
        return 1

    bal = await client.get_balance()
    symbols = await client.get_active_symbols()
    ticks = await client.get_ticks_history(symbol, count=20)
    ticks100 = await client.get_ticks_history(symbol, count=100)

    momen = MomenPatternAnalyzer()
    result = momen.analyze(ticks100)
    freq = Counter(t.digit for t in ticks100)

    await client.disconnect()

    if _HAS_RICH:
        con = _console()
        con.print(
            Panel(
                f"[bold]Balance:[/]  [green]${bal or 0.0:.2f}[/]\n"
                f"[bold]Symbols:[/]  {len(symbols)} active",
                title="Connection Test",
                border_style="green",
            )
        )
        tick_table = Table(title=f"{symbol} — Last {len(ticks)} Ticks", box=box.SIMPLE)
        tick_table.add_column("#", style="dim")
        tick_table.add_column("Digit", style="cyan")
        tick_table.add_column("Price", style="yellow")
        for i, t in enumerate(ticks[-10:], 1):
            tick_table.add_row(str(i), str(t.digit), f"${t.price:.4f}")
        con.print(tick_table)

        if result:
            con.print(
                Panel(
                    f"[bold]Carrier:[/] {result.carrier}   "
                    f"[bold]Confidence:[/] {result.confidence * 100:.0f}%",
                    title="Momen Pattern",
                    border_style="cyan",
                )
            )
        else:
            con.print("[yellow]No Momen pattern found[/]")

        bar_table = Table(title="Digit Distribution (last 100)", box=box.SIMPLE)
        bar_table.add_column("Digit", style="cyan")
        bar_table.add_column("Count", style="yellow")
        bar_table.add_column("Bar")
        for d in range(10):
            bar_table.add_row(str(d), str(freq.get(d, 0)), "█" * freq.get(d, 0))
        con.print(bar_table)
    else:
        print(f"\n── {_bold('CONNECTION TEST')} ──")
        print(f"  Balance:  ${bal or 0.0:.2f}")
        print(f"  Symbols:  {len(symbols)} active")
        print(f"\n── {_bold(f'{symbol} — Last Ticks')} ──")
        for i, t in enumerate(ticks[-10:], 1):
            print(f"  {i:2d}.  digit={t.digit}  ${t.price:.4f}")
        if result:
            print(f"\nMomen:  carrier={result.carrier}  conf={result.confidence * 100:.0f}%")
        else:
            print("\nNo Momen pattern found")
        print(f"\n── {_bold('Digit Distribution (last 100)')} ──")
        for d in range(10):
            print(f"  {d}: {'█' * freq.get(d, 0)} ({freq.get(d, 0)})")

    LOG.info("Test complete")
    return 0


# ── Command: trade ──


async def cmd_trade(symbol: str) -> int:
    """Run one live trade cycle using DigitMartingaleStrategy."""
    client = make_client()
    await client.connect()

    strategy = DigitMartingaleStrategy(client=client, symbol=symbol)
    result = await strategy.analyse_and_trade()

    await client.disconnect()

    lines = [
        f"P/L:        ${result.profit:+.2f}",
        f"Wins:       {result.wins}",
        f"Losses:     {result.losses}",
        f"Win Rate:   {result.win_rate:.0f}%",
        f"Trades:     {result.trades}",
    ]
    _print_info("Trade Result", "\n".join(lines))
    return 0


# ── Command: stream ──


async def cmd_stream(symbol: str, duration: int = 30) -> int:
    """Subscribe to live ticks and print them for *duration* seconds."""
    client = make_client()
    await client.connect()

    tick_queue: asyncio.Queue = asyncio.Queue()
    client.on("tick", lambda t: tick_queue.put_nowait(t))
    await client.subscribe_ticks(symbol)

    if _HAS_RICH:
        con = _console()
        con.print(f"[bold cyan]Streaming {symbol} for {duration}s...[/]")

    start = asyncio.get_event_loop().time()
    count = 0
    captured: list[tuple[int, float]] = []

    while asyncio.get_event_loop().time() - start < duration:
        try:
            tick = await asyncio.wait_for(tick_queue.get(), timeout=5)
        except TimeoutError:
            continue
        count += 1
        captured.append((tick.digit, tick.price))
        if _HAS_RICH:
            con.print(
                f"  [dim]{count:3d}.[/]  digit=[cyan]{tick.digit}[/]  $[yellow]{tick.price:.4f}[/]"
            )
        else:
            print(f"  {count:3d}.  digit={tick.digit}  ${tick.price:.4f}")

    await client.disconnect()

    freq = Counter(d for d, _ in captured)
    summary_lines = [f"Ticks captured:  {count}"]
    for d in range(10):
        summary_lines.append(f"  {d}: {'█' * freq.get(d, 0)} ({freq.get(d, 0)})")
    _print_info("Stream Summary", "\n".join(summary_lines))
    return 0


# ── Command: backtest ──


async def cmd_backtest(symbol: str, pattern: str, count: int) -> int:
    """Run tick-by-tick backtest and print summary."""
    client = make_client()
    await client.connect()

    engine = DigitBacktestEngine(client=client)
    summary = await engine.run(pattern_type=pattern, symbol=symbol, count=count)

    await client.disconnect()

    print_summary(summary)

    if _HAS_RICH and hasattr(summary, "to_dict"):
        rows = [
            ["Symbol", getattr(summary, "symbol", symbol)],
            ["Strategy", getattr(summary, "strategy", pattern)],
            ["Total ticks", str(getattr(summary, "total_ticks", count))],
            ["Total trades", str(getattr(summary, "total_trades", 0))],
            ["Wins", str(getattr(summary, "wins", 0))],
            ["Losses", str(getattr(summary, "losses", 0))],
            ["Win rate", f"{getattr(summary, 'win_rate', 0):.1%}"],
            ["Net PnL", f"${getattr(summary, 'net_pnl', 0):+,.2f}"],
        ]
        _print_table("Backtest Result", ["Metric", "Value"], rows)

    return 0


# ── Command: bridge ──


def cmd_bridge(port: int) -> int:
    """Start the HTTP signal bridge server (blocking)."""
    from tradebot.brokers.deriv.bridge import DerivBridgeRunner

    runner = DerivBridgeRunner(
        symbol=DEFAULT_SYMBOL,
        host=settings.BRIDGE_HOST,
        port=port,
    )
    if _HAS_RICH:
        _console().print(
            Panel(
                f"Bridge listening on [bold]{settings.BRIDGE_HOST}:{port}[/]",
                title="Signal Bridge",
                border_style="green",
            )
        )
    else:
        print(f"\nBridge listening on {settings.BRIDGE_HOST}:{port}")
    try:
        runner.start()
    except KeyboardInterrupt:
        LOG.info("Bridge stopped")
    return 0


# ── Command: signals ──


async def cmd_signals() -> int:
    """Connect to Deriv, analyse patterns, and show the latest signal."""
    client = make_client()
    ok = await client.connect()
    if not ok:
        _print_info("Signals", "Connect failed")
        return 1

    bal = await client.get_balance()
    symbol = _resolve_symbol(None)

    ticks = await client.get_ticks_history(symbol, count=100)
    if not ticks:
        _print_info("Signals", "No ticks fetched")
        await client.disconnect()
        return 1

    momen = MomenPatternAnalyzer()
    momen_result = momen.analyze(ticks)

    from tradebot.brokers.deriv.patterns import (
        AdjacencyPatternAnalyzer,
        StreakCountdownAnalyzer,
    )

    adj = AdjacencyPatternAnalyzer()
    adj_result = adj.analyze(ticks)

    streak = StreakCountdownAnalyzer()
    streak_result = streak.analyze(ticks)

    current_tick = ticks[-1]
    freq = Counter(t.digit for t in ticks[-30:])

    await client.disconnect()

    rows = [
        ["Symbol", symbol],
        ["Current price", f"${current_tick.price:.4f}"],
        ["Current digit", str(current_tick.digit)],
        ["Balance", f"${bal:.2f}" if bal else "N/A"],
    ]
    _print_table("Live Signal Report", ["Field", "Value"], rows)

    pattern_lines: list[str] = []
    if momen_result:
        pattern_lines.append(
            f"Momen:         carrier={momen_result.carrier}  "
            f"conf={momen_result.confidence:.0%}  "
            f"M1={momen_result.total_m1}  M2={momen_result.total_m2}"
        )
    else:
        pattern_lines.append("Momen:         No pattern found")
    if adj_result:
        pattern_lines.append(
            f"Adjacency:     trigger={adj_result.trigger} -> target={adj_result.target}  "
            f"freq={adj_result.freq}  flood_ok={adj_result.anti_flood_ok}"
        )
    else:
        pattern_lines.append("Adjacency:     No pattern found")
    if streak_result:
        pattern_lines.append(
            f"Streak:        {streak_result.trigger_digit} streak={streak_result.streak_length}/"
            f"{streak_result.required_streak}  fire=tick+{streak_result.op_tick_countdown}  "
            f"conf={streak_result.confidence:.0%}"
        )
    else:
        pattern_lines.append("Streak:        No pattern found")
    _print_info("Pattern Analysis", "\n".join(pattern_lines))

    freq_lines = [f"  {' '.join(str(t.digit) for t in ticks[-30:])}", ""]
    for d in range(10):
        freq_lines.append(f"  {d}: {'█' * freq.get(d, 0)} ({freq.get(d, 0)})")
    _print_info("Last 30 Digits", "\n".join(freq_lines))

    return 0


# ── Command: health ──


async def cmd_health() -> int:
    """Run HealthService checks and display results."""
    health = HealthService()
    report = await health.run_all()

    if _HAS_RICH:
        con = _console()
        status_color = {
            HealthStatus.OK: "green",
            HealthStatus.DEGRADED: "yellow",
            HealthStatus.DOWN: "red",
        }.get(report.status, "white")

        con.print(
            Panel(
                f"Overall: [bold {status_color}]{report.status.value}[/]",
                title="Health Check",
                border_style=status_color,
            )
        )

        check_table = Table(box=box.ROUNDED, header_style="bold cyan")
        check_table.add_column("Check", style="cyan")
        check_table.add_column("Status")
        check_table.add_column("Detail")
        check_table.add_column("Latency")
        for c in report.checks:
            st = {"ok": "green", "degraded": "yellow", "down": "red"}.get(c.status.value, "white")
            check_table.add_row(
                c.name,
                f"[{st}]{c.status.value}[/]",
                c.detail,
                f"{c.latency_ms:.1f}ms" if c.latency_ms else "-",
            )
        con.print(check_table)
        con.print(f"[dim]{report.summary}[/]")
    else:
        print(f"\n── {_bold('Health Check')} ──")
        print(f"  Overall: {report.status.value}")
        print()
        for c in report.checks:
            print(f"  {c.name:30s}  {c.status.value:10s}  {c.detail}")
        print()
        print(f"  {report.summary}")

    return 0 if report.ok else 1


# ── Command: monitor ──


def cmd_monitor() -> int:
    """Start HealthProbe HTTP server (blocking)."""
    probe = HealthProbe(extra_checks=None)
    probe.set_liveness(True)
    probe.set_readiness(True)
    probe.set_startup(True)

    if _HAS_RICH:
        _console().print(
            Panel(
                f"Listening on [bold]{probe.host}:{probe.port}[/]\n"
                f"Endpoints:  [cyan]/healthz[/]  [cyan]/livez[/]  [cyan]/readyz[/]  [cyan]/startupz[/]",  # noqa: E501
                title="Health Probe Server",
                border_style="green",
            )
        )
    else:
        print(f"\nHealth probe on {probe.host}:{probe.port}")
        print("  Endpoints: /healthz  /livez  /readyz  /startupz")

    try:
        probe.start()
    except KeyboardInterrupt:
        LOG.info("Monitor stopped")
    return 0


# ── Command: analytics ──


async def cmd_analytics() -> int:
    """Run daily mapping + session levels analysis."""
    analyzer = MarketAnalyzer()
    mapping = await analyzer.get_daily_mapping()

    if _HAS_RICH:
        con = _console()
        momentum_color = {
            "BULLISH": "green",
            "BEARISH": "red",
        }.get(mapping.momentum, "yellow")
        con.print(
            Panel(
                f"Date:     [bold]{mapping.date}[/]\n"
                f"Session:  [cyan]{mapping.current_session}[/]\n"
                f"Momentum: [{momentum_color}]{mapping.momentum}[/]\n"
                f"DXY:      {f'${mapping.dxy:.2f}' if mapping.dxy else 'N/A'}\n"
                f"NFP Fri:  {'Yes' if mapping.is_nfp_friday else 'No'}",
                title="Daily Market Mapping",
                border_style="blue",
            )
        )

        if mapping.prices:
            px_table = Table(title="Key Prices", box=box.SIMPLE, header_style="bold cyan")
            px_table.add_column("Symbol")
            px_table.add_column("Price")
            px_table.add_column("Change")
            for sym, info in sorted(mapping.prices.items()):
                chg = info.get("change", 0)
                chg_str = f"[green]{chg:+.2f}%[/]" if chg >= 0 else f"[red]{chg:+.2f}%[/]"
                px_table.add_row(sym, f"${info.get('price', 0):.4f}", chg_str)
            con.print(px_table)
    else:
        print(f"\n── {_bold('Daily Market Mapping')} ──")
        print(f"  Date:       {mapping.date}")
        print(f"  Session:    {mapping.current_session}")
        print(f"  Momentum:   {mapping.momentum}")
        print(f"  DXY:        {mapping.dxy if mapping.dxy else 'N/A'}")
        print()
        if mapping.prices:
            print("── Key Prices ──")
            for sym, info in sorted(mapping.prices.items()):
                chg = info.get("change", 0)
                print(f"  {sym:10s}  ${info.get('price', 0):.4f}  ({chg:+.2f}%)")
        print()

    if mapping.session_levels:
        sl = mapping.session_levels
        sl_rows = [
            ["Asia High/Low", f"{sl.asia_high} / {sl.asia_low}"],
            ["London High/Low", f"{sl.london_high} / {sl.london_low}"],
            ["NY High/Low", f"{sl.ny_high} / {sl.ny_low}"],
            ["Prev Day High/Low", f"{sl.prev_day_high} / {sl.prev_day_low}"],
            ["Today High/Low", f"{sl.today_high} / {sl.today_low}"],
        ]
        _print_table("Session Levels", ["Level", "Price"], sl_rows)

    return 0


# ── Command: bot ──


_KNOWN_BOTS = {
    "vilona": "tradebot.bots.platforms.vilona",
    "subscription": "tradebot.bots.subscription",
    "stockity": "tradebot.bots.stockity.bot",
}


def cmd_bot(action: str, name: str) -> int:
    """Start or stop a trading bot."""
    if name not in _KNOWN_BOTS:
        known = ", ".join(_KNOWN_BOTS)
        _print_info("Bot Manager", f"Unknown bot: {name}\nKnown bots: {known}")
        return 1

    module_path = _KNOWN_BOTS[name]

    if action == "start":
        try:
            __import__(module_path, fromlist=["_"])
            LOG.info("%s bot module loaded (%s)", name, module_path)
            if _HAS_RICH:
                _console().print(
                    Panel(
                        f"[bold green]{name}[/] bot loaded\nModule: {module_path}",
                        title="Bot Manager",
                        border_style="green",
                    )
                )
            else:
                print(f"\n{name} bot loaded (module: {module_path})")
        except ImportError as exc:
            _print_info("Bot Manager", f"Failed to load {name}: {exc}")
            return 1
    elif action == "stop":
        LOG.info("%s bot stop requested", name)
        if _HAS_RICH:
            _console().print(
                Panel(
                    f"[bold yellow]{name}[/] bot stop requested",
                    title="Bot Manager",
                    border_style="yellow",
                )
            )
        else:
            print(f"\n{name} bot stop requested")
    else:
        _print_info("Bot Manager", f"Unknown action: {action} (use start or stop)")
        return 1

    return 0


# ── Command: config ──


def cmd_config(show_path: bool = False) -> int:
    """Show current config (sanitized, no secrets)."""
    if show_path:
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
            os.path.join(os.path.expanduser("~"), "projects", "1ai-trade-bot", ".env"),
            os.path.join(os.getcwd(), ".env"),
        ]
        for p in candidates:
            resolved = os.path.abspath(p)
            if os.path.isfile(resolved):
                if _HAS_RICH:
                    _console().print(
                        Panel(f"[cyan]{resolved}[/]", title="Config Path", border_style="blue")
                    )
                else:
                    print(f"Config path: {resolved}")
                return 0
        if _HAS_RICH:
            _console().print("[yellow]No .env file found[/]")
        else:
            print("No .env file found")
        return 1

    SECRET_KEYS = {"KEY", "COOKIE", "AUTH", "TOKEN"}  # noqa: N806
    config_dict: dict[str, Any] = {}
    for field_name in settings.model_fields:
        value = getattr(settings, field_name)
        if any(s in field_name.upper() for s in SECRET_KEYS):
            value = "***" if value else ""
        elif isinstance(value, (int, float, bool)):
            pass
        elif isinstance(value, str) and len(value) > 40:
            value = value[:20] + "..."
        config_dict[field_name] = value

    grouped: dict[str, dict[str, Any]] = {}
    for key, val in sorted(config_dict.items()):
        prefix = key.split("_")[0] if "_" in key else "MISC"
        grouped.setdefault(prefix, {})[key] = val

    if _HAS_RICH:
        con = _console()
        con.print(
            Panel(
                f"[bold]Settings loaded[/]  ({len(settings.model_fields)} fields)",
                title="Configuration",
                border_style="blue",
            )
        )
        for prefix, fields in sorted(grouped.items()):
            table = Table(title=f"[{prefix}]", box=box.SIMPLE, header_style="bold cyan")
            table.add_column("Key")
            table.add_column("Value")
            for k, v in fields.items():
                table.add_row(k, str(v))
            con.print(table)
    else:
        print(f"\n── {_bold('Configuration')} ──")
        print(f"  ({len(settings.model_fields)} fields)\n")
        for prefix, fields in sorted(grouped.items()):
            print(f"── [{prefix}] ──")
            for k, v in fields.items():
                print(f"  {k:35s}  {v}")
            print()

    return 0


# ── Command: version ──


def cmd_version(json_output: bool = False) -> int:
    """Show tradebot version."""
    data = {
        "version": __version__,
        "package": "tradebot",
        "python": sys.version.split()[0],
        "rich": _HAS_RICH,
    }

    if json_output:
        _print_json(data)
    elif _HAS_RICH:
        _console().print(
            Panel(
                f"[bold cyan]tradebot[/]  v[bold yellow]{__version__}[/]\n"
                f"Python  [dim]{sys.version.split()[0]}[/]\n"
                f"Rich    [dim]{'yes' if _HAS_RICH else 'no'}[/]",
                title="Version",
                border_style="cyan",
            )
        )
    else:
        print(f"\ntradebot  v{__version__}  (python {sys.version.split()[0]})")

    return 0


# ── JSON output wrapper ──


def _json_result(success: bool, data: Any, error: str | None = None) -> str:
    return json.dumps(
        {
            "success": success,
            "data": data,
            "error": error,
        },
        indent=2,
        default=str,
    )


# ── Subcommand dispatcher ──


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tradebot",
        description="Unified trading system — Deriv, Stockity, Bitget",
        epilog="All config via .env / environment variables (see tradebot.config.settings).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (for programmatic use)",
    )
    parser.add_argument(
        "-c",
        "--config",
        action="store_true",
        help="Show config file path and exit",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # test
    p_test = sub.add_parser("test", help="Connection + pattern test")
    p_test.add_argument(
        "symbol", nargs="?", default=None, help=f"Trading symbol (default: {DEFAULT_SYMBOL})"
    )

    # trade
    p_trade = sub.add_parser("trade", help="One live trade cycle")
    p_trade.add_argument(
        "symbol", nargs="?", default=None, help=f"Trading symbol (default: {DEFAULT_SYMBOL})"
    )

    # stream
    p_stream = sub.add_parser("stream", help="Live tick stream (30s)")
    p_stream.add_argument(
        "symbol", nargs="?", default=None, help=f"Trading symbol (default: {DEFAULT_SYMBOL})"
    )
    p_stream.add_argument(
        "-d", "--duration", type=int, default=30, help="Stream duration in seconds (default: 30)"
    )

    # backtest
    p_bt = sub.add_parser("backtest", help="Tick-by-tick historical backtest")
    p_bt.add_argument(
        "symbol", nargs="?", default=None, help=f"Trading symbol (default: {DEFAULT_SYMBOL})"
    )
    p_bt.add_argument(
        "pattern",
        nargs="?",
        default="Momen",
        choices=["Momen", "Adjacency", "Streak"],
        help="Pattern type (default: Momen)",
    )
    p_bt.add_argument(
        "count", nargs="?", type=int, default=500, help="Number of ticks to fetch (default: 500)"
    )

    # bridge
    p_br = sub.add_parser("bridge", help="HTTP signal bridge server")
    p_br.add_argument(
        "port",
        nargs="?",
        type=int,
        default=settings.BRIDGE_PORT,
        help=f"HTTP port (default: {settings.BRIDGE_PORT})",
    )

    # signals
    sub.add_parser("signals", help="Show latest market signal")

    # health — NEW
    sub.add_parser("health", help="Run HealthService checks")

    # monitor — NEW
    p_mon = sub.add_parser("monitor", help="Start HealthProbe HTTP server")
    p_mon.add_argument(
        "-p",
        "--port",
        type=int,
        default=settings.MONITORING_PROMETHEUS_PORT,
        help=f"HTTP port (default: {settings.MONITORING_PROMETHEUS_PORT})",
    )

    # analytics — NEW
    sub.add_parser("analytics", help="Daily mapping + session levels report")

    # bot — NEW
    p_bot = sub.add_parser("bot", help="Manage a trading bot (start/stop)")
    p_bot.add_argument("action", choices=["start", "stop"], help="Action to perform")
    p_bot.add_argument(
        "name", choices=list(_KNOWN_BOTS), help="Bot name (vilona, subscription, stockity)"
    )

    # config — NEW
    p_cfg = sub.add_parser("config", help="Show sanitised configuration")
    p_cfg.add_argument("-p", "--path", action="store_true", help="Show config file path only")

    # version — NEW
    sub.add_parser("version", help="Show tradebot version")

    # blast
    p_blast = sub.add_parser("blast", help="Marketing blast (weekly, flash, freetier, referral)")
    p_blast.add_argument(
        "--type",
        choices=["weekly", "flash", "freetier", "referral"],
        required=True,
        help="Blast type",
    )
    p_blast.add_argument("--dry-run", action="store_true", help="Print only, don't send")

    # learn
    p_learn = sub.add_parser("learn", help="Run autonomous learning pipeline")
    p_learn.add_argument("--lookback", type=int, default=14, help="Lookback days (default: 14)")

    # broadcast
    p_brc = sub.add_parser("broadcast", help="Scheduled Telegram broadcasts")
    p_brc.add_argument(
        "--type",
        choices=["levels", "ta", "btc-chart", "winrate"],
        required=True,
        help="Broadcast type",
    )
    p_brc.add_argument("--dry-run", action="store_true", help="Print only, don't send")

    # maintenance
    p_maint = sub.add_parser("maintenance", help="Maintenance utilities")
    p_maint.add_argument("--action", choices=["backup"], required=True, help="Action to perform")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — called by ``tradebot`` console script (pyproject.toml)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    level = "DEBUG" if args.verbose else "INFO"
    setup_logging(level=level, log_format="console")

    if getattr(args, "config", False) or (
        hasattr(args, "command") and args.command == "config" and getattr(args, "path", False)
    ):
        return cmd_config(show_path=True)

    command = args.command.lower()

    json_output = getattr(args, "json", False)
    if json_output and command in ("health", "version", "config", "signals", "analytics"):
        return _json_mode(command, args)

    symbol = _resolve_symbol(getattr(args, "symbol", None))

    if command == "test":
        return asyncio.run(cmd_test(symbol))
    elif command == "trade":
        return asyncio.run(cmd_trade(symbol))
    elif command == "stream":
        duration = getattr(args, "duration", 30)
        return asyncio.run(cmd_stream(symbol, duration))
    elif command == "backtest":
        return asyncio.run(cmd_backtest(symbol, args.pattern, args.count))
    elif command == "bridge":
        return cmd_bridge(args.port)
    elif command == "signals":
        return asyncio.run(cmd_signals())
    elif command == "health":
        return asyncio.run(cmd_health())
    elif command == "monitor":
        return cmd_monitor()
    elif command == "analytics":
        return asyncio.run(cmd_analytics())
    elif command == "bot":
        return cmd_bot(args.action, args.name)
    elif command == "config":
        return cmd_config(show_path=getattr(args, "path", False))
    elif command == "version":
        return cmd_version()
    elif command == "blast":
        from tradebot.services.marketing_service import MarketingService

        asyncio.run(MarketingService().execute_blast(args.type, getattr(args, "dry_run", False)))
        return 0
    elif command == "learn":
        import json
        from pathlib import Path

        from tradebot.analytics.learning import format_learning_report, run_learning_pipeline

        db_path = str(Path(settings.DATA_DIR) / "vilona_tradefx" / "members.db")
        res = run_learning_pipeline(db_path, args.lookback)
        print(format_learning_report(res))
        print("\nSuggested Weights:", json.dumps(res.get("suggested_weights"), indent=2))
        return 0
    elif command == "broadcast":
        from tradebot.services.broadcast_service import BroadcastService

        svc = BroadcastService()
        if args.type == "levels":
            asyncio.run(svc.broadcast_levels(getattr(args, "dry_run", False)))
        elif args.type == "ta":
            asyncio.run(svc.broadcast_tech_analysis(getattr(args, "dry_run", False)))
        elif args.type == "btc-chart":
            asyncio.run(svc.broadcast_btc_chart(getattr(args, "dry_run", False)))
        elif args.type == "winrate":
            asyncio.run(svc.broadcast_weekly_winrate(getattr(args, "dry_run", False)))
        return 0
    elif command == "maintenance":
        if args.action == "backup":
            from tradebot.services.backup_service import execute_backup

            asyncio.run(execute_backup())
        return 0

    else:
        parser.print_help()
        return 1


def _json_mode(command: str, args: argparse.Namespace) -> int:
    """Run a command in JSON-only output mode and print JSON to stdout."""
    if command == "health":

        async def _h():
            h = HealthService()
            r = await h.run_all()
            print(_json_result(r.ok, r.to_dict()))
            return 0

        return asyncio.run(_h())

    elif command == "version":
        print(
            _json_result(
                True,
                {
                    "version": __version__,
                    "python": sys.version.split()[0],
                    "rich": _HAS_RICH,
                },
            )
        )
        return 0

    elif command == "config":
        SECRET_KEYS = {"COOKIE", "AUTH", "TOKEN"}  # noqa: N806
        safe: dict[str, Any] = {}
        for field_name in settings.model_fields:
            value = getattr(settings, field_name)
            if any(s in field_name.upper() for s in SECRET_KEYS):
                value = "***" if value else ""
            elif isinstance(value, str) and len(value) > 40:
                value = value[:20] + "..."
            safe[field_name] = value
        print(_json_result(True, safe))
        return 0

    elif command == "signals":
        print(_json_result(True, {"message": "Use non-JSON mode for signals output"}))
        return 0
    elif command == "analytics":
        print(_json_result(True, {"message": "Use non-JSON mode for analytics output"}))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
