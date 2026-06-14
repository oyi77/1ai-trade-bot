"""
vilona_dispatcher.py — VilonaSignalDispatcher — The Unified Signal Router

Deprecates Paths A-E. Single entry point after VilonaMetaOrchestrator for
tiered Gotong Royong routing:

  Tier 1 — Public Showroom (TELEGRAM_CHAT_ID)
    Masked teaser for STRONG/MODERATE or PRZ_Active signals.
    No entry/SL/TP exposed — FOMO only.

  Tier 2 — Free DM Trial (trial members)
    Full signal via individual Telegram DM. No execution.

  Tier 3 — Premium Auto-Copytrade (paid members)
    Full signal DM + concurrent per-user broker auto-execution via
    UserBrokerFactory + TradeExecutor. Failures are isolated per user.

Token safety: single httpx.AsyncClient for all Telegram calls — no
competing getUpdates connections, no 409 Conflict risk.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from tradebot.config import settings
from tradebot.models import Signal

LOG = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

# ═══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

# Grades that qualify for public showroom teaser
_SHOWROOM_GRADES = frozenset({"STRONG", "MODERATE"})

# Default cooldown between dispatches of the same symbol (seconds)
_DEFAULT_COOLDOWN_S = 120

# Default max concurrent broker connections per dispatch
_DEFAULT_MAX_CONCURRENT_BROKERS = 20

# Default Telegram send timeout per message
_TG_TIMEOUT_S = 8.0


# ═══════════════════════════════════════════════════════════════════════
#  DELIVERY RESULT
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DispatchResult:
    """Full result of a dispatch cycle."""

    signal_id: str = ""
    symbol: str = ""
    direction: str = ""
    grade: str = ""
    ts: str = ""

    # Tier 1
    showroom_sent: bool = False

    # Tier 2
    trial_total: int = 0
    trial_sent: int = 0
    trial_failed: int = 0

    # Tier 3
    premium_total: int = 0
    premium_dm_sent: int = 0
    premium_executed: int = 0
    premium_exec_failed: int = 0
    premium_disabled: list[str] = field(default_factory=list)

    errors: list[str] = field(default_factory=list)

    @property
    def total_delivered(self) -> int:
        return (
            (1 if self.showroom_sent else 0)
            + self.trial_sent
            + self.premium_dm_sent
            + self.premium_executed
        )


# ═══════════════════════════════════════════════════════════════════════
#  VILONA SIGNAL DISPATCHER
# ═══════════════════════════════════════════════════════════════════════


class VilonaSignalDispatcher:
    """Unified multi-tier signal dispatcher.

    Single replacement for the 5 scattered delivery paths (TelegramService,
    SignalPublisher, broadcast_signal_result, VilonaSignalBridge, and
    auto_analysis_loop).

    Usage::

        dispatcher = VilonaSignalDispatcher()
        result = await dispatcher.dispatch(signal)
        LOG.info("Dispatched to %d users", result.total_delivered)
    """

    def __init__(
        self,
        *,
        bot_token: str = "",
        public_chat_id: str | int = "",
        cooldown_s: int = _DEFAULT_COOLDOWN_S,
        max_concurrent_brokers: int = _DEFAULT_MAX_CONCURRENT_BROKERS,
        tg_timeout_s: float = _TG_TIMEOUT_S,
    ):
        self._bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self._public_chat = str(public_chat_id or settings.TELEGRAM_CHAT_ID or "")
        self._cooldown_s = cooldown_s
        self._max_brokers = max_concurrent_brokers
        self._tg_timeout = tg_timeout_s

        # Shared httpx client for ALL Telegram calls — prevents 409 conflicts
        self._http: httpx.AsyncClient | None = None

        # Simple dedup — last dispatch time per symbol
        self._last_dispatch: dict[str, float] = {}

    async def _ensure_http(self) -> httpx.AsyncClient:
        """Lazy-init the shared httpx client."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._tg_timeout,
                limits=httpx.Limits(max_keepalive_connections=50),
            )
        return self._http

    async def close(self) -> None:
        """Release resources."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── PUBLIC API ───────────────────────────────────────────────────

    async def dispatch(
        self,
        signal: Signal,
    ) -> DispatchResult:
        """Route a signal through all three tiers.

        Deduplicates by symbol within the cooldown window.
        All delivery happens concurrently where possible.
        """
        from tradebot.models import SignalGrade

        symbol = signal.symbol
        direction = signal.direction
        grade = signal.grade.name if hasattr(signal.grade, "name") else str(signal.grade)
        meta = signal.metadata

        # Dedup
        now_ts = datetime.now(WIB).timestamp()
        last = self._last_dispatch.get(symbol, 0)
        if now_ts - last < self._cooldown_s:
            LOG.debug("Dispatcher: %s cooldown — skipped", symbol)
            return DispatchResult(
                signal_id=meta.get("signal_id", ""),
                symbol=symbol, direction=direction, grade=grade,
                ts=datetime.now(WIB).isoformat(),
            )
        self._last_dispatch[symbol] = now_ts

        LOG.info(
            "Dispatcher: routing %s %s grade=%s conf=%.0f%%",
            direction, symbol, grade, signal.confidence,
        )

        result = DispatchResult(
            signal_id=meta.get("signal_id", ""),
            symbol=symbol, direction=direction, grade=grade,
            ts=datetime.now(WIB).isoformat(),
        )

        # Ensure HTTP client is alive
        await self._ensure_http()

        # ── Tier 1: Public Showroom ────────────────────────────────
        showroom_eligible = (
            grade.upper() in _SHOWROOM_GRADES
            or meta.get("PRZ_Active") is True
        )
        if showroom_eligible:
            result.showroom_sent = await self._send_showroom_teaser(signal)

        # ── Fetch all members ──────────────────────────────────────
        trial_users, premium_users = await self._load_members()

        # ── Tier 2: Free DM Trial ──────────────────────────────────
        result.trial_total = len(trial_users)
        if trial_users:
            full_msg = self._format_full_signal(signal)
            dm_results = await self._send_bulk_dm(trial_users, full_msg)
            result.trial_sent = sum(1 for ok in dm_results if ok)
            result.trial_failed = result.trial_total - result.trial_sent

        # ── Tier 3: Premium Auto-Copytrade ─────────────────────────
        result.premium_total = len(premium_users)
        if premium_users:
            result = await self._dispatch_premium(signal, premium_users, result)

        LOG.info(
            "Dispatcher done: showroom=%s trial=%d/%d prem=%d/%d exec=%d/%d",
            result.showroom_sent,
            result.trial_sent, result.trial_total,
            result.premium_dm_sent, result.premium_total,
            result.premium_executed, result.premium_total,
        )

        return result

    # ── TIER 1: PUBLIC SHOWROOM ───────────────────────────────────────

    async def _send_showroom_teaser(self, signal: Signal) -> bool:
        """Send a masked teaser to the public channel. No entry/SL/TP."""
        symbol = signal.symbol
        direction = signal.direction
        grade = signal.grade.name if hasattr(signal.grade, "name") else str(signal.grade)
        meta = signal.metadata
        prZ_active = meta.get("PRZ_Active", False)
        pattern = meta.get("pattern", meta.get("gate_reason", ""))

        emoji = "🟢" if direction.upper() in ("BULLISH", "BUY", "CALL") else "🔴"

        if prZ_active:
            headline = f"🚨 VILONA AI IS HUNTING {symbol}"
            lines = [
                f"{emoji} <b>{headline}</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
                f"🎯 Pattern: <b>{pattern or 'PRZ Validated'}</b>",
                f"📊 Direction: <b>{direction.upper()}</b>",
                "",
                "⚡ <b>PRZ ZONE ACTIVE — AI agents deployed.</b>",
                "Pro members receive full entry + auto-execution.",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "💳 <b>Subscribe →</b> /subscribe",
            ]
        else:
            headline = f"VILONA AI SIGNAL — {symbol}"
            lines = [
                f"{emoji} <b>{headline}</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
                f"📊 Direction: <b>{direction.upper()}</b>",
                f"🏆 Grade: <b>{grade}</b>",
                "",
                "⚡ <b>AI consensus signal detected.</b>",
                "Full entry/SL/TP → upgrade to Premium.",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "💳 <b>Subscribe →</b> /subscribe",
            ]

        text = "\n".join(lines)
        return await self._tg_send(self._public_chat, text)

    # ── TIER 2: FREE DM TRIAL ─────────────────────────────────────────

    async def _send_bulk_dm(
        self, chat_ids: list[str], text: str
    ) -> list[bool]:
        """Send a message to multiple chat_ids concurrently."""
        tasks = [self._tg_send(cid, text) for cid in chat_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            isinstance(r, bool) and r
            for r in results
        ]

    # ── TIER 3: PREMIUM AUTO-COPYTRADE ─────────────────────────────────

    async def _dispatch_premium(
        self,
        signal: Signal,
        chat_ids: list[str],
        result: DispatchResult,
    ) -> DispatchResult:
        """Send full DM + execute trades for all premium users.

        Error isolation: one user's broker failure does not block others.
        Semaphore caps concurrent broker connections.
        """
        full_msg = self._format_full_signal(signal)

        # Phase 1: DM all premium users
        dm_results = await self._send_bulk_dm(chat_ids, full_msg)
        result.premium_dm_sent = sum(1 for ok in dm_results if ok)

        # Phase 2: Auto-execute for users with linked brokers
        sem = asyncio.Semaphore(self._max_brokers)
        exec_tasks = [self._execute_for_user(signal, cid, sem) for cid in chat_ids]
        exec_results = await asyncio.gather(*exec_tasks, return_exceptions=True)

        for i, exec_r in enumerate(exec_results):
            if isinstance(exec_r, Exception):
                result.premium_exec_failed += 1
                result.errors.append(f"{chat_ids[i]}: {exec_r}")
            elif exec_r is True:
                result.premium_executed += 1
            elif exec_r is False:
                result.premium_exec_failed += 1
            elif exec_r is None:
                # No broker linked — not a failure, just skip
                result.premium_disabled.append(chat_ids[i])

        return result

    async def _execute_for_user(
        self,
        signal: Signal,
        chat_id: str,
        sem: asyncio.Semaphore,
    ) -> bool | None:
        """Execute a trade for one premium user.

        Returns:
            True  — trade executed successfully
            False — execution failed (broker error, no credentials, etc.)
            None  — user has no broker linked (skip silently)
        """
        from tradebot.brokers.user_broker_factory import get_user_broker
        from tradebot.pipeline.trade_executor import TradeExecutor

        async with sem:
            # Determine which platform to use — try stockity first, then any linked
            platforms = await self._get_user_platforms(chat_id)
            if not platforms:
                return None  # No broker linked

            # Try each platform in order until one succeeds
            for platform in platforms:
                try:
                    broker = await get_user_broker(chat_id, platform, for_execution=True)
                    if broker is None:
                        continue

                    executor = TradeExecutor(broker=broker)
                    trade_result = await executor.execute(signal)
                    await broker.close()

                    if trade_result is not None:
                        LOG.info(
                            "Dispatcher: executed %s for user %s via %s — "
                            "profit=%.2f",
                            signal.symbol, chat_id, platform,
                            trade_result.profit,
                        )
                        return True
                except Exception as exc:
                    LOG.warning(
                        "Dispatcher: trade failed for user %s via %s: %s",
                        chat_id, platform, exc,
                    )
                    continue

            return False

    # ── MEMBER LOADING ──────────────────────────────────────────────

    async def _load_members(self) -> tuple[list[str], list[str]]:
        """Load members from SQLite, split into trial and premium.

        Uses a thread to avoid blocking the event loop with synchronous
        SQLite calls (members/__init__.py uses raw sqlite3 which is
        synchronous).
        """
        import sqlite3
        from pathlib import Path

        DB_PATH = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx" / "members.db"

        def _query():
            trial: list[str] = []
            premium: list[str] = []
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT chat_id, status, tags, expiry FROM members"
                ).fetchall()
                conn.close()

                now = datetime.now(WIB)
                for row in rows:
                    cid = row["chat_id"]
                    status = row.get("status", "")
                    tags = row.get("tags", "")
                    expiry_str = row.get("expiry", "")

                    # Skip test accounts
                    if "test" in (tags or ""):
                        continue

                    if status == "paid":
                        # Check expiry
                        try:
                            exp = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                            if exp.tzinfo is None:
                                exp = exp.replace(tzinfo=WIB)
                            if exp < now:
                                trial.append(cid)  # expired → trial
                                continue
                        except (ValueError, TypeError):
                            pass
                        premium.append(cid)
                    else:
                        trial.append(cid)
            except Exception as exc:
                LOG.warning("load_members failed: %s", exc)
            return trial, premium

        return await asyncio.get_event_loop().run_in_executor(None, _query)

    # ── BROKER PLATFORM DISCOVERY ──────────────────────────────────

    async def _get_user_platforms(self, chat_id: str) -> list[str]:
        """Return platforms linked by a user (ordered by priority).

        Uses PlatformLinkService for decryption transparency.
        """
        from tradebot.services.platform_link_service import PlatformLinkService

        svc = PlatformLinkService()
        linked = await svc.get_linked_platforms(chat_id)

        # Prioritize: stockity > deriv > ccxt > mt5
        order = {"stockity": 0, "deriv": 1, "ccxt": 2, "mt5": 3}
        platforms = [
            p["platform"] for p in linked
            if p.get("credentials") and p["credentials"] != "{}"
        ]
        platforms.sort(key=lambda p: order.get(p, 99))
        return platforms

    # ── TELEGRAM SEND UTILITY ──────────────────────────────────────

    async def _tg_send(self, chat_id: str, text: str) -> bool:
        """Send one Telegram message via the shared httpx client."""
        if not self._bot_token or not chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
            resp = await (await self._ensure_http()).post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            if resp.status_code != 200:
                LOG.debug("TG send failed to %s: %s", chat_id, resp.text[:120])
                return False
            return True
        except Exception as exc:
            LOG.debug("TG send error to %s: %s", chat_id, exc)
            return False

    # ── SIGNAL FORMATTERS ──────────────────────────────────────────

    def _format_full_signal(self, signal: Signal) -> str:
        """Format a full signal for DM delivery (Tier 2 + Tier 3)."""
        symbol = signal.symbol
        direction = signal.direction
        emoji = "🟢" if direction.upper() in ("BULLISH", "BUY", "CALL") else "🔴"
        meta = signal.metadata

        entry = signal.entry_price or meta.get("entry_price", 0)
        sl = meta.get("sl", 0)
        tp1 = meta.get("tp1", 0)
        tp2 = meta.get("tp2", 0)
        rr = meta.get("rr", 0)
        grade = signal.grade.name if hasattr(signal.grade, "name") else str(signal.grade)
        confidence = signal.confidence
        orch = meta.get("orchestrator_verdict", {})
        resolution = orch.get("resolution_path", "") if isinstance(orch, dict) else ""
        macro_trend = meta.get("macro_trend", "")
        prZ = meta.get("PRZ_Active", False)
        pattern = meta.get("pattern", "")

        lines = [
            f"{emoji} <b>VILONA SIGNAL — {direction.upper()} {symbol}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]

        if prZ and pattern:
            lines.append(f"🎯 Pattern: <b>{pattern.upper()}</b>")

        lines.extend([
            f"💰 Entry: <b>${entry:.2f}</b>" if entry else "",
            f"🛑 SL: <b>${sl:.2f}</b>" if sl else "",
            f"✅ TP1: <b>${tp1:.2f}</b>" if tp1 else "",
            f"✅ TP2: <b>${tp2:.2f}</b>" if tp2 else "",
            f"📊 RR: <b>1:{rr}</b>" if rr else "",
            f"🏆 Grade: <b>{grade}</b> | ⚡ {confidence:.0%}",
        ])

        if macro_trend:
            lines.append(f"🏛 Macro: <b>{macro_trend.upper()}</b>")
        if resolution:
            lines.append(f"🧠 {resolution}")

        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"⏰ {datetime.now(WIB).strftime('%H:%M WIB')}",
            "⚡ Powered by Vilona AI",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ])

        return "\n".join(line for line in lines if line)
