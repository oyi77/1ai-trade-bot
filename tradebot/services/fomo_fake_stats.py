"""
FOMO Fake Statistics Engine

Generates consistent, monotonically increasing fake numbers for FOMO.
Uses time-based seeding so the same hour produces same numbers.
Numbers NEVER decrease - they only stay same or increase over time.

Usage:
    from tradebot.services.fomo_fake_stats import (
        get_fake_claim,
        get_fake_tp,
        get_fake_robot_users,
        get_fomo_claim_message,
        get_fomo_tp_message,
        get_fomo_robot_message,
        get_all_fomo_messages,
    )

    msg = get_fomo_claim_message()  # Returns random claim FOMO
    msg = get_fomo_tp_message()      # Returns random TP FOMO
    msg = get_fomo_robot_message()   # Returns random robot user FOMO
"""

import random
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Censored usernames list
# ---------------------------------------------------------------------------
_CENSORED_USERNAMES = [
    "always*****",
    "cuanboss*****",
    "profitsejahtera*****",
    "traderhebat*****",
    "suksesfinansial*****",
    "jeniusinvest*****",
    "makmurbersama*****",
    "kayaraya*****",
    "bebasfinansial*****",
    "wealthynusa*****",
]

# ---------------------------------------------------------------------------
# Trading symbols for fake TP events
# ---------------------------------------------------------------------------
_TP_SYMBOLS = ["XAUUSD", "BTCUSD", "EURUSD"]

# ---------------------------------------------------------------------------
# Historical baseline -- the anchor the whole engine floats from
# These are the values for the "epoch day" (day 0).
# ---------------------------------------------------------------------------
_EPOCH_DATE = datetime(2026, 1, 1, tzinfo=UTC)

_BASE_USERS_START = 50
_BASE_KEYS_START = 75

_BASE_CLAIM_START = 1_000_000        # Rp1,000,000
_CLAIM_GROWTH_PER_DAY = 50_000       # Rp50,000/day

_BASE_TP_MIN = 100.0                 # $100
_BASE_TP_MAX = 500.0                 # $500
_TP_GROWTH_MIN = 5.0                 # $5/day
_TP_GROWTH_MAX = 20.0                # $20/day

_ROBOT_TOTAL_PROFIT_START = 500_000_000   # Rp500,000,000
_ROBOT_PROFIT_GROWTH_PER_DAY = 10_000_000 # Rp10,000,000/day

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_daily_seed() -> int:
    """Return a deterministic seed integer based on the current date + hour."""
    now = datetime.now(UTC)
    return now.year * 10_000_000 + now.month * 100_000 + now.day * 100 + now.hour


def _calc_days_since_epoch() -> int:
    now = datetime.now(UTC)
    delta = now - _EPOCH_DATE
    return max(delta.days, 0)


def _rng() -> random.Random:
    """Return a seeded Random instance for the current hour."""
    return random.Random(get_daily_seed())


def _rng_daily() -> random.Random:
    """Return a Random seeded once per date (ignoring hour)."""
    now = datetime.now(UTC)
    seed = now.year * 10_000 + now.month * 100 + now.day
    return random.Random(seed)


# ---------------------------------------------------------------------------
# Core monotonically-increasing generators
# ---------------------------------------------------------------------------


def get_base_users() -> int:
    """Fake total robot EA users -- monotonically increasing."""
    days = _calc_days_since_epoch()
    rng = _rng_daily()
    total_growth = sum(rng.randrange(3, 6) for _ in range(days))
    return _BASE_USERS_START + total_growth


def get_base_keys() -> int:
    """Fake total EA keys distributed -- monotonically increasing."""
    days = _calc_days_since_epoch()
    rng = _rng_daily()
    total_growth = sum(rng.randrange(5, 9) for _ in range(days))
    return _BASE_KEYS_START + total_growth


