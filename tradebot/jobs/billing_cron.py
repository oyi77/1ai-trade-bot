#!/usr/bin/env python3
"""
Gotong Royong Weekly Billing Cron Job.

Runs every Saturday morning. Iterates through all premium users,
calculates weekly Villona-tagged P&L, applies High-Water Mark billing,
and returns structured invoice payloads for Telegram dispatch.

Usage (standalone):
    python -m tradebot.jobs.billing_cron

Usage (cron):
    0 9 * * SAT cd ~/projects/1ai-trade-bot && python -m tradebot.jobs.billing_cron

Output:
    Prints JSON lines — one per user with a fee > 0.
    Each line includes the Telegram message text for dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tradebot.config import settings
from tradebot.services.accounting import AccountingService, TradeRecord
from tradebot.services.members_service import is_premium

LOG = logging.getLogger("tradebot.jobs.billing_cron")

WIB = timezone(timedelta(hours=7))

# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════


def _get_period_dates(now: datetime) -> tuple[str, str]:
    """Return ISO date strings for the previous Mon–Sun week."""
    days_since_monday = now.weekday()
    last_monday = now - timedelta(days=days_since_monday + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")


def _format_invoice(cycle) -> str:
    """Build the Telegram message for a user with a fee due."""
    fee_rate_pct = int(settings.GOTONG_ROYONG_FEE_RATE * 100)
    payment_url = settings.GOTONG_ROYONG_PAYMENT_URL or "/pay"

    lines = [
        "🧾 <b>Gotong Royong — Weekly Performance Fee</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 Period: <b>{cycle.period_start}</b> → <b>{cycle.period_end}</b>",
        f"📊 Vilona Bot P&L: <b>${cycle.bot_pnl:,.2f}</b>",
        f"📈 HWM (before): <b>${cycle.hwm_baseline:,.2f}</b>",
        f"📈 HWM (after):  <b>${cycle.hwm_new:,.2f}</b>",
        "",
        f"💰 Performance Fee ({fee_rate_pct}%): <b>${cycle.fee_amount:,.2f}</b>",
        "",
        f"👉 <a href='{payment_url}'>Pay via Tripay</a>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Fee only charged on <b>new profit above previous high</b>.",
        "Drawdown recovery is always free. 🤝",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  MAIN BILLING LOOP
# ═══════════════════════════════════════════════════════════════════


async def run_billing_cycle(
    db_path: str = "",
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Run billing for all premium users. Returns invoice payloads."""
    accounting = AccountingService(db_path=db_path)
    now = datetime.now(WIB)
    period_start, period_end = _get_period_dates(now)

    # Load premium members
    premium_ids = await _load_premium_chat_ids()
    if not premium_ids:
        LOG.info("No premium users found — billing cycle skipped.")
        return []

    invoices: list[dict[str, Any]] = []
    skipped = 0
    errors = 0

    for chat_id in premium_ids:
        for platform in ("ccxt", "mt5"):
            try:
                # ── Fetch trades from broker ────────────────────────
                trades = await _fetch_broker_trades(chat_id, platform, period_start, period_end)

                # ── Generate billing cycle ──────────────────────────
                cycle = await accounting.generate_billing_cycle(
                    chat_id=chat_id,
                    platform=platform,
                    trades=trades,
                    period_start=period_start,
                    period_end=period_end,
                )

                if cycle.fee_amount <= 0:
                    skipped += 1
                    LOG.debug(
                        "No fee for %s/%s: pnl=%.2f hwm=%.2f",
                        chat_id, platform, cycle.bot_pnl, cycle.hwm_baseline,
                    )
                    continue

                invoice_msg = _format_invoice(cycle)

                invoices.append({
                    "chat_id": chat_id,
                    "platform": platform,
                    "period_start": period_start,
                    "period_end": period_end,
                    "fee_amount": cycle.fee_amount,
                    "bot_pnl": cycle.bot_pnl,
                    "hwm_baseline": cycle.hwm_baseline,
                    "hwm_new": cycle.hwm_new,
                    "telegram_message": invoice_msg,
                })

                LOG.info(
                    "💸 Fee generated: %s/%s = $%.2f (pnl=%.2f hwm=%.2f)",
                    chat_id, platform, cycle.fee_amount,
                    cycle.bot_pnl, cycle.hwm_baseline,
                )

            except Exception as exc:
                errors += 1
                LOG.error(
                    "Billing failed for %s/%s: %s",
                    chat_id, platform, exc,
                )

    LOG.info(
        "Billing complete: %d invoices, %d skipped, %d errors "
        "(period: %s → %s)",
        len(invoices), skipped, errors, period_start, period_end,
    )

    return invoices


