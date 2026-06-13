"""
Pillar 3: Autonomous Non-Stop Worker — 24/7 Daemon with Heartbeat.

The central production worker that integrates all 4 pillars:
  1. PhantomSync — pushes live status to dashboard
  2. Resilience — wraps all external calls with exponential backoff
  3. DataGate — validates OHLCV, enforces SL/TP clamp, killzone routing
  4. Heartbeat — periodic health ping to prevent silent death

Runs as a systemd service. Never stops. Never asks.

Architecture:
    ┌────────────────────────────────────────────────────────┐
    │              AutonomousWorker.run()                     │
    │  while True:                                           │
    │    1. Gate Check (killzone, OHLCV, circuit breaker)    │
    │    2. Fetch price + OHLCV (resilient)                  │
    │    3. Mechanical signal detection                       │
    │    4. AI consensus (resilient)                          │
    │    5. Quality gate + clamp                              │
    │    6. Post to bridge + channel                          │
    │    7. Sync dashboard status                             │
    │    8. Heartbeat pulse                                   │
    └────────────────────────────────────────────────────────┘

Usage:
    from tradebot.engines.autonomous_worker import AutonomousWorker

    worker = AutonomousWorker(
        assets=[("gold", "XAUUSD", "GC=F", True), ("btc", "BTCUSD", "BTC-USD", False)],
    )
    worker.run()  # blocks forever
"""

import time
import threading
import logging
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("autonomous")

# Add project root for importing existing modules
PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# ── Lazy imports (avoid triggering heavy tradebot __init__) ──
_resilience_mod = None
_data_gate_mod = None
_phantomfx_mod = None


def _get_resilience():
    global _resilience_mod
    if _resilience_mod is None:
        import importlib.util
        eng_dir = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "tradebot.engines.resilience", eng_dir / "resilience.py")
        _resilience_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_resilience_mod)
    return _resilience_mod


def _get_data_gate():
    global _data_gate_mod
    if _data_gate_mod is None:
        import importlib.util
        eng_dir = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "tradebot.engines.data_gate", eng_dir / "data_gate.py")
        _data_gate_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_data_gate_mod)
    return _data_gate_mod


def _get_phantomfx():
    global _phantomfx_mod
    if _phantomfx_mod is None:
        import importlib.util
        eng_dir = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "tradebot.engines.phantomfx_sync", eng_dir / "phantomfx_sync.py")
        _phantomfx_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_phantomfx_mod)
    return _phantomfx_mod

WIB = timezone(timedelta(hours=7))
HEARTBEAT_PATH = PROJECT_DIR / "data" / "vilona_tradefx" / "heartbeat.json"
HEARTBEAT_INTERVAL = 60  # seconds between heartbeats (Railway-ready: 1/min)
WEBHOOK_INTERVAL = 120   # seconds between full dashboard webhook pushes

# Railway / cloud detection
ON_RAILWAY = bool(os.environ.get("RAILWAY_SERVICE_ID", ""))


