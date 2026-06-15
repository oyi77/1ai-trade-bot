"""Scalev Adapter — wraps Scalev checkout + webhook verification for subscriptions."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))

# Order → plan mapping used by this bot
PLAN_ORDER_SNAPSHOT: dict[str, dict] = {
    "AI Trader": {
        "plan_key": "basic",
        "variant_id": 527961,
        "duration_days": 30,
        "price_idr": 99000,
    },
    "Suami Perkasa": {
        "plan_key": "pro",
        "variant_id": 479490,
        "duration_days": 30,
        "price_idr": 77000,
    },
}

# Legacy alias → plan_key (for any webhook parsing that still mentions these)
VARIANT_ID_TO_PLAN_KEY: dict[int, str] = {
    527961: "ai_trader",
    479490: "suami_perkasa",
    530093: "basic",
    530094: "pro",
}


@dataclass
class ScalevConfig:
    store_id: str = ""
    storefront_api_key: str = ""
    api_key: str = ""
    signing_secret: str = ""
    webhook_url: str = ""
    checkout_base_url: str = "https://api.scalev.com/v3"
    payment_store_path: str = ""


@dataclass
class CheckoutResult:
    success: bool = False
    order_id: str = ""
    payment_url: str = ""
    public_order_url: str = ""
    secret_slug: str = ""
    raw: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class OrderStatus:
    found: bool = False
    order_id: str = ""
    status: str = "UNKNOWN"
    paid_at: Optional[str] = None
    plan_key: str = ""
    raw: dict = field(default_factory=dict)
    error: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = "".join(c if (c.isalnum() or c in (" ", "-")) else "" for c in slug)
    slug = slug.replace(" ", "-")
    return slug[:120]


def _sign_scalev(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


# ── Adapter ──────────────────────────────────────────────────────────────────

class ScalevAdapter:
    def __init__(self, config: Optional[ScalevConfig] = None):
        self.config = config or ScalevConfig()
        self._initialized = False
        self._store_path = Path("data/scalev_orders.json")

    # Lifecycle ----------------------------------------------------------------

    async def initialize(self) -> bool:
        try:
            cfg = self.config
            if not cfg.store_id:
                cfg.store_id = os.environ.get(
                    "SCALEV_STORE_ID", "store_xdK72UFPYZRo8zvgOmdFgYeP"
                )
            if not cfg.storefront_api_key:
                cfg.storefront_api_key = os.environ.get(
                    "SCALEV_STOREFRONT_API_KEY", ""
                ) or os.environ.get("SCALEV_CLIENT_ID", "")
            if not cfg.api_key:
                cfg.api_key = os.environ.get("SCALEV_API_KEY", "")
            if not cfg.signing_secret:
                cfg.signing_secret = os.environ.get(
                    "SCALEV_SIGNING_SECRET", ""
                )
            if not cfg.webhook_url:
                cfg.webhook_url = os.environ.get(
                    "SCALEV_WEBHOOK_URL", ""
                )
            if not cfg.payment_store_path:
                cfg.payment_store_path = (
                    Path(__file__).resolve().parent.parent
                    / "data"
                    / "scalev_orders.json"
                ).as_posix()
            self._store_path = Path(cfg.payment_store_path)
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialized = True
            LOG.info("ScalevAdapter initialized (store=%s)", cfg.store_id)
            return True
        except Exception as exc:
            LOG.error("ScalevAdapter init failed: %s", exc)
            return False

    async def shutdown(self) -> None:
        self._initialized = False
        LOG.info("ScalevAdapter shutdown")

    # Checkout ----------------------------------------------------------------

    async def create_checkout(
        self,
        telegram_user_id: int,
        plan_key: str = "basic",
        customer_name: str = "Hermes User",
        customer_email: str = "",
    ) -> CheckoutResult:
        plan = PLAN_ORDER_SNAPSHOT.get(plan_key)
        if not plan:
            return CheckoutResult(
                success=False, error=f"Unknown plan_key={plan_key}"
            )

        variant_id = plan["variant_id"]
        price_idr = plan["price_idr"]
        store_id = self.config.store_id
        public_key = self.config.storefront_api_key

        if not public_key or not store_id:
            return CheckoutResult(
                success=False,
                error="SCALEV_STORE_ID / SCALEV_STOREFRONT_API_KEY missing",
            )

        headers = {
            "Content-Type": "application/json",
            "X-Scalev-Storefront-Api-Key": public_key,
        }

        cart_url = (
            f"{self.config.checkout_base_url}/stores/{store_id}"
            "/public/cart/items"
        )
        async with urllib.request._urlopen(cart_url) if False else _AsyncClient() as c:  # noqa: F841
            pass

        try:
            async with _HttpxSession() as sess:
                cart_resp = await sess.post(
                    cart_url,
                    headers=headers,
                    json={"variant_id": variant_id, "quantity": 1},
                )
        except Exception as exc:
            return CheckoutResult(success=False, error=str(exc))

        if cart_resp.status_code not in (200, 201):
            body = cart_resp.text[:300]
            return CheckoutResult(
                success=False,
                error=f"cart failed {cart_resp.status_code}: {body}",
            )

        guest_token = cart_resp.headers.get("X-Scalev-Guest-Token", "")
        cart_data = json.loads(cart_resp.text) if isinstance(cart_resp.text, str) else {}
        LOG.info("cart created token=%s…", (guest_token[:20] if guest_token else ""))

        checkout_url = (
            f"{self.config.checkout_base_url}/stores/{store_id}"
            "/public/checkout"
        )
        checkout_headers = dict(headers)
        if guest_token:
            checkout_headers["X-Scalev-Guest-Token"] = guest_token

        payload = {
            "customer_name": customer_name,
            "customer_email": customer_email
            or f"tg_{telegram_user_id}@hermes.user",
            "payment_method": "bank_transfer",
            "notes": f"VTFX subscribe plan={plan_key} — tg://{telegram_user_id}",
        }

        try:
            async with _HttpxSession() as sess:
                resp = await sess.post(
                    checkout_url,
                    headers=checkout_headers,
                    json=payload,
                )
        except Exception as exc:
            return CheckoutResult(success=False, error=str(exc))

        if resp.status_code not in (200, 201):
            body = resp.text[:500]
            return CheckoutResult(
                success=False,
                error=f"checkout failed {resp.status_code}: {body}",
            )

        data = json.loads(resp.text) if isinstance(resp.text, str) else {}
        order_id = data.get("order_id", "")
        return CheckoutResult(
            success=True,
            order_id=order_id,
            payment_url=data.get("payment_url", ""),
            public_order_url=data.get("public_order_url", ""),
            secret_slug=data.get("secret_slug", ""),
            raw=data,
        )

    # Webhook verification -----------------------------------------------------

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        if not self.config.signing_secret or not signature:
            return True
        expected = _sign_scalev(raw_body, self.config.signing_secret)
        return hmac.compare_digest(expected, signature)

    # Status -------------------------------------------------------------------

    async def get_order_status(self, order_id: str) -> OrderStatus:
        if not self._initialized:
            await self.initialize()

        url = (
            f"{self.config.checkout_base_url}/orders/{order_id}"
        )
        headers = {"Authorization": f"Bearer {self.config.api_key}"}

        try:
            async with _HttpxSession() as sess:
                resp = await sess.get(url, headers=headers)
            data = json.loads(resp.text) if isinstance(resp.text, str) else {}
        except Exception as exc:
            return OrderStatus(error=str(exc))

        if resp.status_code != 200:
            return OrderStatus(error=f"HTTP {resp.status_code}")

        item = data.get("data", data)
        status = str(item.get("status", "")).upper()
        paid_at = item.get("paid_at") or item.get("completed_at") or item.get("updated_at")

        snapshot = self._load_store().get("by_order_id", {}).get(order_id, {})
        plan_key = snapshot.get("plan_key", "")

        return OrderStatus(
            found=True,
            order_id=order_id,
            status=status,
            paid_at=paid_at,
            plan_key=plan_key,
            raw=item,
        )

    # Mapping helpers ----------------------------------------------------------

    def variant_id_to_plan(self, variant_id: int) -> str:
        return VARIANT_ID_TO_PLAN_KEY.get(variant_id, "")

    def plan_limits(self, plan_key: str) -> dict:
        return {
            "basic": {
                "ideas_per_day": 5,
                "active_campaigns": 3,
                "landing_pages": 5,
                "daily_reports": True,
            },
            "pro": {
                "ideas_per_day": 999,
                "active_campaigns": 999,
                "landing_pages": 999,
                "daily_reports": True,
            },
            "trial": {
                "ideas_per_day": 1,
                "active_campaigns": 1,
                "landing_pages": 2,
                "daily_reports": False,
            },
        }.get(plan_key or "trial", {})

    # Local helper store (best-effort; real source should be scalev order) -----

    def _load_store(self) -> dict:
        if self._store_path.exists():
            try:
                return json.loads(self._store_path.read_text())
            except Exception:
                pass
        return {"by_order_id": {}}

    def _save_store(self, data: dict) -> None:
        self._store_path.write_text(json.dumps(data, indent=2, default=str))

    def mark_order_paid(
        self, order_id: str, plan_key: str = ""
    ) -> tuple[bool, dict]:
        data = self._load_store()
        by_order = data.setdefault("by_order_id", {})
        row = by_order.setdefault(order_id, {})
        row["status"] = "PAID"
        row["plan_key"] = plan_key or row.get("plan_key", "")
        row["paid_at"] = datetime.now(WIB).isoformat()
        self._save_store(data)
        return True, row


# light async HTTP helper (no external http libs)
class _HttpxSession:
    def __init__(self) -> None:
        self._client: Optional[urllib.request._opener] = None

    async def __aenter__(self):  # type: ignore[override]
        return self

    async def __aexit__(self, *exc) -> None:  # type: ignore[override]
        return None

    async def request(self, method: str, url: str, **kwargs):
        headers = kwargs.get("headers", {}) or {}
        body = kwargs.get("json")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        ctx = urllib.request.urlopen(req, timeout=20)
        return _Resp(ctx)


class _Resp:
    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self.status_code = getattr(ctx, "status", 200)
        self.headers = ctx.headers
        self.text = ctx.read().decode("utf-8", errors="ignore")