# ═══════════════════════════════════════════════════════════════════
#  MEMBER LOADING
# ═══════════════════════════════════════════════════════════════════


async def _load_premium_chat_ids() -> list[str]:
    """Fetch all premium chat_ids from the members database."""
    import sqlite3
    from pathlib import Path

    db_path = (
        Path(settings.DATA_DIR) / "vilona_tradefx" / "members.db"
    )

    def _query() -> list[str]:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT chat_id, status, tags, expiry FROM members"
            ).fetchall()
            conn.close()

            now = datetime.now(WIB)
            premium: list[str] = []
            for row in rows:
                cid = row["chat_id"]
                status = row.get("status", "")
                tags = row.get("tags", "")

                if "test" in (tags or ""):
                    continue

                if status != "paid":
                    continue

                # Check expiry
                try:
                    expiry_str = row.get("expiry", "")
                    if expiry_str:
                        exp = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                        if exp.tzinfo is None:
                            exp = exp.replace(tzinfo=WIB)
                        if exp < now:
                            continue
                except (ValueError, TypeError):
                    pass

                premium.append(cid)
            return premium
        except Exception as exc:
            LOG.warning("_load_premium_chat_ids failed: %s", exc)
            return []

    return await asyncio.get_event_loop().run_in_executor(None, _query)


# ═══════════════════════════════════════════════════════════════════
#  BROKER TRADE FETCHING
# ═══════════════════════════════════════════════════════════════════


async def _fetch_broker_trades(
    chat_id: str,
    platform: str,
    period_start: str,
    period_end: str,
) -> list[TradeRecord]:
    """Fetch closed trades from the user's broker for the given period.

    The AccountingService filters by Vilona tag, so we fetch ALL trades
    here and let filter_vilona_trades() isolate the tagged ones.

    Note: This is the integration point for real broker APIs.
    Currently returns an empty list — wire up broker-specific fetching
    once the broker history APIs are available.
    """
    from pathlib import Path

    # ── MT5 path ──────────────────────────────────────────────────
    if platform == "mt5":
        return await _fetch_mt5_trades(chat_id, period_start, period_end)

    # ── CCXT path ─────────────────────────────────────────────────
    if platform == "ccxt":
        return await _fetch_ccxt_trades(chat_id, period_start, period_end)

    LOG.warning("Unknown platform: %s", platform)
    return []


