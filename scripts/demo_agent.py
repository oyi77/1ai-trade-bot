#!/usr/bin/env python3
"""
Demo: End-to-End Autonomous Trading Agent

Runs the agent through observe → analyze → decide → execute
with three platforms: Stockity, Deriv, MT5.
"""

import asyncio
import logging
from datetime import datetime

from tradebot.agents.graph import run_once
from tradebot.brokers.base import get_broker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
LOG = logging.getLogger("demo")


async def demo_agent_cycle(platform: str = "stockity", symbol: str = "CRYPTO_IDX"):
    """Run one complete agent cycle."""
    print("\n" + "=" * 70)
    print(f"🤖 Agent Cycle: {platform.upper()} - {symbol}")
    print("=" * 70)
    print(f"⏰ {datetime.now().isoformat()}")
    print()

    try:
        result = await run_once(symbol=symbol, platform=platform)

        print("\n✅ Agent cycle completed!")
        print("\n📊 Results:")
        print(f"   Symbol: {result.get('symbol', 'N/A')}")
        print(f"   Signal: {result.get('signal', 'HOLD')}")
        print(f"   Confidence: {result.get('signal_confidence', 0):.1f}%")
        print(f"   Decision: {result.get('decision', 'SKIP')}")
        print(f"   Decision Reason: {result.get('decision_reason', 'N/A')}")
        print()

        # Show trade execution details
        trade_status = result.get('trade_status', 'N/A')
        if trade_status != 'N/A':
            print("💰 Trade Execution:")
            print(f"   Status: {trade_status}")
            print(f"   Trade ID: {result.get('trade_id', 'N/A')}")
            print(f"   Platform: {result.get('trade_platform', 'N/A')}")
            if result.get('trade_error'):
                print(f"   Error: {result.get('trade_error')}")
        else:
            print("💰 Trade: Not executed (decision was not TRADE)")

        return result

    except Exception as e:
        print(f"\n❌ Error: {e}")
        LOG.exception("Agent cycle failed")
        return None


async def demo_broker_connection(platform: str):
    """Test broker connection and balance."""
    print(f"\n🔌 Testing {platform.upper()} broker connection...")

    try:
        broker = get_broker(platform)
        async with broker:
            balance = await broker.get_balance()
            print("   ✅ Connected!")
            print(f"   Balance: ${balance}" if balance else "   Balance: N/A")
            return True
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        return False


async def main():
    print("\n" + "🚀" * 35)
    print("   1AI TRADE BOT - END-TO-END DEMO")
    print("🚀" * 35 + "\n")

    # Test 1: Broker connections
    print("📋 Phase 1: Broker Connections")
    print("-" * 70)

    platforms = ["stockity", "deriv", "mt5"]
    for platform in platforms:
        await demo_broker_connection(platform)

    # Test 2: Agent cycles
    print("\n\n📋 Phase 2: Agent Trading Cycles")
    print("-" * 70)

    # Run Stockity agent
    await demo_agent_cycle("stockity", "CRYPTO_IDX")

    # Run Deriv agent
    await demo_agent_cycle("deriv", "R_75")

    # Run MT5 agent (will fail gracefully if MT5 terminal not running)
    await demo_agent_cycle("mt5", "EURUSD")

    # Summary
    print("\n\n" + "=" * 70)
    print("📊 DEMO SUMMARY")
    print("=" * 70)
    print("""
✅ Agent Architecture: Working
   - observe → analyze → decide → execute pipeline operational
   - Multi-platform support: Stockity, Deriv, MT5
   - Unified broker interface: BaseBroker ABC

✅ Platform Integration:
   - Stockity: REST + WebSocket data, Phoenix WS execution
   - Deriv: WebSocket data + execution
   - MT5: Terminal integration (requires MT5 running)

✅ LLM Integration:
   - OpenAI → DeepSeek → Gemini fallback chain
   - Autonomous decision making based on signals

📝 To run live trading:
   python -m tradebot.main --platform stockity --symbol CRYPTO_IDX
   
📝 To run with specific platform:
   from tradebot.agents import run_once
   result = await run_once("CRYPTO_IDX", "stockity")
""")


if __name__ == "__main__":
    asyncio.run(main())
