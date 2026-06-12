"""
Unified Application Orchestrator — single entry point for everything.

Starts:
  - FastAPI web server (dashboard + API + webhooks)
  - Telegram bot (multi-platform, single PTB instance)
  - Background services (health, watchdog, signal scanning)

Usage:
  python -m tradebot              # Start everything
  python -m tradebot --web-only   # Web dashboard only
  python -m tradebot --bot-only   # Telegram bot only
  python -m tradebot --port 8889  # Custom port
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys

import uvicorn

LOG = logging.getLogger("tradebot.app")


class App:
    """Orchestrates all subsystems."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8889):
        self.host = host
        self.port = port
        self._bot_task: asyncio.Task | None = None
        self._running = False

    # ── Start ───────────────────────────────────────────────────────

    async def start(self, web: bool = True, bot: bool = True) -> None:
        """Start all subsystems."""
        self._running = True
        self._assert_payment_webhooks_configured()
        LOG.info("🚀 Starting 1ai-trade-bot...")

        tasks = []

        if bot:
            LOG.info("📡 Starting Telegram bot...")
            self._bot_task = asyncio.create_task(self._run_telegram())
            tasks.append(self._bot_task)

        if web:
            LOG.info("🌐 Starting web dashboard on %s:%s...", self.host, self.port)
            tasks.append(asyncio.create_task(self._run_web()))

        if not tasks:
            LOG.warning("Nothing to start (web=False, bot=False)")

        import contextlib
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _assert_payment_webhooks_configured() -> None:
        """Fail fast if payment callback URLs are not configured."""
        from tradebot.config import settings
        missing = [
            name
            for name, value in [
                ("TRIPAY_CALLBACK_URL", settings.TRIPAY_CALLBACK_URL),
                ("DUITKU_CALLBACK_URL", settings.DUITKU_CALLBACK_URL),
            ]
            if not value
        ]
        if missing:
            raise SystemExit(
                f"Payment webhook not configured: {', '.join(missing)}. "
                "Set them in .env before starting the service."
            )

    async def _run_web(self) -> None:
        """Run FastAPI via uvicorn."""
        config = uvicorn.Config(
            "tradebot.web.server:app",
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()

    _BOT_LOCK = "/tmp/vilona_bot.lock"

    async def _run_telegram(self) -> None:
        """Run unified Telegram bot — PID-locked to prevent duplicate polling."""
        if os.path.exists(self._BOT_LOCK):
            try:
                with open(self._BOT_LOCK) as f:
                    old_pid = int(f.read().strip())
                os.kill(old_pid, 0)  # signal 0 = check if alive
                LOG.warning("⚠️ Bot already running (PID %s) — SKIPPING duplicate", old_pid)
                return
            except (OSError, ValueError):
                with contextlib.suppress(OSError):
                    os.remove(self._BOT_LOCK)
        with open(self._BOT_LOCK, "w") as f:
            f.write(str(os.getpid()))
        try:
            from tradebot.bots.telegram import UnifiedBot
            bot = UnifiedBot()
            await bot.start()
        finally:
            with contextlib.suppress(OSError):
                os.remove(self._BOT_LOCK)

    def shutdown(self) -> None:
        """Graceful shutdown."""
        LOG.info("🛑 Shutting down...")
        self._running = False
        if self._bot_task:
            self._bot_task.cancel()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="1ai-trade-bot — Unified Trading Platform")
    parser.add_argument("--host", default="0.0.0.0", help="Web server bind host")
    parser.add_argument("--port", type=int, default=8889, help="Web server port")
    parser.add_argument("--web-only", action="store_true", help="Only start web dashboard")
    parser.add_argument("--bot-only", action="store_true", help="Only start Telegram bot")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = App(host=args.host, port=args.port)

    def _handle_signal(sig, frame):
        app.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    web = not args.bot_only
    bot = not args.web_only

    asyncio.run(app.start(web=web, bot=bot))


if __name__ == "__main__":
    main()
