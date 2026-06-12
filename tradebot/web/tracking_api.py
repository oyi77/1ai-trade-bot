"""
Tracking API — FB Ads tracking pixel + deep link generation.

Serves:
    GET  /track.js  — Tracking pixel JavaScript
    POST /api/track/capture — Capture FB click data
    GET  /track/health — Health check

Port of scripts/tracking_api.py into FastAPI router.
Uses tradebot.tracking module for all data operations.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from tradebot.tracking.capture import create_tracking_record
from tradebot.tracking.deep_link import generate_deep_link

LOG = logging.getLogger("tradebot.web.tracking_api")

router = APIRouter(prefix="/api/track", tags=["tracking"])

TRACK_JS = """// Vilona Trade FX — FB Tracking Pixel (unified)
(function() {
    var params = new URLSearchParams(window.location.search);
    var fbclid = params.get('fbclid') || '';
    var utm_source = params.get('utm_source') || '';
    var utm_medium = params.get('utm_medium') || '';
    var utm_campaign = params.get('utm_campaign') || '';

    try {
        sessionStorage.setItem('vtfx_fbclid', fbclid);
        sessionStorage.setItem('vtfx_utm_source', utm_source);
        sessionStorage.setItem('vtfx_utm_medium', utm_medium);
        sessionStorage.setItem('vtfx_utm_campaign', utm_campaign);
    } catch(e) {}

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

            var links = document.querySelectorAll('a[href*="t.me/berkahkaryaforexbotbot"]');
            links.forEach(function(a) {
                if (a.getAttribute('data-tracked')) return;
                a.setAttribute('data-original-href', a.href);
                a.href = data.deep_link;
                a.setAttribute('data-tracked', '1');
            });
        }
    })
    .catch(function(e) {
        console.warn('VTFX tracking capture failed:', e);
    });

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
            window.location.href = link.href;
        }
    });
})();
"""


@router.get("/track.js", include_in_schema=False)
async def serve_track_js():
    """Serve tracking pixel JavaScript."""
    return PlainTextResponse(
        content=TRACK_JS,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.post("/capture")
async def capture_tracking(request: Request):
    """Capture FB click data and generate deep link."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

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
        ip_address=request.client.host if request.client else "",
        user_agent=request.headers.get("User-Agent", ""),
        landing_url=landing_url,
    )
    deep_link = generate_deep_link(tracking_id)
    LOG.info("Tracked: %s → %s", tracking_id, deep_link)

    return {
        "tracking_id": tracking_id,
        "deep_link": deep_link,
    }


@router.get("/health")
async def track_health():
    return {"status": "ok", "service": "tracking-api"}
