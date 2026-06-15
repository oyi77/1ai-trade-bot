"""MLM referral + commission distribution service."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

LOG = logging.getLogger(__name__)
BASE = Path(os.getenv("DATA_DIR", "data")) / "whitelabel"
BASE.mkdir(parents=True, exist_ok=True)


@dataclass
class CommissionEntry:
    payment_id: str
    amount: float
    user_id: str
    brand_id: str
    reseller_id: Optional[str] = None
    referrer_id: Optional[str] = None


def _rules() -> dict:
    p = BASE / "mlm_rules.json"
    if not p.exists():
        return {
            "global_active": True,
            "owner_cut": 0.70,
            "reseller_cut": 0.20,
            "referrer_cut": 0.10,
            "min_payout_idr": 100000,
            "payout_cycle": "monthly",
        }
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        LOG.warning("mlm rule read failed: %s", exc)
        return {}


def _commissions() -> dict:
    p = BASE / "commissions.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_commissions(data: dict) -> None:
    (BASE / "commissions.json").write_text(json.dumps(data, indent=2, default=str))


def track_commission(entry: CommissionEntry) -> dict:
    rules = _rules()
    if not rules.get("global_active", False):
        LOG.info("MLM disabled globally; skip commission for %s", entry.payment_id)
        return {"skipped": True}

    brand_override = (rules.get("per_brand_override") or {}).get(entry.brand_id, {})
    owner_cut = brand_override.get("owner_cut", rules.get("owner_cut", 0.70))
    reseller_cut = brand_override.get("reseller_cut", rules.get("reseller_cut", 0.20))
    referrer_cut = brand_override.get("referrer_cut", rules.get("referrer_cut", 0.10))

    amount = float(entry.amount)
    owner_amount = round(amount * owner_cut)
    reseller_amount = round(amount * reseller_cut)
    referrer_amount = amount - owner_amount - reseller_amount

    record = {
        "payment_id": entry.payment_id,
        "brand_id": entry.brand_id,
        "total": amount,
        "owner_amount": owner_amount,
        "reseller_id": entry.reseller_id,
        "reseller_amount": reseller_amount,
        "referrer_id": entry.referrer_id,
        "referrer_amount": referrer_amount,
        "created_at": __import__("datetime").datetime.now().isoformat(),
    }
    data = _commissions()
    data[entry.payment_id] = record
    _save_commissions(data)
    return record
