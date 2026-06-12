#!/usr/bin/env python3
"""Centralized Facebook CAPI event firing for Vilona Trade FX tracking."""

import json
import logging
import os
import time
import urllib.request

from tradebot.tracking.capture import get_tracking_by_telegram

logger = logging.getLogger("vtfx-tracking-events")

FB_PIXEL_ID = os.environ.get("FB_PIXEL_ID", "")
FB_ACCESS_TOKEN = os.environ.get("FB_ACCESS_TOKEN", "")
API_VERSION = "v19.0"


def _fb_capi_url():
    return (
        f"https://graph.facebook.com/{API_VERSION}"
        f"/{FB_PIXEL_ID}/events?access_token={FB_ACCESS_TOKEN}"
    )


def fire_capi_event(event_name, user_data, custom_data, event_source_url):
    """Send a generic CAPI event to Facebook."""
    if not FB_PIXEL_ID or not FB_ACCESS_TOKEN:
        logger.warning("CAPI skipped: FB_PIXEL_ID or FB_ACCESS_TOKEN not set")
        return None

    payload = json.dumps({
        "data": [{
            "event_name": event_name,
            "event_time": int(time.time()),
            "action_source": "website",
            "event_source_url": event_source_url,
            "user_data": user_data,
            "custom_data": custom_data,
        }]
    }).encode()

    try:
        req = urllib.request.Request(
            _fb_capi_url(),
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
        logger.info("CAPI %s fired: %s", event_name, result)
        return result
    except Exception as exc:
        logger.error("CAPI %s failed: %s", event_name, exc)
        return None


def fire_page_view(fbclid, ip, ua, url):
    """Fire PageView or ViewContent event for landing page visitors."""
    fire_capi_event(
        "PageView",
        {
            "client_ip_address": ip or "",
            "client_user_agent": ua or "",
        },
        {
            "fbclid": fbclid or "",
            "content_category": "Trading Signal",
        },
        url or "https://phantomfx.aitradepulse.com/lp",
    )


def fire_lead(telegram_user_id, tracking_id, tier):
    """Fire Lead event when user starts bot via deep link."""
    tracking_rows = get_tracking_by_telegram(telegram_user_id)
    fbclid = tracking_rows[0].get("fbclid", "") if tracking_rows else ""

    fire_capi_event(
        "Lead",
        {
            "external_id": str(telegram_user_id),
        },
        {
            "fbclid": fbclid,
            "tracking_id": tracking_id,
            "tier": tier or "free",
            "content_name": "Telegram Bot Start",
        },
        "https://phantomfx.aitradepulse.com/lp",
    )


def fire_initiate_checkout(telegram_user_id, tier, amount):
    """Fire InitiateCheckout event for subscription flow."""
    tracking_rows = get_tracking_by_telegram(telegram_user_id)
    fbclid = tracking_rows[0].get("fbclid", "") if tracking_rows else ""

    fire_capi_event(
        "InitiateCheckout",
        {
            "external_id": str(telegram_user_id),
        },
        {
            "fbclid": fbclid,
            "tier": tier or "free",
            "value": amount or 0,
            "currency": "IDR",
            "content_category": "Subscription",
        },
        "https://phantomfx.aitradepulse.com/lp",
    )


def fire_purchase(telegram_user_id, tier, amount, transaction_id):
    """Fire Purchase event with full user data from tracking."""
    tracking_rows = get_tracking_by_telegram(telegram_user_id)
    user_data = {
        "external_id": str(telegram_user_id),
    }

    fbclid = ""
    if tracking_rows:
        row = tracking_rows[0]
        fbclid = row.get("fbclid", "")
        if row.get("ip_address"):
            user_data["client_ip_address"] = row["ip_address"]
        if row.get("user_agent"):
            user_data["client_user_agent"] = row["user_agent"]

    fire_capi_event(
        "Purchase",
        user_data,
        {
            "fbclid": fbclid,
            "tier": tier or "",
            "value": amount or 0,
            "currency": "IDR",
            "transaction_id": transaction_id or "",
            "content_category": "Subscription",
        },
        "https://phantomfx.aitradepulse.com/lp",
    )
