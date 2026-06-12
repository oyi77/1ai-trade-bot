#!/usr/bin/env python3
"""Tracking API server on port 8790 — serves track.js + captures FB params.

Usage: python3 scripts/tracking_api.py --port 8790
"""

import json
import logging
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)

from tradebot.tracking.capture import create_tracking_record
from tradebot.tracking.deep_link import generate_deep_link
from tradebot.tracking.schema import init_tracking_db

# Init DB on startup
DB_PATH = os.path.join(PROJECT_DIR, "data", "vilona_tradefx", "tracking.db")
init_tracking_db(DB_PATH)
logging.info(f"Tracking DB ready: {DB_PATH}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tracking-api")

TRACK_JS = """// Vilona Trade FX — FB Tracking Pixel
(function() {
    var params = new URLSearchParams(window.location.search);
    var fbclid = params.get('fbclid') || '';
    var utm_source = params.get('utm_source') || '';
    var utm_medium = params.get('utm_medium') || '';
    var utm_campaign = params.get('utm_campaign') || '';

    // Save to sessionStorage
    try {
        sessionStorage.setItem('vtfx_fbclid', fbclid);
        sessionStorage.setItem('vtfx_utm_source', utm_source);
        sessionStorage.setItem('vtfx_utm_medium', utm_medium);
        sessionStorage.setItem('vtfx_utm_campaign', utm_campaign);
    } catch(e) {}

    // Send to capture endpoint
    var payload = {
        fbclid: fbclid,
        utm_source: utm_source,
        utm_medium: utm_medium,
        utm_campaign: utm_campaign,
        landing_url: window.location.href
    };

    fetch('/api/track/capture', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.tracking_id && data.deep_link) {
            try {
                sessionStorage.setItem('vtfx_tracking_id', data.tracking_id);
                sessionStorage.setItem('vtfx_deep_link', data.deep_link);
            } catch(e) {}

            // Wire up Telegram connect button
            var btn = document.getElementById('tg-connect-btn');
            if (btn) {
                // Update href with tracked deep link
                var links = document.querySelectorAll('a[href*="t.me/berkahkaryaforexbotbot"]');
                links.forEach(function(a) {
                    if (a.getAttribute('data-tracked')) return;
                    a.setAttribute('data-original-href', a.href);
                    a.href = data.deep_link;
                    a.setAttribute('data-tracked', '1');
                });
            }
        }
    })
    .catch(function(e) {
        console.warn('VTFX tracking capture failed:', e);
    });

    // Click handler fallback: generate deep link on click if not already tracked
    document.addEventListener('click', function(e) {
        var link = e.target.closest('a[href*="t.me/berkahkaryaforexbotbot"]');
        if (!link || link.getAttribute('data-tracked')) return;
        e.preventDefault();
        var tid = sessionStorage.getItem('vtfx_tracking_id');
        var dl = sessionStorage.getItem('vtfx_deep_link');
        if (dl) {
            link.setAttribute('data-tracked', '1');
            window.location.href = dl;
        } else {
            // Fallback: navigate without tracking
            window.location.href = link.href;
        }
    });
})();
"""


class TrackingHandler(BaseHTTPRequestHandler):

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _js(self, content, code=200):
        body = content.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/javascript")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/track.js" or path == "/track":
            self._js(TRACK_JS)
        elif path == "/health":
            self._json({"status": "ok", "service": "tracking-api"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/track/capture":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                self._json({"error": "invalid json"}, 400)
                return

            fbclid = body.get("fbclid", "")
            utm_source = body.get("utm_source", "")
            utm_medium = body.get("utm_medium", "")
            utm_campaign = body.get("utm_campaign", "")
            landing_url = body.get("landing_url", "")

            tracking_id = create_tracking_record(
                fbclid=fbclid,
                utm_source=utm_source,
                utm_medium=utm_medium,
                utm_campaign=utm_campaign,
                ip_address=self.client_address[0],
                user_agent=self.headers.get("User-Agent", ""),
                landing_url=landing_url,
            )
            deep_link = generate_deep_link(tracking_id)
            log.info("Tracked: %s → %s", tracking_id, deep_link)
            self._json({"tracking_id": tracking_id, "deep_link": deep_link})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        log.debug(format % args)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Tracking API Server")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    log.info("Tracking API listening on %s:%s", args.host, args.port)
    server = HTTPServer((args.host, args.port), TrackingHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
