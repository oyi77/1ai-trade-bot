"""
Bridge Server — HTTP server for external signal access.

Provides REST endpoints for querying the latest trading signal,
broker status, and account balance.
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from tradebot.config import settings

LOG = logging.getLogger(__name__)


class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP handler for bridge server endpoints."""

    server_state: dict = {}

    def _json(self, data, code=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        LOG.info("HTTP %s", fmt % args)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")

        if path == "/signal":
            self._json(self.server_state.get("signal", {"status": "no_signal"}))
        elif path == "/status":
            self._json({
                "status": "ok",
                "engine_count": len(self.server_state.get("engines", [])),
                "connected": self.server_state.get("connected", False),
            })
        elif path == "/balance":
            self._json(self.server_state.get("balance", {"balance": None}))
        else:
            self._json({"error": "not_found"}, 404)


class BridgeServer:
    """HTTP bridge server for external signal access.

    Runs on BRIDGE_HOST:BRIDGE_PORT (from settings).
    Shares state dict for reading signals, status, and balance.
    """

    def __init__(self, host: str = "", port: int = 0,
                 state: dict | None = None):
        self.host = host or settings.BRIDGE_HOST
        self.port = port or settings.BRIDGE_PORT
        self._httpd: HTTPServer | None = None
        self.state = state or {}

        BridgeHandler.server_state = self.state

    def start(self):
        """Start the HTTP server (blocks until KeyboardInterrupt)."""
        self._httpd = HTTPServer((self.host, self.port), BridgeHandler)
        LOG.info("🌐 Bridge server listening on %s:%d", self.host, self.port)
        try:
            self._httpd.serve_forever()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            LOG.info("Bridge server stopped")
