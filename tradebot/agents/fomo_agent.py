"""
FOMO Agent — LangGraph-based viral marketing agent.

Uses LLM + fake statistics from fomo_fake_stats.py to generate
context-aware, varying FOMO messages for Telegram broadcast.

Architecture:
    fomo_fake_stats.py  →  Tool Node  →  LLM Node  →  Format Node  →  Output
        (data source)       (fetch stats)  (craft message)  (apply template)

The LLM crafts unique messages each time using:
    - Fake whitelabel claim amounts (monotonically increasing)
    - Fake user TP events (growing daily)
    - Fake robot user counts (growing daily)
    - Random quotes and phrases from the system prompt
"""

from __future__ import annotations

import logging
import random
from typing import Any

from langgraph.graph import END, StateGraph

from tradebot.agents.llm import get_llm
from tradebot.agents.state import AgentState
from tradebot.services.fomo_fake_stats import (
    get_all_fomo_messages,
    get_fake_claim,
    get_fake_robot_users,
    get_fake_tp,
)

LOG = logging.getLogger("tradebot.agents.fomo")

# ── Extended AgentState for FOMO ─────────────────────────────────────


class FomoState(AgentState, total=False):
    """FOMO-specific state extending AgentState."""

    fomo_type: str  # "claim", "tp", "robot", "mixed"
    fomo_data: dict[str, Any]  # Raw fake stats
    fomo_message: str  # Generated message
    fomo_messages: list[str]  # Batch of messages
    user_context: str  # Optional user context for personalization


# ── Nodes ─────────────────────────────────────────────────────────────


async def fetch_fomo_stats_node(state: FomoState) -> dict[str, Any]:
    """Fetch fake statistics data from fomo_fake_stats service.

    This node reads the deterministic monotonically-increasing
    fake data and makes it available for the LLM to craft messages.
    """
    fomo_type = state.get("fomo_type", "mixed")

    data = {
        "claim": get_fake_claim(),
        "tp": get_fake_tp(),
        "robot": get_fake_robot_users(),
    }

    LOG.info(
        "FOMO stats fetched: type=%s claim=%s tp=%s robot=%d keys=%d",
        fomo_type,
        data["claim"]["formatted"],
        data["tp"]["formatted"],
        data["robot"]["total_users"],
        data["robot"]["total_keys"],
    )

    return {"fomo_data": data}


async def llm_fomo_node(state: FomoState) -> dict[str, Any]:
    """Use LLM to craft engaging FOMO messages from the stats.

    The LLM receives the fake stats and generates varied,
    natural-sounding FOMO messages. No two calls produce
    the same wording.
    """
    data = state.get("fomo_data", {})
    fomo_type = state.get("fomo_type", "mixed")

    # Build system prompt with context
    claim = data.get("claim", {})
    tp = data.get("tp", {})
    robot = data.get("robot", {})

    system_prompt = f"""Kamu adalah AI marketing specialist untuk platform trading forex dan binary options.
Tugasmu adalah membuat FOMO (Fear Of Missing Out) message dalam Bahasa Indonesia yang engaging dan natural.

DATA SAAT INI:
- Whitelabel claim terbesar: {claim.get("formatted", "Rp0")} oleh {claim.get("censored_username", "user****")}
- Fake TP terbaru: {tp.get("formatted", "$0")} oleh {tp.get("censored_username", "trader****")} pada {tp.get("symbol", "XAUUSD")}
- Total pengguna robot EA: {robot.get("total_users", 0)} trader dengan {robot.get("total_keys", 0)} EA key aktif
- Total profit robot: Rp{robot.get("total_profit", 0):,}

TIPE KONTEN YANG DIMINTA: {fomo_type}

ATURAN:
1. Gunakan bahasa Indonesia casual, seperti teman ngobrol
2. Variasikan gaya bicara setiap kali — jangan monoton
3. Sensor username dengan **** (contoh: always****, cuanboss****)
4. Nominal harus akurat sesuai data di atas
5. Jangan terlalu formal — pakai bahasa sehari-hari
6. Sertakan satu kalimat quotes bijak jika cocok
7. Buat 3-5 kalimat per message, jangan terlalu panjang

CONTOH GAYA:
- "always***** melakukan penarikan komisi senilai Rp23.508.308. Mereka sudah mulai, kamu kapan?"
- "cuanboss***** baru saja TP XAUUSD +$342.50. Cuannya real, bukan mimpi!"
- "Sudah 87 trader menggunakan robot EA kita. Total 134 EA key aktif di seluruh server."

Sekarang buat 2 FOMO message dengan data di atas. Variasikan antara claim, TP, dan robot users."""

    try:
        llm = get_llm(temperature=0.9, prefer="openai")
        response = await llm.ainvoke(system_prompt)
        message = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        LOG.warning("LLM FOMO failed, using template fallback: %s", e)
        # Fallback to template-based messages
        all_msgs = get_all_fomo_messages()
        message = "\n\n".join(all_msgs)

    return {"fomo_message": message, "llm_thought": f"FOMO {fomo_type} generated"}


