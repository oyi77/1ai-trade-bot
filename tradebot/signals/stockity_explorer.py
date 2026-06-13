"""
Stockity API Explorer — Discover hidden endpoints and patterns.

Explores:
- Different option types (blitz, binary, turbo, etc.)
- Various durations (5s, 1m, 5m, 15m, 30m, 60m)
- Analytics endpoints (winrate, volume, statistics)
- Historical deals and performance data
- Leaderboard and copy trading data
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from tradebot.brokers.stockity.broker import StockityBroker, _symbol_to_ric

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("stockity_explorer")


class StockityExplorer:
    """Explore Stockity API for hidden data and patterns."""

    def __init__(self) -> None:
        self.broker: StockityBroker | None = None
        self.discovered_endpoints: list[dict[str, Any]] = []
        self.option_types: set[str] = set()
        self.durations: list[tuple[str, int]] = []

    async def connect(self) -> None:
        """Connect to Stockity Phoenix Channels."""
        self.broker = StockityBroker()
        await self.broker.connect()
        LOG.info("✅ Connected to Stockity")

    async def close(self) -> None:
        """Close connection."""
        if self.broker:
            await self.broker.close()

    async def test_option_type(
        self,
        option_type: str,
        symbol: str = "CRYPTO_IDX",
        amount: float = 0.35,
        duration_s: int | None = None,
    ) -> dict[str, Any] | None:
        """Test a specific option type."""
        if not self.broker:
            await self.connect()

        now = datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)

        # Calculate expire_at based on option type
        if option_type == "blitz":
            # 5 seconds, both timestamps in ms
            expire_ms = now_ms + 5000
            payload = {
                "ric": _symbol_to_ric(symbol),
                "amount": int(amount * 100000000),
                "created_at": now_ms,
                "deal_type": "demo",
                "expire_at": expire_ms,
                "option_type": option_type,
                "trend": "call",
                "tournament_id": None,
                "is_state": False,
            }
        elif option_type == "binary":
            # Next 30-min boundary
            minute = now.minute
            next_30 = ((minute // 30) + 1) * 30
            if next_30 >= 60:
                expire_dt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            else:
                expire_dt = now.replace(minute=next_30, second=0, microsecond=0)
            expire_ts = int(expire_dt.timestamp())

            payload = {
                "ric": _symbol_to_ric(symbol),
                "amount": int(amount * 100000000),
                "created_at": now_ms,
                "deal_type": "demo",
                "expire_at": expire_ts,
                "option_type": option_type,
                "trend": "call",
                "tournament_id": None,
                "is_state": False,
            }
        elif option_type == "turbo":
            # 1 minute (60 seconds), timestamps in ms like blitz
            expire_ms = now_ms + 60000
            payload = {
                "ric": _symbol_to_ric(symbol),
                "amount": int(amount * 100000000),
                "created_at": now_ms,
                "deal_type": "demo",
                "expire_at": expire_ms,
                "option_type": option_type,
                "trend": "call",
                "tournament_id": None,
                "is_state": False,
            }
        else:
            LOG.warning(f"Unknown option type: {option_type}")
            return None

        LOG.info(f"Testing {option_type}: {json.dumps(payload, indent=2)}")

        try:
            ref = await self.broker._send_event("bo", "create", payload)
            LOG.info(f"Sent with ref: {ref}")

            # Wait for response
            await asyncio.sleep(3)

            return {"option_type": option_type, "ref": ref, "payload": payload}
        except Exception as e:
            LOG.error(f"Failed: {e}")
            return None

    async def fetch_deals_history(
        self,
        deal_type: str = "demo",
        limit: int = 100,
    ) -> list[dict[str, Any]] | None:
        """Fetch historical deals via REST API."""
        import httpx

        url = "https://api.stockity.com/bo-deals-history/v3/deals/trade"
        params = {
            "type": deal_type,
            "locale": "en",
            "limit": limit,
        }

        headers = {
            "accept": "application/json",
            "cookie": self.broker._cookie if self.broker else "",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                LOG.info(f"Fetched {len(data.get('deals', []))} deals")
                return data.get('deals', [])
        except Exception as e:
            LOG.error(f"Failed to fetch deals: {e}")
            return None

    async def fetch_leaderboard(self) -> dict[str, Any] | None:
        """Fetch copy trading leaderboard."""
        import httpx

        url = "https://api.stockity.com/copy-trading/v1/leaderboard/membership"
        params = {"locale": "en"}

        headers = {
            "accept": "application/json",
            "cookie": self.broker._cookie if self.broker else "",
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            LOG.error(f"Failed to fetch leaderboard: {e}")
            return None

    async def fetch_statistics(self, ric: str = "Z-CRY/IDX") -> dict[str, Any] | None:
        """Fetch asset statistics (winrate, volume, etc.)."""
        import httpx

        # Try different possible endpoints
        endpoints = [
            f"https://api.stockity.com/statistics/v1/assets/{ric}",
            f"https://api.stockity.com/analytics/v1/assets/{ric}",
            f"https://api.stockity.com/market-data/v1/{ric}/stats",
        ]

        headers = {
            "accept": "application/json",
            "cookie": self.broker._cookie if self.broker else "",
        }

        for url in endpoints:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        LOG.info(f"Found stats endpoint: {url}")
                        return resp.json()
            except Exception:
                continue

        LOG.warning("No statistics endpoint found")
        return None

    async def monitor_majority_opinion(self, duration_s: int = 30) -> dict[str, Any]:
        """Monitor majority opinion events (crowd sentiment)."""
        if not self.broker:
            await self.connect()

        majority_samples = []

        original = self.broker._handle_message

        async def capture(msg):
            if msg.get('event') == 'majority_opinion':
                payload = msg.get('payload', {})
                majority_samples.append({
                    'call': payload.get('call', 0),
                    'put': payload.get('put', 0),
                    'asset': payload.get('asset', ''),
                })
            await original(msg)

        self.broker._handle_message = capture

        LOG.info(f"Monitoring majority opinion for {duration_s}s...")
        await asyncio.sleep(duration_s)

        self.broker._handle_message = original

        if not majority_samples:
            return {'error': 'No majority opinion events received'}

        # Analyze
        avg_call = sum(s['call'] for s in majority_samples) / len(majority_samples)
        avg_put = sum(s['put'] for s in majority_samples) / len(majority_samples)

        return {
            'samples': len(majority_samples),
            'avg_call': avg_call,
            'avg_put': avg_put,
            'majority': 'CALL' if avg_call > avg_put else 'PUT',
            'confidence': max(avg_call, avg_put),
            'samples_detail': majority_samples[-5:],  # Last 5
        }

    async def analyze_winrate_patterns(
        self,
        deals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Analyze winrate patterns from historical deals."""
        if not deals:
            return {}

        total = len(deals)
        wins = sum(1 for d in deals if d.get('status') == 'won' or d.get('profit', 0) > 0)
        losses = sum(1 for d in deals if d.get('status') == 'lost' or d.get('profit', 0) < 0)

        # Analyze by hour
        hourly = {}
        for deal in deals:
            created = deal.get('created_at', '')
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    hour = dt.hour
                    if hour not in hourly:
                        hourly[hour] = {'wins': 0, 'losses': 0}
                    if deal.get('profit', 0) > 0:
                        hourly[hour]['wins'] += 1
                    else:
                        hourly[hour]['losses'] += 1
                except Exception as e:
                    LOG.warning("Silent exception caught: %s", e)

        # Find best hours
        best_hours = []
        for hour, stats in hourly.items():
            total_h = stats['wins'] + stats['losses']
            if total_h >= 5:  # Minimum sample size
                winrate = stats['wins'] / total_h * 100
                best_hours.append((hour, winrate, total_h))

        best_hours.sort(key=lambda x: -x[1])

        # Analyze by trend
        trend_stats = {'call': {'wins': 0, 'losses': 0}, 'put': {'wins': 0, 'losses': 0}}
        for deal in deals:
            trend = deal.get('trend', '').lower()
            if trend in trend_stats:
                if deal.get('profit', 0) > 0:
                    trend_stats[trend]['wins'] += 1
                else:
                    trend_stats[trend]['losses'] += 1

        return {
            'total_deals': total,
            'wins': wins,
            'losses': losses,
            'winrate': wins / total * 100 if total > 0 else 0,
            'best_hours': best_hours[:5],
            'trend_stats': trend_stats,
        }


