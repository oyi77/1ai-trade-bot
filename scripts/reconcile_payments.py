#!/usr/bin/env python3
"""Payment reconciliation daemon — auto-activate ScaleV payments from pending.json.

Runs every 15 min via cron. Checks scalev_pending.json for entries older than
5 minutes, activates them in members.db, then removes from pending.
"""
import json, sqlite3, os, sys, logging, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOG = logging.getLogger("reconcile")

WIB = timezone(timedelta(hours=7))
BASE = Path(__file__).resolve().parent.parent.parent
DATA = BASE / "data" / "vilona_tradefx"
PENDING = DATA / "scalev_pending.json"
MEMBERS = DATA / "members.db"
STALE_MIN = 5  # act if payment link > 5 min old


def main():
    if not PENDING.exists():
        return

    with open(PENDING) as f:
        pending = json.load(f)

    if not pending:
        return

    if not MEMBERS.exists():
        LOG.warning("members.db not found at %s", MEMBERS)
        return

    db = sqlite3.connect(str(MEMBERS))
    now = datetime.now(WIB)
    processed = []

    for ref, info in list(pending.items()):
        chat_id = info.get("chat_id", "")
        tier = info.get("tier", "pro")
        amount = info.get("amount", 0)
        ts = info.get("timestamp", 0)

        if not chat_id:
            continue

        # Skip if newer than STALE_MIN minutes
        age_min = (time.time() - ts) / 60
        if age_min < STALE_MIN:
            continue

        # Check if already activated
        cur = db.execute(
            "SELECT tier, status FROM members WHERE chat_id=? AND tier!=? AND status=?",
            (chat_id, "starter", "paid"),
        )
        if cur.fetchone():
            LOG.info("Already active: %s — skip", chat_id)
            processed.append(ref)
            continue

        # Activate
        days = {"pro": 30, "elite": 30, "lifetime": 9999, "donor": 9999}.get(tier, 30)
        expiry = (now + timedelta(days=days)).isoformat()
        db.execute(
            "UPDATE members SET tier=?, status=?, payment_ref=?, expiry=? WHERE chat_id=?",
            (tier, "paid", f"AUTO-RECONCILE:{ref}", expiry, chat_id),
        )
        db.commit()
        LOG.info("✅ ACTIVATED %s → %s (Rp%,d) — expiry %s", chat_id, tier.upper(), amount, expiry)
        processed.append(ref)

    db.close()

    # Remove processed from pending
    if processed:
        for ref in processed:
            del pending[ref]
        with open(PENDING, "w") as f:
            json.dump(pending, f, indent=2)
        LOG.info("Cleaned %d stale pending entries. Remaining: %d", len(processed), len(pending))


if __name__ == "__main__":
    main()
