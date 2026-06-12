#!/usr/bin/env python3
import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradebot.config import settings
from tradebot.brokers.stockity.broker import StockityBroker
from tradebot.logging import setup_logging

async def main():
    setup_logging(level="DEBUG", log_format="console")
    print("🚀 Initializing Stockity Broker (DEMO)...")
    
    # Verify cookies
    cookie = settings.STOCKITY_FULL_COOKIE
    if not cookie:
        print("❌ Error: STOCKITY_FULL_COOKIE is not set in .env")
        sys.exit(1)
        
    print(f"📧 Stockity Email: {settings.STOCKITY_EMAIL}")
    print(f"🆔 User ID: {settings.STOCKITY_USER_ID}")
    
    # Initialize broker in demo mode
    broker = StockityBroker(deal_type="demo")
    
    try:
        await broker.connect()
        print("✅ Connected successfully!")
        
        # Wait a bit for balance update from WS
        await asyncio.sleep(3)
        balance = await broker.get_balance()
        print(f"💰 Current Demo Balance: {balance} USD")
        
        print("\n⚡ Placing a demo trade: CALL on CRYPTO_IDX (Amount: 1.0 USD)...")
        trade_res = await broker.place_trade(
            symbol="CRYPTO_IDX",
            direction="CALL",
            amount=1.0,
            duration=30,
            option_type="turbo"  # Standard turbo option (1-5 min)
        )
        
        print("\n📊 Trade Result:")
        print(f"   Status: {trade_res.status}")
        print(f"   Order ID: {trade_res.order_id}")
        print(f"   Symbol: {trade_res.symbol}")
        print(f"   Direction: {trade_res.direction}")
        print(f"   Amount: {trade_res.amount} USD")
        print(f"   Error: {trade_res.error}")
        
        print("\n⏳ Waiting 8 seconds for balance updates & trade execution events...")
        await asyncio.sleep(8)
        
        balance_new = await broker.get_balance()
        print(f"💰 Updated Demo Balance: {balance_new} USD")
    except Exception as e:
        print(f"❌ Error during execution: {e}")
    finally:
        await broker.close()
        print("\n🚪 Connection closed.")

if __name__ == "__main__":
    asyncio.run(main())
