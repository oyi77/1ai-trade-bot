"""
Autonomous trading agent using LangGraph.

Components:
    llm.py    — LLM provider with fallback chain (OpenAI → DeepSeek → Gemini)
    state.py  — AgentState TypedDict for graph state
    graph.py  — LangGraph workflow: observe → analyze → decide → execute
"""

from tradebot.agents.graph import build_agent, make_initial_state, run_once
from tradebot.agents.llm import get_llm, list_available_providers
from tradebot.agents.state import AgentState

__all__ = [
    "AgentState",
    "build_agent",
    "get_llm",
    "list_available_providers",
    "make_initial_state",
    "run_once",
]
