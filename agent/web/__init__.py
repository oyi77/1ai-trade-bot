"""
Web dashboard runner — launches FastAPI in a separate thread.
"""

import logging
import threading
import uvicorn

from agent.database import init_db

LOG = logging.getLogger("agent.web_runner")


def run_web(host: str = "0.0.0.0", port: int = 9091) -> None:
    """Start the web dashboard in background thread."""
    init_db()
    LOG.info("Web dashboard starting on %s:%s", host, port)
    uvicorn.run(
        "agent.web.server:app",
        host=host,
        port=port,
        log_level="info",
    )