class AutonomousWorker:
    """24/7 autonomous trading worker with 4-pillar integration."""

    def __init__(self, assets=None,
                 heartbeat_interval: int = HEARTBEAT_INTERVAL):
        """
        Args:
            assets: List of (pair_key, display, yahoo_sym, is_forex) tuples.
            heartbeat_interval: Seconds between heartbeat writes.
        """
        self.assets = assets or [
            ("gold", "XAUUSD", "GC=F", True),
            ("btc", "BTCUSD", "BTC-USD", False),
            ("oil", "USOIL", "CL=F", True),
        ]
        self.heartbeat_interval = heartbeat_interval
        self.gate = _get_data_gate().GATE
        self.sync = _get_phantomfx().SYNC
        self._running = True
        self._last_heartbeat = 0.0
        self._asset_idx = 0

        # Ensure heartbeat directory exists
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Heartbeat ──

    def _pulse(self, cycle_info: dict | None = None):
        """Write heartbeat file. Dashboard reads this to confirm liveness."""
        now = time.time()
        if now - self._last_heartbeat < self.heartbeat_interval:
            return
        self._last_heartbeat = now

        hb = {
            "timestamp": datetime.now(WIB).isoformat(),
            "unix": now,
            "cycles": self.sync.total_cycles,
            "signals": self.sync.total_signals,
            "errors": self.sync.total_errors,
            "status": self.sync.current_status,
            "uptime_seconds": int(now - self.sync.start_time),
            "resilience": _get_resilience().REPORT.snapshot(),
            "gate": self.gate.snapshot(),
        }
        if cycle_info:
            hb["cycle"] = cycle_info

        try:
            tmp = HEARTBEAT_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(hb, indent=2, ensure_ascii=False))
            tmp.rename(HEARTBEAT_PATH)
        except Exception as e:
            LOG.warning("Silent exception caught: %s", e)

        # Fire-and-forget webhook heartbeat
        self.sync.push_heartbeat()

    # ── Price Fetch (resilient) ──

    def _fetch_price(self, pair: str) -> Optional[float]:
        """Fetch current price with exponential backoff.
        Primary: RapidAPI real-time finance. Fallback: gold-api.com / yfinance."""
        pair_lower = pair.lower().strip()
        pair_upper = pair.upper().strip()

        # ── XAUUSD ──
        if pair_lower in ("gold", "xauusd"):
            import urllib.request as _ur

            def _get():
                # Primary: RapidAPI
                try:
                    from tradebot.signals.rapid_finance import get_xauusd
                    price = get_xauusd()
                    if price and 2000 < price < 6000:
                        return price
                except Exception as e:
                    LOG.warning("Silent exception caught: %s", e)
                # Fallback: gold-api.com
                try:
                    resp = _ur.urlopen("https://api.gold-api.com/price/XAU", timeout=5)
                    data = json.loads(resp.read())
                    if data.get("price"):
                        return float(data["price"])
                except Exception as e:
                    LOG.warning("Silent exception caught: %s", e)
                # Tertiary: MT5 bridge
                try:
                    resp = _ur.urlopen("http://localhost:8765/current_price", timeout=3)
                    if resp.status == 200:
                        data = json.loads(resp.read())
                        price = data.get("price") or data.get("bid")
                        if price and 2000 < float(price) < 6000:
                            return float(price)
                except Exception as e:
                    LOG.warning("Silent exception caught: %s", e)
                return None
            return _get_resilience().resilient_call(_get, max_retries=3, base_delay=2.0)

        # ── Other pairs ──
        def _get():
            # Primary: RapidAPI
            try:
                from tradebot.signals.rapid_finance import fetch_prices
                prices = fetch_prices([pair_upper])
                if prices.get(pair_upper):
                    return prices[pair_upper]
            except Exception as e:
                LOG.warning("Silent exception caught: %s", e)
            # Fallback: yfinance
            try:
                import yfinance as yf
                yahoo_map = {"BTC": "BTC-USD", "BTCUSD": "BTC-USD", "oil": "CL=F", "USOIL": "CL=F"}
                sym = yahoo_map.get(pair, yahoo_map.get(pair_lower, pair))
                ticker = yf.Ticker(sym)
                data = ticker.history(period="1d", interval="5m")
                if not data.empty:
                    return float(data["Close"].iloc[-1])
            except Exception as e:
                LOG.warning("Silent exception caught: %s", e)
            return None

        return _get_resilience().resilient_call(_get, max_retries=3, base_delay=2.0)

    # ── OHLCV Fetch (resilient) ──

    def _fetch_ohlcv(self, pair: str, interval: str = "15m",
                     count: int = 100) -> list:
        """Fetch OHLCV bars with exponential backoff. Returns [] on failure."""

        pair_lower = pair.lower().strip()

        def _get():
            # ── XAUUSD: use spot symbol, not GC=F futures ──
            try:
                import yfinance as yf
                yahoo_map = {
                    "gold": "XAUUSD=X", "btc": "BTC-USD", "oil": "CL=F",
                    "XAUUSD": "XAUUSD=X", "BTCUSD": "BTC-USD", "USOIL": "CL=F",
                }
                sym = yahoo_map.get(pair, yahoo_map.get(pair.lower(), pair))
                ticker = yf.Ticker(sym)
                data = ticker.history(period="5d", interval=interval)
                if data.empty:
                    return []
                bars = []
                for idx, row in data.iterrows():
                    bars.append({
                        "timestamp": idx.isoformat(),
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": float(row.get("Volume", 0)),
                    })
                return bars[-count:] if len(bars) > count else bars
            except Exception:
                return []

        return _get_resilience().resilient_call(_get, max_retries=3, base_delay=3.0)

    # ── Weekend check ──

    @staticmethod
    def _is_weekend() -> bool:
        now = datetime.now(WIB)
        return now.weekday() >= 5  # Saturday=5, Sunday=6

    @staticmethod
    def _is_crypto(pair: str) -> bool:
        return pair.upper() in ("BTCUSD", "ETHUSD", "BTC/USD", "ETH/USD")

    # ── Main Loop ──

    def run(self):
        """Start the 24/7 autonomous loop. BLOCKS FOREVER."""
        log.info("🚀 AutonomousWorker starting with %d assets", len(self.assets))
        log.info("   Assets: %s", ", ".join(d for _, d, _, _ in self.assets))
        self.sync.push_status("starting", {"detail": "Worker initializing"})

        self._consecutive_failures = 0

        while self._running:
            cycle_start = time.time()
            self._consecutive_failures = 0  # reset on successful cycle entry
            try:
                now = datetime.now(WIB)
                hour = now.hour
                today_str = now.strftime("%Y%m%d")
                is_weekend = self._is_weekend()

                # ── Rotate assets ──
                pair_key, disp, yahoo_sym, is_forex = self.assets[
                    self._asset_idx % len(self.assets)
                ]
                self._asset_idx += 1

                # ── 1. GATE: weekend crypto-only mode ──
                if is_weekend and not self._is_crypto(disp):
                    self.sync.push_status("idle", {"pair": disp, "detail": "Weekend — waiting for crypto slot"})
                    time.sleep(60)
                    continue

                # ── 2. GATE: killzone check ──
                DGR = _get_data_gate().DataGateResult
                kz_result = self.gate.pass_killzone(disp, hour, is_weekend)
                if kz_result != DGR.PASS:
                    self.sync.push_status("idle", {"pair": disp, "detail": kz_result.value})
                    self._pulse({"state": "skip_killzone", "pair": disp})
                    time.sleep(60)
                    continue

                # ── 3. GATE: circuit breaker ──
                cb_result = self.gate.check_circuit(today_str, 0)  # losses from tracker
                if cb_result != DGR.PASS:
                    self.sync.push_status("blocked", {"pair": disp, "detail": "Circuit breaker active"})
                    self._pulse({"state": "circuit_breaker"})
                    time.sleep(600)
                    continue

                # ── 4. Fetch price (resilient) ──
                self.sync.push_status("fetching_price", {"pair": disp})
                price = self._fetch_price(pair_key)
                if not price:
                    self.sync.push_error("price_fetch", f"No price for {disp}")
                    self._pulse({"state": "no_price", "pair": disp})
                    time.sleep(30)
                    continue

                self.sync.price_cache[disp] = price

                # ── 5. Fetch OHLCV (resilient) ──
                self.sync.push_status("fetching_ohlcv", {"pair": disp})
                bars = self._fetch_ohlcv(pair_key, "15m", 100)

                # ── 6. GATE: OHLCV validation ──
                if self.gate.validate_ohlcv(bars) != DGR.PASS:
                    self.sync.push_error("ohlcv", f"Corrupt data for {disp}")
                    self._pulse({"state": "ohlcv_corrupt", "pair": disp})
                    time.sleep(300)  # wait for next candle
                    continue

                # ── 7. Mechanical signal detection ──
                if is_forex:
                    self.sync.push_status("analyzing", {"pair": disp, "detail": "Mechanical scan M1/M15..."})
                    mech_sig = self._detect_mechanical(disp, price, bars)
                    if mech_sig:
                        clamped = self.gate.clamp_sltp(
                            mech_sig["action"], price,
                            mech_sig.get("sl", 0), mech_sig.get("tp", 0),
                            disp,
                        )
                        if clamped:
                            self._publish_signal(clamped, price, disp, "mechanical")
                            self._pulse({"state": "signal_posted", "pair": disp, "engine": "mechanical"})
                            time.sleep(180)  # cooldown
                            continue

                # ── 8. AI consensus would go here (delegated to handler) ──
                # For autonomous mode: just log we're healthy
                self.sync.push_health()
                _get_resilience().REPORT.record_success()

                # Periodic full dashboard webhook push
                if cycle_start - getattr(self, '_last_webhook', 0) > WEBHOOK_INTERVAL:
                    self.sync.push_webhook()
                    self._last_webhook = cycle_start

                # ── 9. Heartbeat ──
                self._pulse({"state": "healthy", "pair": disp, "price": price})

                # ── Cycle cooldown ──
                elapsed = time.time() - cycle_start
                sleep_time = max(30, 120 - elapsed)  # ~2 min per asset
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                log.info("Worker stopped by user")
                self._running = False
                break
            except Exception as e:
                log.exception("Worker cycle crashed — auto-recovering")
                self.sync.push_error("worker_cycle", str(e))
                _get_resilience().REPORT.record_failure(e)
                self._pulse({"state": "error", "error": str(e)})
                self._consecutive_failures += 1
                backoff = min(30 * (2 ** (self._consecutive_failures - 1)), 300)
                log.warning("Backing off for %ds (consecutive failures: %d)", backoff, self._consecutive_failures)
                time.sleep(backoff)

        log.info("AutonomousWorker stopped after %d cycles", self.sync.total_cycles)

    # ── Mechanical signal detection (delegates to handler) ──

    def _detect_mechanical(self, display: str, price: float,
                           bars: list) -> Optional[dict]:
        """Detect mechanical signals using SMC logic on M15 bars.

        Simplified inline version. Full version in vilona_tradefx_handler.py.
        """
        if len(bars) < 20:
            return None

        try:
            # Find recent swing high/low
            closes = [b["close"] for b in bars]
            highs = [b["high"] for b in bars]
            lows = [b["low"] for b in bars]

            # Simple liquidity sweep detection
            recent_high = max(highs[-20:-1])
            recent_low = min(lows[-20:-1])
            current = closes[-1]

            # Bullish sweep: price breaks above recent high then retraces
            if highs[-1] > recent_high and current < recent_high:
                return {
                    "action": "SELL",
                    "entry": price,
                    "sl": round(price + 35 * 0.10, 2),  # 35 pip for XAUUSD
                    "tp": round(price - 60 * 0.10, 2),
                    "reason": "Liquidity sweep above resistance",
                    "confidence": 70,
                }

            # Bearish sweep: price breaks below recent low then recovers
            if lows[-1] < recent_low and current > recent_low:
                return {
                    "action": "BUY",
                    "entry": price,
                    "sl": round(price - 35 * 0.10, 2),
                    "tp": round(price + 60 * 0.10, 2),
                    "reason": "Liquidity sweep below support",
                    "confidence": 70,
                }
        except Exception as e:
            LOG.warning("Silent exception caught: %s", e)

        return None

    # ── Signal publishing ──

    def _publish_signal(self, clamped: dict, price: float,
                        display: str, source: str):
        """Publish signal to bridge, sync, and channel."""
        sig_id = f"auto_{int(time.time()*1000)}"
        signal = {
            "signal_id": sig_id,
            "symbol": display,
            "action": clamped["action"],
            "entry": clamped["entry"],
            "sl": clamped["sl"],
            "tp": clamped["tp"],
            "tp1": clamped.get("tp1", 0),
            "tp2": clamped.get("tp2", 0),
            "confidence": 70,
            "rr_ratio": round(abs(clamped["tp"] - clamped["entry"]) /
                            max(abs(clamped["sl"] - clamped["entry"]), 0.001), 2),
            "comment": f"Autonomous {source}",
            "source": source,
            "timestamp": datetime.now(WIB).isoformat(),
        }

        # Push to bridge
        try:
            import urllib.request
            bridge_key = os.environ.get("BRIDGE_MASTER_KEY", "")
            if not bridge_key:
                log.error("BRIDGE_MASTER_KEY is not set — cannot push signal to bridge")
                return
            url = f"http://localhost:8765/signal?api_key={bridge_key}"
            data = json.dumps(signal).encode()
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
            log.info("Signal pushed to bridge: %s %s@%.2f", sig_id, signal["action"], signal["entry"])
        except Exception as e:
            log.error("Bridge push failed: %s", e)

        # Sync to dashboard
        self.sync.push_signal(signal)
        self.sync.push_status("signal_generated", {
            "pair": display, "detail": f"{signal['action']}@{signal['entry']}"
        })

    # ── Stop ──

    def stop(self):
        """Graceful shutdown."""
        self._running = False
        self.sync.push_status("stopped")
        log.info("Worker stop requested")