async def main():
    """Run exploration."""
    print("=" * 70)
    print("🔍 STOCKITY API EXPLORER")
    print("=" * 70)
    print()

    explorer = StockityExplorer()

    try:
        await explorer.connect()

        # Test different option types
        print("📊 Testing Option Types...")
        print()

        for opt_type in ["blitz", "binary", "turbo"]:
            print(f"Testing {opt_type}...")
            result = await explorer.test_option_type(opt_type)
            if result:
                print(f"  ✅ {opt_type} works")
                explorer.option_types.add(opt_type)
            else:
                print(f"  ❌ {opt_type} failed")
            print()

        # Fetch deals history
        print("📜 Fetching Deals History...")
        deals = await explorer.fetch_deals_history(limit=50)
        if deals:
            print(f"  ✅ Fetched {len(deals)} deals")

            # Analyze patterns
            print()
            print("📈 Analyzing Winrate Patterns...")
            analysis = await explorer.analyze_winrate_patterns(deals)
            print(f"  Total Deals: {analysis.get('total_deals', 0)}")
            print(f"  Winrate: {analysis.get('winrate', 0):.1f}%")
            print(f"  Best Hours: {analysis.get('best_hours', [])}")
        else:
            print("  ❌ Failed to fetch deals")

        # Fetch leaderboard
        print()
        print("🏆 Fetching Leaderboard...")
        leaderboard = await explorer.fetch_leaderboard()
        if leaderboard:
            print("  ✅ Leaderboard fetched")
            # print(json.dumps(leaderboard, indent=2)[:500])
        else:
            print("  ❌ Failed to fetch leaderboard")

        # Fetch statistics
        print()
        print("📊 Fetching Statistics...")
        stats = await explorer.fetch_statistics()
        if stats:
            print("  ✅ Statistics fetched")
            print(json.dumps(stats, indent=2)[:500])
        else:
            print("  ❌ No statistics endpoint found")

    finally:
        await explorer.close()

    print()
    print("=" * 70)
    print("📋 EXPLORATION SUMMARY")
    print("=" * 70)
    print(f"Option Types Found: {explorer.option_types}")
    print(f"Durations Tested: {explorer.durations}")


if __name__ == "__main__":
    asyncio.run(main())
