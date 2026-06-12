#!/usr/bin/env python3
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tradebot.services.trade_tracker_service as json_tracker
from tradebot.brokers.stockity.broker import StockityBroker
from tradebot.config import settings
from tradebot.logging import setup_logging
from tradebot.monitoring.tracker import TradeTracker

# Local Constants
USD_IDR = 16350


async def main():
    setup_logging(level="INFO", log_format="console")
    print("🚀 Initializing Stockity Broker (DEMO)...")

    # Verify cookies
    cookie = settings.STOCKITY_FULL_COOKIE
    if not cookie:
        print("❌ Error: STOCKITY_FULL_COOKIE is not set in .env")
        sys.exit(1)

    print(f"📧 Stockity Email: {settings.STOCKITY_EMAIL}")
    print(f"🆔 User ID: {settings.STOCKITY_USER_ID}")

    # Initialize trackers
    db_tracker = TradeTracker()

    # Initialize broker in demo mode
    broker = StockityBroker(deal_type="demo")

    opened_future = asyncio.Future()
    closed_future = asyncio.Future()

    def on_opened(msg):
        payload = msg.get("payload", {})
        o_id = payload.get("id")
        uuid = payload.get("uuid")
        o_type = payload.get("option_type")
        print(f"\n🔔 WS Event [opened]: ID={o_id} UUID={uuid} Option={o_type}")
        if not opened_future.done():
            opened_future.set_result(payload)

    def on_closed(msg):
        payload = msg.get("payload", {})
        o_id = payload.get("id")
        uuid = payload.get("uuid")
        status = payload.get("status")
        win = payload.get("win")
        print(f"\n🔔 WS Event [closed]: ID={o_id} UUID={uuid} Status={status} Win={win}")
        if not closed_future.done():
            closed_future.set_result(payload)

    try:
        await broker.connect()
        print("✅ Connected to Stockity Phoenix WebSocket successfully!")

        # Register WS callbacks
        broker.on_event("bo", "opened", on_opened)
        broker.on_event("bo", "closed", on_closed)

        # Wait for initial balance load
        for i in range(20):
            print(f"DEBUG: Loop {i}, raw_balance={broker._balance_raw}")
            if broker.balance_currency and broker.balance > 0:
                break
            await asyncio.sleep(0.5)
        initial_balance = await broker.get_balance()
        currency = broker.balance_currency
        print(f"💰 Initial Demo Balance: {initial_balance} {currency}")

        # Set stake dynamically based on currency
        stake = 14000.0 if currency.upper() == "IDR" else 1.0
        symbol = "CRYPTO_IDX"
        direction = "CALL"
        confidence = 0.85
        grade = "A"

        print(
            f"\n🎯 [Signal Generation] Generated mock signal: {symbol} {direction} "
            f"(Conf: {confidence * 100}%, Grade: {grade})"
        )

        # Record open in JSON journal
        sig_dict = {
            "action": direction,
            "sl": 0,
            "tp": 0,
            "confidence": confidence,
            "grade": grade,
            "stake": stake,
        }
        json_tid = json_tracker.open_trade(
            sig_dict, entry_price=1.0, symbol=symbol, source="stockity_blitz"
        )
        print(f"📝 Recorded open trade in JSON journal (ID: {json_tid})")

        # Record open in SQLite journal
        db_tid = db_tracker.open_trade(
            sig_dict, entry_price=1.0, symbol=symbol, source="stockity_blitz"
        )
        print(f"📝 Recorded open trade in SQLite journal (ID: {db_tid})")

        # 2. Place Trade via Broker
        print(f"\n⚡ Executing 5-second BLITZ (Amount: {stake} {currency})...")
        trade_res = await broker.place_trade(
            symbol=symbol,
            direction=direction,
            amount=stake,
            duration=5,
            option_type="blitz",
        )

        print("📥 Order placement reply:")
        print(f"   Order ID/Ref: {trade_res.order_id}")
        print(f"   Status: {trade_res.status}")

        # 3. Wait for WS execution & completion events
        print("\n⏳ Waiting for Blitz trade to settle (5 seconds duration)...")
        try:
            opened_payload = await asyncio.wait_for(opened_future, timeout=10.0)
            ws_id = opened_payload.get("id")
            print(f"   ➔ Trade is now OPEN. WebSocket ID: {ws_id}")
        except TimeoutError:
            print("⚠️ Timeout waiting for trade to open.")
            opened_payload = None

        try:
            closed_payload = await asyncio.wait_for(closed_future, timeout=15.0)
            print("   ➔ Trade is now CLOSED/SETTLED.")
        except TimeoutError:
            print("❌ Timeout waiting for trade to settle.")
            closed_payload = None

        if closed_payload:
            # 4. Process Outcome & Calculate PNL
            status = closed_payload.get("status")  # 'won' or 'lost'
            win_amount_raw = closed_payload.get("win", 0)
            win_amount_native = win_amount_raw / broker.currency_factor

            pnl_native = win_amount_native - stake if status == "won" else -stake
            outcome = "WON" if status == "won" else "LOST"
            print(
                f"\n📊 [PNL Calculation] Outcome: {outcome} | "
                f"Payout: {win_amount_native} {currency} | P&L: {pnl_native:+.2f} {currency}"
            )

            # 5. Resolve/Close Trade in SQLite Journal
            db_tracker.close_trade(db_tid, close_price=1.0, outcome=outcome, symbol=symbol)
            # Update PNL correctly for binary option in SQLite
            db_tracker._storage.execute(
                "UPDATE trades SET outcome=?, profit_usd=?, profit_idr=?, pips=0 WHERE trade_id=?",
                (outcome, pnl_native / USD_IDR, int(pnl_native), db_tid),
            )
            print(f"✅ Resolved trade {db_tid} in SQLite database.")

            # Resolve/Close Trade in JSON Journal
            json_data = json_tracker._load()
            for t in json_data["trades"]:
                if t.get("id") == json_tid:
                    t["outcome"] = outcome
                    t["close_time"] = datetime.now().isoformat()
                    t["close_price"] = 1.0
                    t["pips"] = 0.0
                    t["profit_usd"] = pnl_native / USD_IDR
                    t["profit_idr"] = int(pnl_native)
                    break

            # Recalculate JSON stats
            s = json_data["stats"]
            s["total"] += 1
            if outcome == "WON":
                s["wins"] += 1
                s["total_profit_usd"] += pnl_native / USD_IDR
            else:
                s["losses"] += 1
                s["total_profit_usd"] += pnl_native / USD_IDR
            json_tracker._save(json_data)
            print(f"✅ Resolved trade {json_tid} in JSON journal file.")

            # 6. Verify and output results
            print("\n🔍 --- Trading Journal / History Verification ---")

            # Fetch from SQLite
            row = db_tracker._storage.fetchone("SELECT * FROM trades WHERE trade_id=?", (db_tid,))
            if row:
                print(f"📥 SQLite Journal Verification (ID: {db_tid}):")
                print(
                    f"   Symbol: {row[2]} | Dir: {row[3]} | Stake: ${row[7]:.2f} | "
                    f"Exit: {row[5]} | Outcome: {row[9]} | P&L: ${row[11]:+.2f}"
                )
            else:
                print("❌ Failed to verify trade in SQLite database.")

            # Fetch from JSON
            json_data_new = json_tracker._load()
            found_json = next((t for t in json_data_new["trades"] if t.get("id") == json_tid), None)
            if found_json:
                print(f"📥 JSON Journal Verification (ID: {json_tid}):")
                print(
                    f"   Symbol: {found_json['symbol']} | Dir: {found_json['action']} | "
                    f"Outcome: {found_json['outcome']} | P&L: ${found_json['profit_usd']:+.2f}"
                )
            else:
                print("❌ Failed to verify trade in JSON journal.")

        # Wait a bit for balance sync on WS
        await asyncio.sleep(2)
        final_balance = await broker.get_balance()
        print(f"\n💰 Final Demo Balance: {final_balance} {currency}")

    except Exception as e:
        print(f"❌ Error during execution: {e}")
    finally:
        await broker.close()
        print("\n🚪 Connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
