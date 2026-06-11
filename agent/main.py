"""
Main entry point — wires Telegram Layer + Core Logic + Background Loops + Web Dashboard.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).resolve().parent.parent / "logs" / "agent_bot.log"),
        logging.StreamHandler(sys.stderr),
    ],
)

LOG = logging.getLogger("agent.main")


def _run_web(port: int):
    from agent.web.server import app
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


from agent.database import init_db
from agent.core import (
    cmd_start, cmd_signal, cmd_price, cmd_status, cmd_myid, cmd_donate,
    cmd_levels, cmd_news, cmd_zones, cmd_structure, cmd_session,
    cmd_killzone, cmd_help, cmd_stockity, cmd_analyze, cmd_data,
    cmd_genkey, cmd_mykey, cmd_winrate, cmd_history, cmd_recap, cmd_mapping,
    auto_analysis_loop, daily_recap_broadcast,
)
from agent.telegram_layer import TelegramLayer


async def main():
    init_db()
    LOG.info("Database initialized")

    tl = TelegramLayer()

    for cmd, handler in [
        ("start", cmd_start), ("help", cmd_help),
        ("signal", cmd_signal), ("price", cmd_price),
        ("status", cmd_status), ("myid", cmd_myid),
        ("donate", cmd_donate), ("levels", cmd_levels),
        ("news", cmd_news), ("zones", cmd_zones),
        ("structure", cmd_structure), ("session", cmd_session),
        ("killzone", cmd_killzone), ("stockity", cmd_stockity),
        ("analyze", cmd_analyze), ("data", cmd_data),
        ("genkey", cmd_genkey), ("mykey", cmd_mykey),
        ("winrate", cmd_winrate), ("history", cmd_history),
        ("recap", cmd_recap), ("mapping", cmd_mapping),
    ]:
        tl.register_command(cmd, handler)

    async def bg_wrapper():
        try:
            await auto_analysis_loop(tl)
        except Exception as e:
            LOG.warning("Auto-analysis loop stopped: %s", e)

    async def recap_wrapper():
        try:
            await daily_recap_broadcast(tl)
        except Exception as e:
            LOG.warning("Daily recap loop stopped: %s", e)

    tg_task = asyncio.create_task(tl.start())
    bg_task = asyncio.create_task(bg_wrapper())
    recap_task = asyncio.create_task(recap_wrapper())

    LOG.info("Agent Bot fully initialized — 34 commands, 9 menus, 2 background loops, web dashboard on port 9091")

    try:
        await tg_task
    except KeyboardInterrupt:
        pass
    finally:
        bg_task.cancel()
        recap_task.cancel()
        await tl.stop()


if __name__ == "__main__":
    # Start web dashboard in background thread
    web_port = int(os.environ.get("AGENT_WEB_PORT", "9091"))
    web_thread = threading.Thread(target=_run_web, args=(web_port,), daemon=True)
    web_thread.start()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("Shutdown complete")
    except Exception as e:
        LOG.critical("Fatal error: %s", e)
        sys.exit(1)
