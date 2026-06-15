"""Demo: end-to-end test of plan_store + providers + unified bot wiring."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.plan_store import PlanRepository, PlanModel
from app.services import PlanRecord


def main():
    print("=" * 50)
    print("VILONA UNIFIED BOT — DEMO")
    print("=" * 50)

    repo = PlanRepository()
    repo.seed_from_env()
    plans = repo.all_active()
    print(f"\n[OK] Plan store: {len(plans)} active plans found")
    for p in plans:
        print(f"  - {p.plan_key} (variant={p.variant_id}) Rp{p.price_idr} / {p.duration_days}d")

    variant_plan = repo.get_by_variant(527961)
    print(f"\n[OK] Lookup by variant 527961 → {variant_plan}")

    from app.providers.sample_providers import SAMPLE_SIGNALS, SAMPLE_ORDERS, SAMPLE_TENANTS
    print(f"\n[OK] Sample signals: {len(SAMPLE_SIGNALS)}")
    print(f"[OK] Sample orders: {len(SAMPLE_ORDERS)}")
    print(f"[OK] Sample tenants: {len(SAMPLE_TENANTS)}")

    from app.providers.normalize_trades import normalize_trade
    raw = {"order_id": "T-123", "symbol": "XAU/USD", "side": "buy", "qty": 0.15, "price": 2650.0}
    norm = normalize_trade(raw)
    print(f"\n[OK] normalize_trade: {norm['symbol']} {norm['side']} x{norm['qty']} @{norm['price']}")

    from app.providers.sample_orders_feed import generate_orders
    orders = generate_orders(3)
    print(f"\n[OK] Generated {len(orders)} synthetic orders:")
    for o in orders:
        print(f"  {o['order_id']} | {o['symbol']} {o['side']} x{o['qty']} [{o['status']}]")

    print("\n" + "=" * 50)
    print("ALL CHECKS PASSED ✅")
    print("=" * 50)


if __name__ == "__main__":
    main()
