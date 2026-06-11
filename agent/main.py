"""
Main entry point — wires Telethon + Core + Background loops.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import logging
import os
import sys
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

from agent.menu import get_menu_kb, get_menu_text
from agent.core import (
    cmd_start, cmd_signal, cmd_price, cmd_status, cmd_myid, cmd_donate,
    cmd_levels, cmd_news, cmd_zones, cmd_structure, cmd_session,
    cmd_killzone, cmd_help, cmd_stockity, cmd_analyze, cmd_data,
    cmd_genkey, cmd_mykey, cmd_winrate, cmd_history, cmd_recap, cmd_mapping,
    handle_menu_callback, handle_cmd_callback, handle_donate_callback,
    auto_analysis_loop, daily_recap_broadcast,
)
from agent.telethon_layer import TelethonLayer


async def main():
    tl = TelethonLayer()

    # Register all command handlers
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
        ("subscribe", cmd_help),
    ]:
        tl.register_command(cmd, handler)

    # Register callback handlers
    tl.register_callback_prefix("menu:", handle_menu_callback)
    tl.register_callback_prefix("cmd:", handle_cmd_callback)
    tl.register_callback_prefix("donate:", handle_donate_callback)

    # Set message handler for menu navigation callback response
    async def on_message(text: str, chat_id: str):
        pass  # Commands are handled by router

    tl.set_message_handler(on_message)

    # Start background tasks
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

    # Start Telethon client (blocks until disconnected)
    tg_task = asyncio.create_task(tl.start())
    bg_task = asyncio.create_task(bg_wrapper())
    recap_task = asyncio.create_task(recap_wrapper())

    LOG.info("Agent Bot fully initialized — 34 commands, 9 menus, 2 background loops")

    # Wait for Telethon to finish (or fail)
    try:
        await tg_task
    except KeyboardInterrupt:
        pass
    finally:
        bg_task.cancel()
        recap_task.cancel()
        await tl.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        LOG.info("Shutdown complete")
    except Exception as e:
        LOG.critical("Fatal error: %s", e)
        sys.exit(1)
