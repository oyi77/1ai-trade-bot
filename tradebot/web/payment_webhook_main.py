#!/usr/bin/env python3
"""
Entry point for the unified payment webhook server (port 8787).

Ported from scripts/payment_webhook_server.py.
Used by systemd service vtfx-payment-webhook.service.
"""

from tradebot.web.payment_webhook import start_webhook_server

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PAYMENT_WEBHOOK_PORT", "8787"))
    host = os.environ.get("PAYMENT_WEBHOOK_HOST", "127.0.0.1")
    server = start_webhook_server(host=host, port=port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()