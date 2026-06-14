"""
POST /api/webhook/trade-close — Real-time MT5 EA trade-close webhook.

Receives HMAC-signed trade events from user EAs, stores them in
trade_log, and sends an instant Telegram DM to the user via a
background task (HTTP 200 OK returned immediately to the EA).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tradebot.api.webhook_auth import verify_vilona_webhook
from tradebot.config import settings

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

WIB = timezone.utc  # Webhooks use UTC for interop


# ═══════════════════════════════════════════════════════════════════
#  REQUEST MODEL
# ═══════════════════════════════════════════════════════════════════


class TradeCloseEvent(BaseModel):
    """Payload pushed by the MT5 EA when a Vilona-tagged trade closes."""

    chat_id: str = Field(..., description="Telegram chat_id of the user")
    platform: str = Field(default="mt5", description="Broker platform")
    symbol: str = Field(..., description="Trading symbol (e.g. XAUUSD)")
    ticket: str = Field(..., description="MT5 ticket / order ID")
    pnl: float = Field(..., description="Realized P&L in account currency")
    magic: str = Field(default="7771041", description="EA magic number")
    closed_at: str = Field(default="", description="ISO 8601 close timestamp")


# ═══════════════════════════════════════════════════════════════════
#  ROUTE
# ═══════════════════════════════════════════════════════════════════


@router.post("/trade-close")
async def webhook_trade_close(
    request: Request,
    background_tasks: BackgroundTasks,
    body_raw: str = Depends(verify_vilona_webhook),
) -> JSONResponse:
    """Receive a trade-close event from a Vilona-tagged EA.

    Flow:
        1. HMAC signature validated by verify_vilona_webhook dependency
        2. Parse JSON body into TradeCloseEvent
        3. INSERT into trade_log (synchronous, fast — SQLite WAL mode)
        4. Schedule background Telegram DM to user
        5. Return 200 OK to EA immediately
    """
    try:
        event = TradeCloseEvent.model_validate_json(body_raw)
    except Exception as exc:
        LOG.warning("Webhook trade-close parse failed: %s", exc)
        return JSONResponse({"ok": False, "error": "Invalid payload"}, status_code=400)

    # Normalize
    if not event.closed_at:
        event.closed_at = datetime.now(WIB).isoformat()

    # ── Store in trade_log ──────────────────────────────────────────
    db_path = _get_tradebot_db_path()
    try:
        _insert_trade_log(db_path, event)
    except Exception as exc:
        LOG.error("trade_log insert failed: %s", exc)
        return JSONResponse({"ok": False, "error": "DB write failed"}, status_code=500)

    # ── Background: notify user via Telegram ────────────────────────
    background_tasks.add_task(_notify_user, event)

    LOG.info(
        "Webhook trade-close: uid=%s symbol=%s pnl=%.2f ticket=%s",
        event.chat_id, event.symbol, event.pnl, event.ticket,
    )

    return JSONResponse({"ok": True, "ticket": event.ticket})


# ═══════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════


def _get_tradebot_db_path() -> Path:
    override = settings.STORAGE_DB_PATH
    if override:
        return Path(override)
    return Path(settings.DATA_DIR) / "tradebot.db"


def _ensure_trade_log_schema(db_path: Path) -> None:
    """Create the trade_log table if it does not exist."""
    import sqlite3

    migration_path = (
        Path(__file__).resolve().parent.parent
        / "db" / "migrations" / "002_create_trade_log.sql"
    )
    conn = sqlite3.connect(str(db_path))
    try:
        if migration_path.exists():
            conn.executescript(migration_path.read_text())
        conn.commit()
    finally:
        conn.close()


def _insert_trade_log(db_path: Path, event: TradeCloseEvent) -> None:
    """Insert a trade-close event into trade_log (synchronous)."""
    import sqlite3

    _ensure_trade_log_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT OR IGNORE INTO trade_log
               (chat_id, platform, symbol, ticket_id, magic_number,
                pnl, closed_at, processed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                event.chat_id,
                event.platform,
                event.symbol,
                event.ticket,
                event.magic,
                event.pnl,
                event.closed_at,
                datetime.now(WIB).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def _notify_user(event: TradeCloseEvent) -> None:
    """Send a real-time Telegram DM to the user about their closed trade.

    Runs as a background task — failures are logged, not returned to EA.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        LOG.debug("No TELEGRAM_BOT_TOKEN — skipping user notification")
        return

    emoji = "🟢" if event.pnl > 0 else ("🔴" if event.pnl < 0 else "⚪")
    pnl_str = f"+${event.pnl:,.2f}" if event.pnl >= 0 else f"-${abs(event.pnl):,.2f}"

    text = (
        f"🔥 <b>Real-Time Update</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} Vilona AI closed <b>{event.symbol}</b>\n"
        f"💰 PnL: <b>{pnl_str}</b>\n"
        f"🎫 Ticket: <code>{event.ticket}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Powered by Vilona AI Trade Engine"
    )

    try:
        import httpx
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": event.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            if resp.status_code != 200:
                LOG.warning(
                    "TG notification failed for %s: %s",
                    event.chat_id, resp.text[:120],
                )
    except Exception as exc:
        LOG.warning("TG notification error for %s: %s", event.chat_id, exc)


# ═══════════════════════════════════════════════════════════════════
#  PUBLIC API — recent trades for live dashboard
# ═══════════════════════════════════════════════════════════════════


@router.get("/trade-log")
async def get_trade_log(chat_id: str = "", limit: int = 20):
    """Get recent trade-close events for the live dashboard."""
    import sqlite3

    db_path = _get_tradebot_db_path()
    _ensure_trade_log_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if chat_id:
            rows = conn.execute(
                """SELECT * FROM trade_log
                   WHERE chat_id = ?
                   ORDER BY closed_at DESC LIMIT ?""",
                (chat_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trade_log ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {"trades": [dict(r) for r in rows]}
    finally:
        conn.close()
