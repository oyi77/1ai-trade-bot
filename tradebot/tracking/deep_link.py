#!/usr/bin/env python3
"""Deep link generation and parsing for Telegram bot tracking."""

import logging

logger = logging.getLogger("vtfx-tracking-deeplink")

BOT_USERNAME = "berkahkaryaforexbotbot"
TRACK_PREFIX = "track_"


def generate_deep_link(tracking_id):
    """Generate a Telegram deep link with tracking_id embedded."""
    return f"https://t.me/{BOT_USERNAME}?start={TRACK_PREFIX}{tracking_id}"


def parse_start_payload(start_param):
    """Parse /start payload for tracking_id.
    Returns (True, tracking_id) if payload starts with 'track_',
    otherwise (False, None).
    """
    if start_param and start_param.startswith(TRACK_PREFIX):
        tracking_id = start_param[len(TRACK_PREFIX):]
        return True, tracking_id
    return False, None
