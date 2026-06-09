"""
Autonomous trading agent using LangGraph.

Graph: observe → analyze → decide → execute → (loop)

Uses an LLM (with OpenAI/DeepSeek/Gemini fallback) to reason about
market state and decide whether to trade. Rule-based signals from
the tradebot engines (MTF consensus, SMC, etc.) are passed as context.

State is persisted between runs via MemorySaver (in-memory).
"""
import json
import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from tradebot.agents.llm import get_llm
from tradebot.agents.state import AgentState

LOG = logging.getLogger("tradebot.agents.graph")

def make_initial_state(symbol: str = "CRYPTO_IDX") -> AgentState:
    """Create initial agent state."""
    return AgentState(
        symbol=symbol,
        last_price=0.0,
        last_tick_time="",
        candles=[],
        signal="HOLD",
        signal_confidence=0.0,
        signal_source="",
        daily_pnl=0.0,
        open_positions=0,
        can_trade=True,
        decision="SKIP",
        decision_reason="init",
        trade_params={},
        trade_id="",
        trade_status="",
        trade_result={},
        llm_thought="",
        messages=[],
    )


# ── Node 1: Observe ────────────────────────────────────────────────

async def observe_node(state: AgentState) -> dict[str, Any]:
    """Fetch latest market data (candles + last tick)."""
    LOG.info("→ observe: fetching market data for %s", state.get("symbol"))
    try:
        from tradebot.signals.stockity import StockitySource
        src = StockitySource()
        try:
            candles = await src.fetch(state.get("symbol", "CRYPTO_IDX"), interval="1m", count=20)
            candle_dicts = [
                {
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                }
                for c in candles
            ]
            last_price = candles[-1].close if candles else 0.0
        finally:
            await src.close()
        return {
            "candles": candle_dicts,
            "last_price": last_price,
            "last_tick_time": str(candle_dicts[-1]["timestamp"]) if candle_dicts else "",
        }
    except Exception as e:
        LOG.warning("observe failed: %s", e)
        return {"candles": [], "last_price": 0.0, "last_tick_time": ""}


# ── Node 2: Analyze (rule-based signals) ──────────────────────────

async def analyze_node(state: AgentState) -> dict[str, Any]:
    """Run rule-based signal analysis (MTF consensus, patterns, etc.)."""
    LOG.info("→ analyze: running signal analysis")
    candles = state.get("candles", [])
    if len(candles) < 10:
        return {
            "signal": "HOLD",
            "signal_confidence": 0.0,
            "signal_source": "rule:insufficient_data",
        }

    # Simple trend analysis: compare recent close to older close
    recent_close = candles[-1]["close"]
    older_close = candles[-10]["close"]
    pct_change = (recent_close - older_close) / older_close * 100

    if pct_change > 0.05:
        signal, conf = "CALL", 0.6
    elif pct_change < -0.05:
        signal, conf = "PUT", 0.6
    else:
        signal, conf = "HOLD", 0.3

    return {
        "signal": signal,
        "signal_confidence": conf,
        "signal_source": "rule:trend",
    }


# ── Node 3: Decide (LLM reasoning) ────────────────────────────────

