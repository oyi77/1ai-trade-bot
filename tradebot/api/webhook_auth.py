"""
HMAC-SHA256 webhook authentication for Vilona EA trade-close events.

Every EA sends a request body + X-Vilona-Signature header.
The signature is HMAC-SHA256(body, VILONA_WEBHOOK_SECRET).
If the secret is not configured or the signature is invalid → 401.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import Header, HTTPException, Request

from tradebot.config import settings

LOG = logging.getLogger(__name__)

_HEADER_SIGNATURE = "x-vilona-signature"


async def verify_vilona_webhook(
    request: Request,
    x_vilona_signature: str = Header("", alias=_HEADER_SIGNATURE),
) -> str:
    """FastAPI dependency — validate HMAC-SHA256 signature on request body.

    Returns the raw request body string on success.
    Raises HTTPException(401) on any failure.

    Usage:
        @router.post("/webhook/trade-close")
        async def webhook(body: str = Depends(verify_vilona_webhook)):
            ...
    """
    secret = settings.VILONA_WEBHOOK_SECRET
    if not secret:
        LOG.error("VILONA_WEBHOOK_SECRET not configured — rejecting webhook")
        raise HTTPException(status_code=401, detail="Webhook secret not configured")

    if not x_vilona_signature:
        LOG.warning("Webhook rejected: missing X-Vilona-Signature header")
        raise HTTPException(status_code=401, detail="Missing signature header")

    body_bytes = await request.body()

    # Compute expected HMAC
    expected = hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, x_vilona_signature.strip()):
        LOG.warning("Webhook rejected: invalid HMAC signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    return body_bytes.decode("utf-8")


def compute_hmac_signature(body: str | bytes) -> str:
    """Compute an X-Vilona-Signature value from a body string.

    Used by EA scripts and test utilities.
    """
    secret = settings.VILONA_WEBHOOK_SECRET
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
