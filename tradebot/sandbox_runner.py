"""
sandbox_runner.py — Vilona Forward-Test Sandbox

Paper-trades the Harmonic Engine + MTF Consensus Gate pipeline
against live XAUUSD data with realistic spread, slippage, and
position sizing. Logs every signal, verdict, and P&L for analysis.

Usage:
    python -m tradebot.sandbox                    # default: XAUUSD, $10k balance
    python -m tradebot.sandbox --symbol BTCUSD    # crypto mode (no killzone gate)
    python -m tradebot.sandbox --balance 50000    # custom starting balance
    python -m tradebot.sandbox --interval 60      # poll every 60s

Output:
    sandbox/run_<timestamp>.jsonl   — every signal + verdict + trade
    sandbox/summary_<timestamp>.md  — end-of-session performance report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from tradebot.engines.harmonic import HarmonicEngine
from tradebot.engines.mtf_consensus import (
    GateState,
    MacroBias,
    MacroState,
    MesoState,
    MicroTrigger,
    MTFConsensusGate,
    TriggerType,
    meso_from_signal,
)
from tradebot.engines.registry import Registry
from tradebot.logging import get_logger
from tradebot.models import Signal, Tick
from tradebot.pipeline.quality_gate import ASSET_CONFIG, DEFAULT_CONFIG
from tradebot.signals.market import MarketAggregator

# Initialize logging for sandbox output
from tradebot.logging import setup_logging
setup_logging(level="INFO", log_format="console")

LOG = get_logger(__name__)

# ── Paper Trading Config ───────────────────────────────────────────────

SANDBOX_DIR = Path(__file__).parent.parent / "sandbox"


@dataclass
class SandboxConfig:
    symbol: str = "XAUUSD"
    starting_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0  # 1% risk per trade
    max_open_trades: int = 3
    spread_pips: float = 0.0  # auto from ASSET_CONFIG
    slippage_pips: float = 0.0  # auto from ASSET_CONFIG
    poll_interval_s: float = 30.0  # seconds between scans
    harmonic_fractal_period: int = 5
    hunt_ttl_s: float = 3600.0  # 1 hour max hunt time
    macro_required: bool = False
    min_trigger_confidence: float = 0.5
    is_crypto: bool = False  # bypass killzone for crypto


@dataclass
class Position:
    symbol: str
    direction: str  # "BULLISH" → BUY, "BEARISH" → SELL
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    size_lots: float
    risk_amount: float
    pattern: str
    confidence: float
    opened_at: float
    closed_at: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    result: str = "OPEN"  # OPEN, WIN_TP1, WIN_TP2, LOSS, EXPIRED


@dataclass
class SandboxMetrics:
    total_signals: int = 0
    ahz_active: int = 0
    hunts_started: int = 0
    hunts_confirmed: int = 0
    hunts_rejected: int = 0
    hunts_expired: int = 0
    trades_opened: int = 0
    trades_closed: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_balance: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    def update_win_rate(self):
        if self.trades_closed > 0:
            self.win_rate = self.wins / self.trades_closed

    def update_profit_factor(self, wins_pnl: float, losses_pnl: float):
        if losses_pnl > 0:
            self.profit_factor = wins_pnl / losses_pnl
        elif wins_pnl > 0:
            self.profit_factor = float("inf")


# ── Helpers ────────────────────────────────────────────────────────────


def _get_asset_cfg(symbol: str) -> dict:
    return ASSET_CONFIG.get(symbol, DEFAULT_CONFIG)


def _pip_value(symbol: str) -> float:
    return _get_asset_cfg(symbol).get("pip_value", 0.01)


def _entry_slip(symbol: str) -> float:
    return _get_asset_cfg(symbol).get("entry_slip", 0.5)


def _pips_to_price(pips: float, symbol: str) -> float:
    return pips * _pip_value(symbol)


def _price_to_pips(price_diff: float, symbol: str) -> float:
    pv = _pip_value(symbol)
    return abs(price_diff) / pv if pv > 0 else 0.0


def _is_killzone() -> bool:
    """Check if current time is within London or New York killzone."""
    from datetime import timezone as tz
    now = datetime.now(tz.utc)
    hour = now.hour
    # London: 07:00–10:00 UTC, New York: 12:00–15:00 UTC
    return (7 <= hour <= 10) or (12 <= hour <= 15)


def _format_price(price: float, symbol: str) -> str:
    if symbol in ("BTCUSD",):
        return f"{price:.1f}"
    if symbol in ("XAUUSD", "USOIL"):
        return f"{price:.2f}"
    return f"{price:.5f}"


# ── Sandbox Runner ─────────────────────────────────────────────────────


class SandboxRunner:
    """
    Forward-test sandbox for the Harmonic + MTF Consensus pipeline.

    Runs in a loop:
      1. Fetch latest M15 data via MarketAggregator
      2. Run Harmonic Engine → AHZ detection
      3. Feed AHZ to MTF Consensus Gate
      4. Check micro triggers (simulated from price action)
      5. Manage open positions (TP/SL hits)
      6. Log everything
    """

    def __init__(self, config: SandboxConfig):
        self.cfg = config
        self.balance = config.starting_balance
        self.peak_balance = config.starting_balance
        self.metrics = SandboxMetrics()
        self.open_positions: list[Position] = []
        self.all_positions: list[Position] = []
        self.verdicts: list[dict] = []

        # Engines
        self.harmonic = HarmonicEngine(
            fractal_period=config.harmonic_fractal_period
        )
        self.gate = MTFConsensusGate(
            hunt_ttl_seconds=config.hunt_ttl_s,
            require_macro=config.macro_required,
            min_trigger_confidence=config.min_trigger_confidence,
        )
        self.aggregator = MarketAggregator()

        # Logging
        SANDBOX_DIR.mkdir(exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self.log_path = SANDBOX_DIR / f"run_{ts}.jsonl"
        self.report_path = SANDBOX_DIR / f"summary_{ts}.md"

        # Killzone bypass for crypto
        self.cfg.is_crypto = config.symbol in ("BTCUSD", "ETHUSD")

    async def _fetch_ticks(self, count: int = 200) -> list[Tick]:
        """Fetch recent M15 OHLCV → convert to ticks for Harmonic Engine.

        Uses Binance public API for crypto (instant, no auth) and Yahoo for
        forex/metals. Binance supports up to 1000 candles per call.
        """
        import urllib.request
        import json as jsonlib
        from datetime import datetime, timezone as tz, timedelta

        # ── Crypto via Binance public REST (fast, no auth) ────────────
        if self.cfg.symbol in ("BTCUSD", "ETHUSD"):
            # Remap to Binance symbol
            bn_sym = self.cfg.symbol.replace("USD", "USDT")
            # 15m = 15m interval
            end_time = int(datetime.now(tz.utc).timestamp() * 1000)
            start_time = end_time - count * 15 * 60 * 1000
            url = (
                f"https://api.binance.com/api/v3/klines"
                f"?symbol={bn_sym}&interval=15m"
                f"&startTime={start_time}&limit={count}"
            )
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = jsonlib.loads(resp.read().decode())

                ticks: list[Tick] = []
                for bar in raw:
                    ts = int(bar[0]) // 1000  # ms → s
                    close = float(bar[4])
                    ticks.append(Tick(
                        symbol=self.cfg.symbol,
                        price=close,
                        epoch=ts,
                    ))
                LOG.info("Fetched %d M15 bars for %s (Binance)", len(ticks), self.cfg.symbol)
                return ticks[-count:]
            except Exception as e:
                LOG.error("Binance fetch failed for %s: %s", self.cfg.symbol, e)
                return []

        # ── Forex/Metals via Yahoo ────────────────────────────────────
        import yfinance as yf

        YAHOO_MAP = {
            "XAUUSD": "GC=F",
            "USOIL": "CL=F",
            "GBPUSD": "GBPUSD=X",
            "EURUSD": "EURUSD=X",
            "USDJPY": "USDJPY=X",
        }
        yahoo_sym = YAHOO_MAP.get(self.cfg.symbol, self.cfg.symbol)

        try:
            df = yf.download(
                yahoo_sym,
                interval="15m",
                period="60d",
                progress=False,
            )
            if df is None or df.empty:
                LOG.warning("No data from Yahoo for %s (%s)", self.cfg.symbol, yahoo_sym)
                return []

            ohlcv_bars = []
            for _, row in df.iterrows():
                ohlcv_bars.append({
                    "timestamp": 0,
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                })

            ohlcv_bars = ohlcv_bars[-count:]
            LOG.info("Fetched %d M15 bars for %s (Yahoo: %s)", len(ohlcv_bars), self.cfg.symbol, yahoo_sym)

            return [
                Tick(symbol=self.cfg.symbol, price=bar["close"], epoch=bar["timestamp"])
                for bar in ohlcv_bars
            ]
        except Exception as e:
            LOG.error("Failed to fetch data for %s: %s", self.cfg.symbol, e)
            return []

    def _simulate_micro_trigger(
        self, price: float, meso: MesoState, prev_price: float
    ) -> MicroTrigger | None:
        """
        Simulate micro-timeframe trigger detection from price action.

        In production this would query SMC/FVG engines on M1/M5.
        For sandbox, we detect simple price-action triggers:
          - Strong directional move inside AHZ → ChoCh simulation
          - Price touches AHZ boundary → OB tap simulation
        """
        if not meso.ahz_active:
            return None

        # Check if price is inside AHZ
        in_ahz = meso.ahz_lower <= price <= meso.ahz_upper
        if not in_ahz:
            return None

        price_change = price - prev_price
        pip_change = _price_to_pips(price_change, self.cfg.symbol)

        # Trigger 1: Strong move inside AHZ (ChoCh simulation)
        if meso.direction == "BULLISH" and pip_change > 2.0:
            return MicroTrigger(
                trigger_type=TriggerType.SMC_CHOCH,
                symbol=self.cfg.symbol,
                price=price,
                direction="BULLISH",
                confidence=min(0.9, 0.5 + pip_change * 0.02),
                source_engine="sandbox_simulator",
                within_ahz=True,
                timeframe="M5",
            )
        elif meso.direction == "BEARISH" and pip_change < -2.0:
            return MicroTrigger(
                trigger_type=TriggerType.SMC_CHOCH,
                symbol=self.cfg.symbol,
                price=price,
                direction="BEARISH",
                confidence=min(0.9, 0.5 + abs(pip_change) * 0.02),
                source_engine="sandbox_simulator",
                within_ahz=True,
                timeframe="M5",
            )

        # Trigger 2: Price touches AHZ boundary (OB tap)
        prZ_height = meso.ahz_upper - meso.ahz_lower
        near_lower = abs(price - meso.ahz_lower) < prZ_height * 0.1
        near_upper = abs(price - meso.ahz_upper) < prZ_height * 0.1

        if near_lower and meso.direction == "BULLISH":
            return MicroTrigger(
                trigger_type=TriggerType.SMC_OB_TAP,
                symbol=self.cfg.symbol,
                price=price,
                direction="BULLISH",
                confidence=0.6,
                source_engine="sandbox_simulator",
                within_ahz=True,
                timeframe="M5",
            )
        elif near_upper and meso.direction == "BEARISH":
            return MicroTrigger(
                trigger_type=TriggerType.SMC_OB_TAP,
                symbol=self.cfg.symbol,
                price=price,
                direction="BEARISH",
                confidence=0.6,
                source_engine="sandbox_simulator",
                within_ahz=True,
                timeframe="M5",
            )

        return None

    def _calculate_position_size(self, sl_pips: float) -> float:
        """Calculate position size based on risk percentage."""
        risk_amount = self.balance * (self.cfg.risk_per_trade_pct / 100)
        pv = _pip_value(self.cfg.symbol)
        if sl_pips <= 0 or pv <= 0:
            return 0.01  # minimum
        # Standard lot = 100,000 units. Risk = lots * sl_pips * pip_value
        lots = risk_amount / (sl_pips * pv * 100_000)
        return max(0.01, round(lots, 2))

    def _open_position(self, verdict_signal: Signal, price: float) -> Position:
        """Open a paper position from a consensus verdict."""
        meta = verdict_signal.metadata
        sl = meta.get("sl", price)
        tp1 = meta.get("tp1", price)
        tp2 = meta.get("tp2", price)
        direction = verdict_signal.direction

        # Apply slippage
        slip = _pips_to_price(_entry_slip(self.cfg.symbol), self.cfg.symbol)
        if direction == "BULLISH":
            entry = price + slip  # worse fill for buy
        else:
            entry = price - slip  # worse fill for sell

        sl_pips = _price_to_pips(abs(entry - sl), self.cfg.symbol)
        size = self._calculate_position_size(sl_pips)
        risk = size * sl_pips * _pip_value(self.cfg.symbol) * 100_000

        pos = Position(
            symbol=self.cfg.symbol,
            direction=direction,
            entry_price=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            size_lots=size,
            risk_amount=risk,
            pattern=meta.get("gate_reason", "unknown"),
            confidence=verdict_signal.confidence,
            opened_at=time.time(),
        )

        self.open_positions.append(pos)
        self.all_positions.append(pos)
        self.metrics.trades_opened += 1
        self.balance -= risk  # reserve margin

        LOG.info(
            "Sandbox OPEN: %s %s @ %s — lots=%.2f risk=$%.2f SL=%s TP1=%s TP2=%s",
            direction, self.cfg.symbol, _format_price(entry, self.cfg.symbol),
            size, risk,
            _format_price(sl, self.cfg.symbol),
            _format_price(tp1, self.cfg.symbol),
            _format_price(tp2, self.cfg.symbol),
        )

        return pos

    def _check_positions(self, current_price: float) -> list[Position]:
        """Check open positions for TP/SL hits. Returns closed positions."""
        closed = []
        for pos in list(self.open_positions):
            if pos.direction == "BULLISH":
                # TP1 hit
                if current_price >= pos.tp1 and pos.result == "OPEN":
                    pos.result = "WIN_TP1"
                    pos.exit_price = pos.tp1
                    # Partial: close 50% at TP1
                    pnl = (pos.tp1 - pos.entry_price) * pos.size_lots * 50_000 * _pip_value(self.cfg.symbol)
                    pos.pnl = pnl
                    self.balance += pnl + pos.risk_amount * 0.5
                    closed.append(pos)
                    self.open_positions.remove(pos)
                    break

                # TP2 hit
                if current_price >= pos.tp2 and pos.result == "OPEN":
                    pos.result = "WIN_TP2"
                    pos.exit_price = pos.tp2
                    pnl = (pos.tp2 - pos.entry_price) * pos.size_lots * 100_000 * _pip_value(self.cfg.symbol)
                    pos.pnl = pnl
                    self.balance += pnl + pos.risk_amount
                    closed.append(pos)
                    self.open_positions.remove(pos)
                    break

                # SL hit
                if current_price <= pos.sl:
                    pos.result = "LOSS"
                    pos.exit_price = pos.sl
                    pos.pnl = -pos.risk_amount
                    self.balance += 0  # risk already deducted
                    closed.append(pos)
                    self.open_positions.remove(pos)
                    break

            elif pos.direction == "BEARISH":
                if current_price <= pos.tp1 and pos.result == "OPEN":
                    pos.result = "WIN_TP1"
                    pos.exit_price = pos.tp1
                    pnl = (pos.entry_price - pos.tp1) * pos.size_lots * 50_000 * _pip_value(self.cfg.symbol)
                    pos.pnl = pnl
                    self.balance += pnl + pos.risk_amount * 0.5
                    closed.append(pos)
                    self.open_positions.remove(pos)
                    break

                if current_price <= pos.tp2 and pos.result == "OPEN":
                    pos.result = "WIN_TP2"
                    pos.exit_price = pos.tp2
                    pnl = (pos.entry_price - pos.tp2) * pos.size_lots * 100_000 * _pip_value(self.cfg.symbol)
                    pos.pnl = pnl
                    self.balance += pnl + pos.risk_amount
                    closed.append(pos)
                    self.open_positions.remove(pos)
                    break

                if current_price >= pos.sl:
                    pos.result = "LOSS"
                    pos.exit_price = pos.sl
                    pos.pnl = -pos.risk_amount
                    closed.append(pos)
                    self.open_positions.remove(pos)
                    break

        for pos in closed:
            self.metrics.trades_closed += 1
            self.metrics.total_pnl += pos.pnl
            if pos.pnl > 0:
                self.metrics.wins += 1
            else:
                self.metrics.losses += 1
            self.metrics.update_win_rate()

            # Track drawdown
            if self.balance > self.peak_balance:
                self.peak_balance = self.balance
            dd = (self.peak_balance - self.balance) / self.peak_balance
            if dd > self.metrics.max_drawdown:
                self.metrics.max_drawdown = dd

            LOG.info(
                "Sandbox CLOSE: %s %s @ %s — result=%s pnl=$%.2f balance=$%.2f",
                pos.direction, pos.symbol,
                _format_price(pos.exit_price, pos.symbol),
                pos.result, pos.pnl, self.balance,
            )

        return closed

    def _log_event(self, event_type: str, data: dict):
        """Write event to JSONL log."""
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "type": event_type,
            "balance": round(self.balance, 2),
            **data,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _generate_report(self) -> str:
        """Generate end-of-session markdown report."""
        m = self.metrics
        wins_pnl = sum(p.pnl for p in self.all_positions if p.pnl > 0)
        losses_pnl = abs(sum(p.pnl for p in self.all_positions if p.pnl < 0))
        m.update_profit_factor(wins_pnl, losses_pnl)

        lines = [
            f"# Sandbox Report — {self.cfg.symbol}",
            f"**Date:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Parameters",
            f"- Starting Balance: ${self.cfg.starting_balance:,.2f}",
            f"- Risk Per Trade: {self.cfg.risk_per_trade_pct}%",
            f"- Max Open Trades: {self.cfg.max_open_trades}",
            f"- Hunt TTL: {self.cfg.hunt_ttl_s:.0f}s",
            f"- Poll Interval: {self.cfg.poll_interval_s:.0f}s",
            f"- Crypto Mode: {'Yes (24/7)' if self.cfg.is_crypto else 'No (Killzone gated)'}",
            "",
            "## Pipeline Metrics",
            f"- Signals Scanned: {m.total_signals}",
            f"- AHZ Active: {m.ahz_active}",
            f"- Hunts Started: {m.hunts_started}",
            f"- Hunts Confirmed: {m.hunts_confirmed}",
            f"- Hunts Rejected: {m.hunts_rejected}",
            f"- Hunts Expired: {m.hunts_expired}",
            "",
            "## Trading Performance",
            f"- Trades Opened: {m.trades_opened}",
            f"- Trades Closed: {m.trades_closed}",
            f"- Wins: {m.wins}",
            f"- Losses: {m.losses}",
            f"- Win Rate: {m.win_rate:.1%}",
            f"- Profit Factor: {m.profit_factor:.2f}" if m.profit_factor != float("inf") else "- Profit Factor: ∞",
            f"- Total P&L: ${m.total_pnl:,.2f}",
            f"- Final Balance: ${self.balance:,.2f}",
            f"- Return: {((self.balance - self.cfg.starting_balance) / self.cfg.starting_balance) * 100:+.2f}%",
            f"- Max Drawdown: {m.max_drawdown:.2%}",
            "",
            "## Trade Log",
            "| # | Dir | Entry | Exit | Result | P&L | Pattern |",
            "|---|-----|-------|------|--------|-----|---------|",
        ]

        for i, p in enumerate(self.all_positions, 1):
            lines.append(
                f"| {i} | {p.direction[:3]} | "
                f"{_format_price(p.entry_price, p.symbol)} | "
                f"{_format_price(p.exit_price, p.symbol) if p.exit_price else '—'} | "
                f"{p.result} | ${p.pnl:+.2f} | {p.pattern[:30]} |"
            )

        lines.extend(["", f"---", f"*Generated by Vilona Sandbox Runner*"])

        report = "\n".join(lines)
        with open(self.report_path, "w") as f:
            f.write(report)

        return report

    async def run(self):
        """Main sandbox loop."""
        LOG.info("=" * 60)
        LOG.info("VILONA SANDBOX — Starting Forward Test")
        LOG.info("Symbol: %s | Balance: $%s | Risk: %s%%",
                 self.cfg.symbol, f"{self.cfg.starting_balance:,.0f}",
                 self.cfg.risk_per_trade_pct)
        LOG.info("Crypto mode: %s | Killzone bypass: %s",
                 self.cfg.is_crypto, self.cfg.is_crypto)
        LOG.info("Log: %s", self.log_path)
        LOG.info("=" * 60)

        prev_price = 0.0
        cycle = 0

        try:
            while True:
                cycle += 1
                now = time.time()

                # Killzone check (skip for crypto)
                if not self.cfg.is_crypto and not _is_killzone():
                    LOG.debug("Outside killzone — skipping cycle %d", cycle)
                    await asyncio.sleep(self.cfg.poll_interval_s)
                    continue

                # 1. Fetch data
                ticks = await self._fetch_ticks(count=200)
                if not ticks:
                    LOG.warning("No data — retrying in %ds", self.cfg.poll_interval_s)
                    await asyncio.sleep(self.cfg.poll_interval_s)
                    continue

                current_price = ticks[-1].price
                self.metrics.total_signals += 1

                # 2. Check open positions
                closed = self._check_positions(current_price)
                for pos in closed:
                    self._log_event("trade_closed", {
                        "direction": pos.direction,
                        "entry": pos.entry_price,
                        "exit": pos.exit_price,
                        "result": pos.result,
                        "pnl": round(pos.pnl, 2),
                        "pattern": pos.pattern,
                    })

                # 3. Run Harmonic Engine on M15 ticks
                harmonic_signal = await self.harmonic.analyze(ticks)

                if harmonic_signal and harmonic_signal.metadata.get("AHZ_Active"):
                    self.metrics.ahz_active += 1
                    meso = meso_from_signal(harmonic_signal)

                    if meso is not None:
                        # 4. Feed to MTF Consensus Gate
                        verdict = self.gate.activate_hunt(meso=meso)

                        self._log_event("ahz_detected", {
                            "pattern": meso.pattern,
                            "direction": meso.direction,
                            "ahz_upper": meso.ahz_upper,
                            "ahz_lower": meso.ahz_lower,
                            "sl": meso.sl,
                            "tp1": meso.tp1,
                            "tp2": meso.tp2,
                            "confidence": meso.confidence,
                        })

                        if verdict.decision == GateState.HUNT_MODE:
                            self.metrics.hunts_started += 1
                        elif verdict.decision == GateState.REJECT:
                            self.metrics.hunts_rejected += 1

                # 5. Check micro triggers for active hunts
                for symbol, session in list(self.gate.active_sessions.items()):
                    if not session.is_active:
                        continue

                    trigger = self._simulate_micro_trigger(
                        current_price, session.meso, prev_price
                    )

                    if trigger:
                        verdict = self.gate.process_micro_trigger(
                            trigger, current_price
                        )

                        if verdict and verdict.decision == GateState.EXECUTE:
                            self.metrics.hunts_confirmed += 1

                            # Check if we can open a trade
                            if len(self.open_positions) < self.cfg.max_open_trades:
                                signal = verdict.to_signal()
                                if signal:
                                    self._open_position(signal, current_price)
                                    self._log_event("trade_opened", {
                                        "direction": signal.direction,
                                        "entry": _format_price(current_price, symbol),
                                        "sl": verdict.sl,
                                        "tp1": verdict.tp1,
                                        "tp2": verdict.tp2,
                                        "confidence": verdict.confidence,
                                        "trigger": trigger.trigger_type.value,
                                    })
                            else:
                                LOG.info("Max open trades reached — skipping")

                # 6. Check invalidation
                for symbol in list(self.gate.active_sessions.keys()):
                    inv = self.gate.check_invalidation(current_price, symbol)
                    if inv:
                        self.metrics.hunts_rejected += 1
                        self._log_event("ahz_invalidated", {
                            "symbol": symbol,
                            "sl_level": inv.sl,
                            "invalidation_price": current_price,
                        })

                # 7. Cleanup expired hunts
                expired = self.gate.cleanup_expired(now)
                for v in expired:
                    self.metrics.hunts_expired += 1
                    self._log_event("hunt_expired", {"symbol": v.symbol})

                # Status update every 10 cycles
                if cycle % 10 == 0:
                    LOG.info(
                        "[Cycle %d] Price=%s Balance=$%.2f "
                        "PnL=$%.2f WR=%.0f%% Open=%d Hunts=%d",
                        cycle,
                        _format_price(current_price, self.cfg.symbol),
                        self.balance,
                        self.metrics.total_pnl,
                        self.metrics.win_rate * 100,
                        len(self.open_positions),
                        self.gate.hunt_count,
                    )

                prev_price = current_price
                await asyncio.sleep(self.cfg.poll_interval_s)

        except KeyboardInterrupt:
            LOG.info("\nSandbox stopped by user")
        finally:
            # Close any remaining positions at market
            if self.open_positions and prev_price > 0:
                for pos in list(self.open_positions):
                    pos.result = "EXPIRED"
                    pos.exit_price = prev_price
                    if pos.direction == "BULLISH":
                        pos.pnl = (prev_price - pos.entry_price) * pos.size_lots * 100_000 * _pip_value(self.cfg.symbol)
                    else:
                        pos.pnl = (pos.entry_price - prev_price) * pos.size_lots * 100_000 * _pip_value(self.cfg.symbol)
                    self.balance += pos.pnl + pos.risk_amount
                    self.metrics.total_pnl += pos.pnl
                    self.open_positions.remove(pos)

            report = self._generate_report()
            print("\n" + report)
            LOG.info("Report saved: %s", self.report_path)


# ── CLI Entry Point ────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Vilona Sandbox Runner")
    parser.add_argument("--symbol", default="XAUUSD", help="Trading symbol")
    parser.add_argument("--balance", type=float, default=10_000, help="Starting balance")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk per trade (%%)")
    parser.add_argument("--interval", type=float, default=30, help="Poll interval (seconds)")
    parser.add_argument("--max-trades", type=int, default=3, help="Max open trades")
    parser.add_argument("--hunt-ttl", type=float, default=3600, help="Hunt TTL (seconds)")
    args = parser.parse_args()

    config = SandboxConfig(
        symbol=args.symbol.upper(),
        starting_balance=args.balance,
        risk_per_trade_pct=args.risk,
        poll_interval_s=args.interval,
        max_open_trades=args.max_trades,
        hunt_ttl_s=args.hunt_ttl,
    )

    runner = SandboxRunner(config)
    asyncio.run(runner.run())


if __name__ == "__main__":
    main()