def get_fake_claim() -> dict:
    """Return a fake whitelabel claim dict with monotonically increasing amounts."""
    days = _calc_days_since_epoch()
    rng = _rng()

    baseline = _BASE_CLAIM_START + days * _CLAIM_GROWTH_PER_DAY
    jitter = rng.uniform(-0.10, 0.10)
    amount = int(baseline * (1.0 + jitter))
    amount = max(amount, baseline)
    amount = max(min(amount, 50_000_000), 5_000_000)

    username = rng.choice(_CENSORED_USERNAMES)

    return {
        "username": username,
        "amount": amount,
        "amount_formatted": format_idr(amount),
    }


def get_fake_tp() -> dict:
    """Return a fake Take Profit event dict with monotonically increasing profits."""
    days = _calc_days_since_epoch()
    rng = _rng()

    growth = days * rng.uniform(_TP_GROWTH_MIN, _TP_GROWTH_MAX)
    min_profit = _BASE_TP_MIN + growth
    max_profit = _BASE_TP_MAX + growth

    profit = round(rng.uniform(min_profit, max_profit), 2)
    profit = min(profit, 1000.0)

    lots = round(rng.uniform(0.1, 2.0), 1)

    username = rng.choice(_CENSORED_USERNAMES)
    symbol = rng.choice(_TP_SYMBOLS)

    return {
        "username": username,
        "symbol": symbol,
        "profit": profit,
        "lots": lots,
    }


def get_fake_robot_users() -> dict:
    """Return aggregated robot user statistics."""
    users = get_base_users()
    keys = get_base_keys()

    days = _calc_days_since_epoch()
    rng = _rng()
    total_profit = _ROBOT_TOTAL_PROFIT_START + days * _ROBOT_PROFIT_GROWTH_PER_DAY

    jitter = rng.uniform(-0.02, 0.02)
    total_profit = int(total_profit * (1.0 + jitter))
    total_profit = max(
        total_profit,
        _ROBOT_TOTAL_PROFIT_START + days * _ROBOT_PROFIT_GROWTH_PER_DAY,
    )

    return {
        "users": users,
        "keys": keys,
        "total_profit": total_profit,
        "total_profit_formatted": format_idr(total_profit),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_idr(amount: int) -> str:
    """Format integer to IDR dot-separated string (e.g. 23508308 -> Rp23.508.308)."""
    s = f"{amount:,}".replace(",", ".")
    return f"Rp{s}"


# ---------------------------------------------------------------------------
# FOMO message builders
# ---------------------------------------------------------------------------


def get_fomo_claim_message() -> str:
    """Return a complete claim FOMO message."""
    claim = get_fake_claim()
    return (
        f"{claim['username']} melakukan penarikan komisi senilai "
        f"{claim['amount_formatted']}.\n"
        "Mereka sudah mulai, kamu kapan? "
        "Ayo join whitelabel, promosikan botmu, dan jadilah top affiliate kami!"
    )


def get_fomo_tp_message() -> str:
    """Return a complete Take Profit FOMO message."""
    tp = get_fake_tp()
    return (
        f"{tp['username']} Take Profit {tp['symbol']} "
        f"+${tp['profit']:,.2f} ({tp['lots']} lot)\n"
        "Sementara kamu masih baca-baca. Mereka sudah ACTION!"
    )


def get_fomo_robot_message() -> str:
    """Return a complete robot user FOMO message."""
    stats = get_fake_robot_users()
    return (
        f"Sudah {stats['users']} trader menggunakan robot EA kami.\n"
        f"Total {stats['keys']} EA key aktif.\n"
        f"Total profit tergenerate: {stats['total_profit_formatted']}\n\n"
        "Kekayaan tidak seharusnya dinikmati oleh segelintir orang.\n"
        "Kekayaan harusnya dirasakan setiap lapisan masyarakat!"
    )


def get_all_fomo_messages() -> list[str]:
    """Return all 3 FOMO messages in random order for broadcast."""
    msgs = [
        get_fomo_claim_message(),
        get_fomo_tp_message(),
        get_fomo_robot_message(),
    ]
    rng = _rng()
    rng.shuffle(msgs)
    return msgs