async def _fetch_mt5_trades(
    chat_id: str,
    since: str,
    until: str,
) -> list[TradeRecord]:
    """Fetch MT5 history for Vilona-tagged trades.

    Uses mt5.history_deals_get() filtered by the Gotong Royong magic
    number (7771041). Requires MetaTrader5 Python package and a running
    terminal connection.
    """
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError:
        LOG.warning("MetaTrader5 not installed — skipping MT5 trade fetch for %s", chat_id)
        return []

    from datetime import datetime as dt

    since_dt = dt.fromisoformat(since)
    until_dt = dt.fromisoformat(until)

    magic = settings.GOTONG_ROYONG_MAGIC_NUMBER

    def _fetch():
        deals = mt5.history_deals_get(since_dt, until_dt)
        if not deals:
            return []

        trades: list[TradeRecord] = []
        for deal in deals:
            # MT5 deal tuple: (ticket, order, time, type, entry, magic, ...)
            deal_magic = deal[5] if len(deal) > 5 else 0
            if deal_magic != magic:
                continue

            # type: 0=buy, 1=sell
            deal_type = deal[3] if len(deal) > 3 else -1
            side = "buy" if deal_type == 0 else "sell"

            trades.append(TradeRecord(
                order_id=str(deal[0]),
                symbol=str(deal[4]) if len(deal) > 4 else "",
                side=side,
                entry_price=float(deal[6]) if len(deal) > 6 else 0.0,
                exit_price=0.0,  # Deal price is exit in single-deal
                amount=float(deal[9]) if len(deal) > 9 else 0.0,
                fee=float(deal[11]) if len(deal) > 11 else 0.0,
                realized_pnl=float(deal[12]) if len(deal) > 12 else 0.0,
                closed_at=str(deal[2]) if len(deal) > 2 else "",
                identifier=str(deal_magic),
            ))

        return trades

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def _fetch_ccxt_trades(
    chat_id: str,
    since: str,
    until: str,
) -> list[TradeRecord]:
    """Fetch CCXT trade history for Vilona-tagged trades.

    Uses the user's broker instance to call fetch_my_trades() or
    fetch_closed_orders(), filtering by clientOrderId prefix.
    """
    from datetime import datetime as dt

    since_ts = int(dt.fromisoformat(since).timestamp() * 1000)
    until_ts = int(dt.fromisoformat(until).timestamp() * 1000)
    prefix = settings.GOTONG_ROYONG_CCXT_PREFIX

    try:
        from tradebot.brokers.user_broker_factory import get_user_broker

        broker = await get_user_broker(chat_id, "ccxt", for_execution=False)
        if broker is None or not hasattr(broker, "_client") or broker._client is None:
            LOG.debug("No CCXT broker available for %s — skipping", chat_id)
            return []

        # Fetch all trades for the period
        raw_trades = await broker._client.fetch_my_trades(
            symbol=None,  # All symbols
            since=since_ts,
            params={"until": until_ts} if until_ts else {},
        )

        trades: list[TradeRecord] = []
        for t in raw_trades:
            oid = t.get("info", {}).get("clientOrderId", t.get("clientOrderId", ""))
            if not oid or not oid.startswith(prefix):
                continue

            trades.append(TradeRecord(
                order_id=str(t.get("id", t.get("order", ""))),
                symbol=t.get("symbol", ""),
                side=t.get("side", ""),
                entry_price=float(t.get("price", 0)),
                exit_price=float(t.get("price", 0)),  # Market trades: price=execution
                amount=float(t.get("amount", 0)),
                fee=float(t.get("fee", {}).get("cost", 0)) if isinstance(t.get("fee"), dict) else 0.0,
                realized_pnl=float(t.get("info", {}).get("realizedPnl", 0)),
                closed_at=t.get("datetime", ""),
                identifier=oid,
            ))

        if broker:
            await broker.close()
        return trades
    except Exception as exc:
        LOG.warning("CCXT trade fetch failed for %s: %s", chat_id, exc)
        return []


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════


def main() -> int:
    """Run the weekly billing cycle and print results as JSON lines."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Gotong Royong Weekly Billing Cron"
    )
    parser.add_argument(
        "--db", type=str, default="",
        help="Path to tradebot.db",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run billing without inserting ledger records",
    )
    parser.add_argument(
        "--json", action="store_true", default=True,
        help="Output JSON lines (default)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    invoices = asyncio.run(run_billing_cycle(
        db_path=args.db,
        dry_run=args.dry_run,
    ))

    if args.json:
        for inv in invoices:
            print(json.dumps(inv, ensure_ascii=False, default=str))
    else:
        for inv in invoices:
            print(inv["telegram_message"], "\n")

    if not invoices:
        print("No fees due this week.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
