#!/usr/bin/env python3
"""
Deriv Signal Bridge — HTTP Server (no FastAPI)
================================================
Endpoints:
  GET /signal   → Latest Deriv signal JSON (symbol, direction, entry_price, confidence, timestamp)
  GET /status   → Strategy health info
  GET /balance  → Current Deriv account balance

Runs on port 8082. Uses DerivWSClient internally with async event loop
running in a background thread.
"""
import asyncio
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
from typing import Optional

# ── Path setup ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # scripts/deriv/
_PARENT = os.path.dirname(_SCRIPT_DIR)                     # scripts/
_GRANDPARENT = os.path.dirname(_PARENT)                    # 1ai-trade-bot/
for p in (_PARENT, _GRANDPARENT):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOG = logging.getLogger("deriv.bridge")

# ── Bridge Config ──
HOST = os.environ.get("DERIV_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("DERIV_BRIDGE_PORT", "8082"))

# ── PAT credentials (from env, fall back to __main__ defaults) ──
PAT_TOKEN = os.environ.get("DERIV_PAT_TOKEN",
    "pat_0f2c09ae7ef25d3970e5829982e77206bd53c761c57e153f53dd99f8e1d11bb2")
APP_ID = os.environ.get("DERIV_APP_ID", "33uQ6fU4eIRvJc6jkYeEa")
ACCOUNT_ID = os.environ.get("DERIV_ACCOUNT_ID", "DOT92925029")
DEFAULT_SYMBOL = os.environ.get("DERIV_SYMBOL", "R_75")

# ── Thread-safe shared state ──
_state = {
    "latest_signal": {},         # latest signal JSON
    "balance": None,             # latest balance float
    "last_balance_update": 0,    # epoch
    "connected": False,          # WS connection status
    "total_ticks": 0,            # tick counter
    "uptime": 0,                 # bridge start time
    "signals_generated": 0,      # counter
    "error": None,               # last error string
}
_state_lock = threading.Lock()
START_TIME = time.time()


# ── Signal Generator (async, runs in background thread) ──

class DerivSignalGenerator:
    """Manages DerivWSClient connection and generates trading signals.

    Runs in an asyncio event loop on a background thread.
    Updates shared _state dict with latest signal, balance, and health info.
    """

    def __init__(self, symbol: str = DEFAULT_SYMBOL):
        self.symbol = symbol
        self.client = None
        self._loop = None
        self._running = False

    async def start(self):
        """Connect to Deriv, subscribe to ticks + balance, start signal loop."""
        from deriv.client import DerivWSClient

        self._running = True
        self.client = DerivWSClient(
            pat_token=PAT_TOKEN,
            app_id=APP_ID,
            account_id=ACCOUNT_ID,
        )

        ok = await self.client.connect()
        if not ok:
            err_msg = "Failed to connect to Deriv WS"
            LOG.error("❌ %s", err_msg)
            with _state_lock:
                _state["connected"] = False
                _state["error"] = err_msg
            self._running = False
            return

        with _state_lock:
            _state["connected"] = True
            _state["error"] = None
            _state["uptime"] = time.time()
        LOG.info("✅ Deriv WS connected — starting signal loop")

        # Register handlers
        self.client.on("tick", self._on_tick)
        self.client.on("balance", self._on_balance)

        # Subscribe
        await self.client.subscribe_ticks(self.symbol)
        await self.client.subscribe_balance()

        # Initial balance fetch
        bal = await self.client.get_balance()
        if bal is not None:
            with _state_lock:
                _state["balance"] = bal
                _state["last_balance_update"] = time.time()
            LOG.info("💰 Initial balance: $%.2f", bal)

        # Periodic signal loop (re-analyze every ~5 ticks or every 15s)
        tick_buffer = deque(maxlen=150)
        last_signal_time = 0

        try:
            while self._running:
                # Read ticks as they arrive via the queue
                # We do a short sleep and check; the real work is in _on_tick handler
                await asyncio.sleep(3.0)

                # Periodically check connection health
                if not self.client.is_connected:
                    LOG.warning("⚠️ WS disconnected, attempting reconnect...")
                    with _state_lock:
                        _state["connected"] = False
                    try:
                        await self.client.reconnect()
                        if self.client.is_connected:
                            with _state_lock:
                                _state["connected"] = True
                                _state["error"] = None
                            LOG.info("✅ Reconnected")
                            await self.client.subscribe_ticks(self.symbol)
                            await self.client.subscribe_balance()
                    except Exception as e:
                        LOG.error("Reconnect failed: %s", e)
                        with _state_lock:
                            _state["error"] = str(e)

                # Fetch fresh ticks history for pattern analysis every ~15s
                now = time.time()
                if now - last_signal_time >= 15.0:
                    try:
                        await self._generate_signal()
                        last_signal_time = now
                    except Exception as e:
                        LOG.error("Signal generation error: %s", e)

                # Refresh balance every 30s
                if now - _state.get("last_balance_update", 0) >= 30.0:
                    try:
                        bal = await self.client.get_balance()
                        if bal is not None:
                            with _state_lock:
                                _state["balance"] = bal
                                _state["last_balance_update"] = time.time()
                    except Exception as e:
                        LOG.warning("Balance refresh failed: %s", e)

        except asyncio.CancelledError:
            pass
        finally:
            await self.client.disconnect()
            with _state_lock:
                _state["connected"] = False

    def stop(self):
        self._running = False

    async def _on_tick(self, tick):
        """Handle incoming tick — update tick buffer."""
        with _state_lock:
            _state["total_ticks"] += 1

    async def _on_balance(self, bal_data):
        """Handle live balance update."""
        bal = bal_data.get("balance") if isinstance(bal_data, dict) else bal_data
        if bal is not None:
            try:
                bal_float = float(bal)
                with _state_lock:
                    _state["balance"] = bal_float
                    _state["last_balance_update"] = time.time()
            except (ValueError, TypeError):
                pass

    async def _generate_signal(self):
        """Analyze recent ticks and produce a signal."""
        try:
            ticks = await self.client.get_ticks_history(self.symbol, count=100)
        except Exception as e:
            LOG.warning("Failed to fetch ticks: %s", e)
            return

        if len(ticks) < 20:
            return

        # Determine direction: compare current vs 10 ticks ago
        current_price = ticks[-1].price
        prev_price = ticks[-10].price if len(ticks) >= 10 else ticks[0].price
        direction = "CALL" if current_price > prev_price else ("PUT" if current_price < prev_price else "NEUTRAL")

        # Confidence estimate based on momentum strength
        price_change_pct = abs(current_price - prev_price) / prev_price * 100 if prev_price > 0 else 0
        confidence = min(round(price_change_pct * 10, 1), 95.0)  # scale to 0-95
        if direction == "NEUTRAL":
            confidence = 0.0

        # Run Momen pattern analysis if available
        momen_confidence = None
        try:
            from deriv.patterns import MomenPatternAnalyzer
            analyzer = MomenPatternAnalyzer(analysis_ticks=len(ticks))
            momen = analyzer.analyze(ticks)
            if momen:
                momen_confidence = round(momen.confidence * 100, 1)
                # Blend confidences if Momen found
                confidence = round((confidence + momen_confidence) / 2, 1)
        except Exception:
            pass

        signal = {
            "symbol": self.symbol,
            "direction": direction,
            "entry_price": round(current_price, 5),
            "confidence": confidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "epoch": int(time.time()),
            "momen_confidence": momen_confidence,
            "ticks_analyzed": len(ticks),
        }

        with _state_lock:
            _state["latest_signal"] = signal
            _state["signals_generated"] += 1

        LOG.info("📡 Signal: %s %s @ %.5f (conf=%.1f%%)",
                 signal["symbol"], signal["direction"], signal["entry_price"], signal["confidence"])


# ── HTTP Request Handler ──

class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Deriv signal bridge."""

    def _json(self, data, code=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _get_params(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return parsed.path.rstrip("/"), qs

    def log_message(self, format, *args):
        LOG.info("HTTP %s", format % args)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path, params = self._get_params()

        if path in ("/signal",):
            self._handle_signal()
        elif path in ("/status", "/health"):
            self._handle_status()
        elif path in ("/balance",):
            self._handle_balance()
        else:
            self._json({"error": "not_found", "endpoints": ["/signal", "/status", "/balance", "/health"]}, 404)

    def _handle_signal(self):
        """GET /signal — returns latest Deriv signal."""
        with _state_lock:
            sig = dict(_state["latest_signal"]) if _state["latest_signal"] else {}

        if not sig:
            self._json({
                "signal": None,
                "message": "No signal generated yet — waiting for market data",
                "symbol": DEFAULT_SYMBOL,
            })
            return

        self._json({
            "signal": sig,
            "status": "ok",
        })

    def _handle_status(self):
        """GET /status — returns strategy health."""
        with _state_lock:
            result = {
                "status": "connected" if _state["connected"] else "disconnected",
                "connected": _state["connected"],
                "total_ticks": _state["total_ticks"],
                "signals_generated": _state["signals_generated"],
                "balance": _state["balance"],
                "last_balance_update": datetime.fromtimestamp(
                    _state["last_balance_update"], tz=timezone.utc
                ).isoformat() if _state["last_balance_update"] else None,
                "uptime_seconds": int(time.time() - START_TIME),
                "error": _state["error"],
                "symbol": DEFAULT_SYMBOL,
            }

        self._json(result)

    def _handle_balance(self):
        """GET /balance — returns current Deriv balance."""
        with _state_lock:
            bal = _state["balance"]
            last_upd = _state["last_balance_update"]

        if bal is None:
            self._json({
                "balance": None,
                "currency": "USD",
                "status": "pending",
                "message": "Balance not yet fetched",
            })
            return

        self._json({
            "balance": bal,
            "currency": "USD",
            "updated_at": datetime.fromtimestamp(last_upd, tz=timezone.utc).isoformat()
                         if last_upd else None,
            "status": "ok",
        })


# ── Background Async Runner ──

class DerivBridgeRunner:
    """Manages the async Deriv client in a background thread + sync HTTP server."""

    def __init__(self, symbol: str = DEFAULT_SYMBOL, host: str = HOST, port: int = PORT):
        self.symbol = symbol
        self.host = host
        self.port = port
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._generator: Optional[DerivSignalGenerator] = None
        self._httpd: Optional[HTTPServer] = None

    def _run_async_loop(self):
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._generator = DerivSignalGenerator(symbol=self.symbol)
        try:
            self._loop.run_until_complete(self._generator.start())
        except Exception as e:
            LOG.error("Async loop exited: %s", e)
        finally:
            self._loop.close()

    def start(self):
        """Start the async WS thread and the HTTP server."""
        # ── Start async Deriv client in background thread ──
        LOG.info("🚀 Starting Deriv Signal Bridge v1.0")
        LOG.info("   Symbol: %s", self.symbol)
        LOG.info("   HTTP: http://%s:%d", self.host, self.port)
        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

        # ── Start HTTP server (main thread) ──
        self._httpd = HTTPServer((self.host, self.port), BridgeHandler)
        LOG.info("✅ HTTP server listening on %s:%d", self.host, self.port)
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            LOG.info("🛑 Shutting down...")
        finally:
            self.shutdown()

    def shutdown(self):
        """Graceful shutdown."""
        LOG.info("🛑 Shutting down bridge...")
        if self._generator:
            self._generator.stop()
        if self._httpd:
            self._httpd.shutdown()


# ── Main Entrypoint ──

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Deriv Signal Bridge — HTTP server on port 8082")
    ap.add_argument("--port", type=int, default=PORT, help="HTTP server port (default: 8082)")
    ap.add_argument("--host", default=HOST, help="HTTP bind address (default: 0.0.0.0)")
    ap.add_argument("--symbol", default=DEFAULT_SYMBOL, help=f"Trading symbol (default: {DEFAULT_SYMBOL})")
    args = ap.parse_args()

    runner = DerivBridgeRunner(symbol=args.symbol, host=args.host, port=args.port)
    try:
        runner.start()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
