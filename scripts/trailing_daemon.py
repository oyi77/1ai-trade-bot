#!/usr/bin/env python3
"""Vilona Trailing Daemon — polls bridge for SL updates, applies to MT5."""
import json, os, sys, time, urllib.request

BRIDGE_URL = "http://localhost:8765"
API_KEY = os.environ.get("VILONA_TRADEFX_API_KEY", "VT-PRO-LAUNCH")
ACCOUNT_ID = os.environ.get("VILONA_MT5_ACCOUNT", "183371455")
POLL_SECONDS = 5

last_known_sl = {}

while True:
    try:
        url = f"{BRIDGE_URL}/signal?mode=trailing&api_key={API_KEY}&account_id={ACCOUNT_ID}"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())

        sl = data.get("sl", 0)
        ticket = data.get("ticket", "")

        if sl and ticket and sl != last_known_sl.get(ticket):
            # --- MT5 POSITION MODIFY via bridge trailing endpoint ---
            modify = {
                "api_key": API_KEY, "account_id": ACCOUNT_ID,
                "ticket": ticket, "new_sl": sl
            }
            req2 = urllib.request.Request(
                f"{BRIDGE_URL}/trailing?api_key={API_KEY}&account_id={ACCOUNT_ID}",
                data=json.dumps(modify).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req2, timeout=5)
            last_known_sl[ticket] = sl
            print(f"TRAIL: ticket={ticket} SL→{sl}", flush=True)
    except Exception as e:
        pass  # silent — bridge restart is handled by systemd

    time.sleep(POLL_SECONDS)