async def decide_node(state: AgentState) -> dict[str, Any]:
    """Use LLM to decide whether to act on the signal."""
    LOG.info("→ decide: LLM reasoning")
    if not state.get("can_trade", True):
        return {
            "decision": "SKIP",
            "decision_reason": "daily_limits",
            "llm_thought": "Daily limits reached, skipping",
        }

    signal = state.get("signal", "HOLD")
    confidence = state.get("signal_confidence", 0.0)
    if signal == "HOLD" or confidence < 0.5:
        return {
            "decision": "SKIP",
            "decision_reason": f"signal={signal} confidence={confidence:.2f}",
            "llm_thought": f"Signal too weak: {signal} @ {confidence:.2f}",
        }

    try:
        llm = get_llm(temperature=0.1)
        prompt = (
            f"You are a binary options trading agent. "
            f"Symbol: {state.get('symbol')}\n"
            f"Last price: {state.get('last_price'):.5f}\n"
            f"Rule-based signal: {signal} (confidence {confidence:.2%})\n"
            f"Daily P&L: {state.get('daily_pnl', 0):.2f}\n"
            f"Open positions: {state.get('open_positions', 0)}\n\n"
            f"Should we trade? Respond in JSON:\n"
            f'{{"decision": "TRADE|SKIP", "reason": "<short>", '
            f'"stake": <number 0.35-2.0>, "duration": <seconds>}}\n'
        )
        resp = await llm.ainvoke(prompt)
        text = resp.content if hasattr(resp, "content") else str(resp)
        LOG.info("LLM response: %s", text[:200])

        # Parse JSON from response
        try:
            # Find JSON in the response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(text[start:end])
                return {
                    "decision": parsed.get("decision", "SKIP"),
                    "decision_reason": parsed.get("reason", "llm"),
                    "trade_params": {
                        "amount": parsed.get("stake", 0.35),
                        "duration": parsed.get("duration", 60),
                    },
                    "llm_thought": text[:500],
                }
        except (json.JSONDecodeError, ValueError) as e:
            LOG.warning("LLM JSON parse failed: %s", e)

        return {
            "decision": "SKIP",
            "decision_reason": "llm_parse_error",
            "llm_thought": text[:500],
        }
    except Exception as e:
        LOG.warning("LLM unavailable, using rule-based decision: %s", e)
        # Fallback: trade if signal strong enough
        if confidence >= 0.6:
            return {
                "decision": "TRADE",
                "decision_reason": f"rule_fallback:{signal}",
                "trade_params": {"amount": 0.35, "duration": 60},
            }
        return {
            "decision": "SKIP",
            "decision_reason": f"llm_error:{e}",
        }


# ── Node 4: Execute ───────────────────────────────────────────────

async def execute_node(state: AgentState) -> dict[str, Any]:
    """Execute the trade via the broker."""
    if state.get("decision") != "TRADE":
        LOG.info("→ execute: no trade (decision=%s)", state.get("decision"))
        return {"trade_status": "skipped"}

    LOG.info("→ execute: placing trade")
    signal = state.get("signal", "HOLD")
    params = state.get("trade_params", {})
    symbol = state.get("symbol", "CRYPTO_IDX")

    if signal not in ("CALL", "PUT"):
        return {"trade_status": "invalid_signal"}

    try:
        from tradebot.brokers.stockity.broker import StockityBroker
        broker = StockityBroker()
        try:
            trade = await broker.place_trade(
                symbol=symbol,
                direction=signal,
                amount=params.get("amount", 0.35),
                duration=params.get("duration", 60),
            )
            return {
                "trade_id": trade.order_id,
                "trade_status": trade.status,
            }
        finally:
            await broker.close()
    except Exception as e:
        LOG.error("Execution failed: %s", e)
        return {
            "trade_id": "",
            "trade_status": f"error: {e}",
        }


# ── Build the graph ────────────────────────────────────────────────

def build_agent() -> Any:
    """Build the LangGraph autonomous trading agent."""
    workflow = StateGraph(AgentState)

    workflow.add_node("observe", observe_node)
    workflow.add_node("analyze", analyze_node)
    workflow.add_node("decide", decide_node)
    workflow.add_node("execute", execute_node)

    workflow.set_entry_point("observe")
    workflow.add_edge("observe", "analyze")
    workflow.add_edge("analyze", "decide")
    workflow.add_edge("decide", "execute")
    workflow.add_edge("execute", END)

    # Compile with in-memory checkpointing
    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


# ── Entry point ────────────────────────────────────────────────────

async def run_once(symbol: str = "CRYPTO_IDX") -> dict[str, Any]:
    """Run one cycle of the agent."""
    agent = build_agent()
    state = make_initial_state(symbol)
    config = {"configurable": {"thread_id": "tradebot-1"}}
    result = await agent.ainvoke(state, config)
    return result