async def format_fomo_node(state: FomoState) -> dict[str, Any]:
    """Format the LLM output into proper FOMO messages.

    If LLM failed or returned nothing, use template fallback.
    Ensures output is always deliverable.
    """
    message = state.get("fomo_message", "")

    if not message or len(message) < 20:
        # Fallback to template messages
        all_msgs = get_all_fomo_messages()
        message = "\n\n".join(all_msgs)

    # Split into individual messages
    separator = "\n\n---\n\n"
    if separator in message:
        messages = [m.strip() for m in message.split(separator) if m.strip()]
    else:
        messages = [message]

    # Ensure we have at least one message
    if not messages:
        messages = get_all_fomo_messages()

    LOG.info("FOMO formatted: %d messages generated", len(messages))
    return {"fomo_messages": messages}


# ── Build graph ────────────────────────────────────────────────────────


def build_fomo_agent() -> Any:
    """Build the FOMO LangGraph agent.

    Graph:
        fetch_stats → llm_craft → format_output → END
    """
    workflow = StateGraph(FomoState)
    workflow.add_node("fetch_stats", fetch_fomo_stats_node)
    workflow.add_node("llm_craft", llm_fomo_node)
    workflow.add_node("format_output", format_fomo_node)

    workflow.set_entry_point("fetch_stats")
    workflow.add_edge("fetch_stats", "llm_craft")
    workflow.add_edge("llm_craft", "format_output")
    workflow.add_edge("format_output", END)

    return workflow.compile()


# ── Entry point ──────────────────────────────────────────────────────


async def run_fomo_cycle(fomo_type: str = "mixed") -> dict[str, Any]:
    """Run one FOMO generation cycle.

    Args:
        fomo_type: "claim", "tp", "robot", or "mixed"

    Returns:
        State with fomo_messages containing generated FOMO content.
    """
    agent = build_fomo_agent()
    state: FomoState = {
        "platform": "stockity",
        "symbol": "XAUUSD",
        "fomo_type": fomo_type,
        "signal": "HOLD",
        "signal_confidence": 0.0,
        "signal_source": "fomo",
        "daily_pnl": 0.0,
        "open_positions": 0,
        "can_trade": True,
        "decision": "SKIP",
        "decision_reason": "FOMO broadcast",
        "trade_params": {},
        "trade_id": "",
        "trade_status": "",
        "trade_platform": "",
        "trade_error": "",
        "trade_result": {},
        "llm_thought": "",
        "messages": [],
        "fomo_message": "",
        "fomo_messages": [],
        "user_context": "",
    }

    config = {"configurable": {"thread_id": f"fomo-{random.randint(1000, 9999)}"}}
    result = await agent.ainvoke(state, config)
    return result


# ── Simple sync wrapper (for scheduled tasks) ────────────────────────


def get_fomo_broadcast() -> list[str]:
    """Get FOMO messages for broadcast.

    Tries LLM-powered generation first, falls back to templates.
    This is a sync wrapper for easy integration with Telegram bot.
    """
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context — use template fallback
            LOG.info("FOMO: async loop running, using template fallback")
            return get_all_fomo_messages()
        result = loop.run_until_complete(run_fomo_cycle())
        return result.get("fomo_messages", get_all_fomo_messages())
    except Exception as e:
        LOG.warning("FOMO agent failed, using template: %s", e)
        return get_all_fomo_messages()
