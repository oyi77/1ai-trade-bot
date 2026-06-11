"""
meta_conversion.py — Server-side Meta Conversions API helper.
Call send_purchase() from payment webhook when donation completes.
"""
from __future__ import annotations
import hashlib
import json
import logging
import time
import os
import urllib.request

LOG = logging.getLogger("meta_conversion")
PIXEL_ID = "771021905629860"
API_TOKEN = os.environ.get("META_CAPI_TOKEN", "YOUR_META_CAPI_TOKEN_HERE")
API_URL = f"https://graph.facebook.com/v18.0/{PIXEL_ID}/events"


def _hash(data: str) -> str:
    return hashlib.sha256(data.strip().lower().encode()).hexdigest()


def send_event(
    event_name: str,
    event_time: int = 0,
    value: float = 0,
    currency: str = "IDR",
    external_id: str = "",
    email: str = "",
    phone: str = "",
    event_source_url: str = "https://phantomfx.aitradepulse.com/lp",
    client_ip: str = "",
    client_user_agent: str = "",
) -> dict:
    """Send a single event to Meta Conversions API. Returns response dict."""
    if not event_time:
        event_time = int(time.time())

    user_data: dict[str, str] = {}
    if external_id:
        user_data["external_id"] = _hash(external_id)
    if email:
        user_data["em"] = _hash(email)
    if phone:
        user_data["ph"] = _hash(phone)

    if not user_data:
        user_data["external_id"] = _hash(str(event_time))

    payload = {
        "data": [{
            "event_name": event_name,
            "event_time": event_time,
            "user_data": user_data,
            "custom_data": {
                "value": value,
                "currency": currency,
            },
            "event_source_url": event_source_url,
            "action_source": "website",
        }],
        "access_token": API_TOKEN,
        "test_event_code": "TEST88387",
    }

    if client_ip:
        payload["data"][0]["user_data"]["client_ip_address"] = client_ip
    if client_user_agent:
        payload["data"][0]["user_data"]["client_user_agent"] = client_user_agent

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            API_URL, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        LOG.info("Meta CAPI %s: OK events_received=%s", event_name, result.get("events_received", 0))
        return {"ok": True, "response": result}
    except Exception as e:
        LOG.error("Meta CAPI %s failed: %s", event_name, e)
        return {"ok": False, "error": str(e)}


def send_purchase(amount: float, chat_id: str = "", username: str = "") -> dict:
    """Track a Purchase event when donation completes."""
    return send_event(
        event_name="Purchase",
        value=amount,
        currency="IDR",
        external_id=str(chat_id) if chat_id else username,
    )


def send_add_to_cart(chat_id: str = "") -> dict:
    """Track AddToCart when user starts bot."""
    return send_event(
        event_name="AddToCart",
        value=0,
        currency="IDR",
        external_id=str(chat_id),
    )


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("Usage: meta_conversion.py purchase <amount> <chat_id>")
        sys.exit(1)
    action = sys.argv[1]
    if action == "purchase":
        amount = float(sys.argv[2]) if len(sys.argv) > 2 else 50000
        chat_id = sys.argv[3] if len(sys.argv) > 3 else ""
        result = send_purchase(amount, chat_id)
        print(json.dumps(result, indent=2))
    elif action == "addtocart":
        chat_id = sys.argv[2] if len(sys.argv) > 2 else ""
        result = send_add_to_cart(chat_id)
        print(json.dumps(result, indent=2))
