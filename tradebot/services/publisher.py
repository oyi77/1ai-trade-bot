"""SignalPublisher — Cron-based signal scanner & publisher.

Runs every N minutes: scans MTF matrix, generates signal, posts to
Telegram channel and local bridge server, with dedup logic.

Absorbed from scripts/auto_signal_publisher.py.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx

from tradebot.config import settings
from tradebot.services.telegram import TelegramService

LOG = logging.getLogger("tradebot.services.publisher")


class SignalPublisher:
    """Cron-based signal scanner and publisher.

    Scans assets using engine consensus, deduplicates against recent
    signals, and publishes to Telegram channel + bridge server.
    """

    def __init__(
        self,
        telegram: TelegramService | None = None,
        bridge_url: str = "",
        trade_log_path: str | Path | None = None,
        scan_interval: int = 0,
    ):
        self.telegram = telegram or TelegramService()
        self.bridge_url = bridge_url or settings.BRIDGE_URL
        self.trade_log_path = Path(
            trade_log_path or settings.PUBLISHER_TRADE_LOG
        )
        self.scan_interval = scan_interval or settings.PUBLISHER_SCAN_INTERVAL

    def load_trade_log(self) -> list[dict]:
        """Load recent trade log for dedup checking."""
        try:
            if self.trade_log_path.exists():
                with open(self.trade_log_path) as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("Failed to load trade log: %s", exc)
        return []

    def is_duplicate(self, log: list[dict], signal: dict) -> bool:
        """Check if signal was already posted (dedup by symbol + entry + time gap).

        Uses price threshold: $1.00 for crypto, $0.30 for forex.
        Minimum 60 minutes between same-symbol signals.
        """
        entry = signal.get("entry", 0)
        action = signal.get("action", "")
        symbol = signal.get("symbol", "")
        now = time.time()

        for s in log[-20:]:
            threshold = 1.0 if symbol in ("BTCUSD",) else 0.30
            if (
                s.get("symbol") == symbol
                and s.get("action") == action
                and abs(s.get("entry", 0) - entry) < threshold
            ):
                try:
                    t1 = datetime.fromisoformat(
                        s.get("timestamp", "")
                    ).timestamp()
                    if now - t1 < 3600:
                        return True
                except (ValueError, TypeError):
                    return True
        return False

    async def post_to_bridge(self, signal: dict) -> bool:
        """Post signal to local bridge server via HTTP."""
        if not self.bridge_url:
            return False
        try:
            bridge_sig = dict(signal)
            bridge_sig["tp"] = signal.get("tp1", 0)
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    self.bridge_url,
                    json=bridge_sig,
                )
                return resp.status_code == 200
        except Exception as exc:
            LOG.debug("Bridge post failed: %s", exc)
            return False

    async def post_to_telegram(self, text: str) -> bool:
        """Post formatted signal text to Telegram channel."""
        return await self.telegram.send_message(text)

    async def publish_signal(
        self, signal: dict, force: bool = False
    ) -> bool:
        """Publish a single signal: dedup check, post to bridge + Telegram.

        Args:
            signal: Signal dict with keys like symbol, action, entry, grade, tp1.
            force: If True, skip dedup check.

        Returns:
            True if signal was published.
        """
        if not force:
            trade_log = self.load_trade_log()
            if self.is_duplicate(trade_log, signal):
                LOG.info(
                    "Duplicate signal — skipped: %s %s",
                    signal.get("symbol"),
                    signal.get("action"),
                )
                return False

        symbol = signal.get("symbol", "?")
        action = signal.get("action", "?")
        grade = signal.get("grade", "?")
        entry = signal.get("entry", 0)

        # Format for Telegram
        text = self._format_signal(signal)
        tg_ok = await self.post_to_telegram(text)
        bridge_ok = await self.post_to_bridge(signal)

        if tg_ok or bridge_ok:
            LOG.info(
                "Published %s %s @ $%s Grade %s (tg=%s, bridge=%s)",
                action, symbol, entry, grade, tg_ok, bridge_ok,
            )
            return True
        LOG.warning("All publish methods failed for %s %s", action, symbol)
        return False

    @staticmethod
    def _format_signal(signal: dict) -> str:
        """Format signal dict as HTML for Telegram."""
        action = signal.get("action", "?")
        symbol = signal.get("symbol", "?")
        entry = signal.get("entry", 0)
        grade = signal.get("grade", "?")
        confidence = signal.get("confidence", 0)
        emoji = (
            "🟢" if action == "BUY"
            else ("🔴" if action == "SELL" else "⚪")
        )
        lines = [
            f"<b>{emoji} Trading Signal</b>",
            "",
            f"🎯 <b>Symbol:</b> {symbol}",
            f"📊 <b>Action:</b> {action}",
            f"💰 <b>Entry:</b> {entry}",
            f"🏆 <b>Grade:</b> {grade}",
        ]
        if confidence:
            lines.append(f"📈 <b>Confidence:</b> {confidence:.0f}%")
        tp1 = signal.get("tp1")
        sl = signal.get("sl")
        if tp1:
            lines.append(f"🎯 <b>TP:</b> {tp1}")
        if sl:
            lines.append(f"🛡️ <b>SL:</b> {sl}")
        lines.append(
            f"⏰ {datetime.now(datetime.UTC).strftime('%H:%M UTC')}"
        )
        return "\n".join(lines)
