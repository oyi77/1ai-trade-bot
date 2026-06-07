#!/usr/bin/env python3
"""⚠️ DEPRECATED — Gunakan payment_webhook_server.py
File ini dipertahankan untuk backward compatibility.
Semua logic payment sekarang di payment_webhook_server.py (Donor model).
"""
import json, logging, sys, os, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Redirect to the real webhook server ──
try:
    from payment_webhook_server import WebhookHandler as RealHandler, main as real_main
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("vtfx-webhook")

    class WebhookHandler(RealHandler):
        """Thin wrapper — delegates to payment_webhook_server."""
        pass

    if __name__ == "__main__":
        real_main()

except ImportError as e:
    print(f"FATAL: Cannot import payment_webhook_server: {e}", file=sys.stderr)
    sys.exit(1)
