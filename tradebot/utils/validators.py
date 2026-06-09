"""Input validation utilities for trading parameters."""

import re

# Valid Deriv.com symbols — match common patterns like R_10, R_25, R_50, R_75, R_100
# and volatility indices 1HZ10V, 1HZ25V, 1HZ50V, 1HZ75V, 1HZ100V, etc.
_SYMBOL_RE = re.compile(
    r"^(?:(?:R|1HZ)(?:_?)(?:\d{2,3})V?|"
    r"boom\s*\d{3}|"
    r"crash\s*\d{3}|"
    r"stp\w*|"
    r"frx\w+|"
    r"cry\w+|"
    r"cg\w+|"
    r"OTC_\w+|"
    r"[A-Z]{3,6}/[A-Z]{3,6})$",
    re.IGNORECASE,
)


def validate_symbol(symbol: str) -> str:
    """Normalise and validate a trading symbol.

    Returns the uppercased, stripped symbol if valid.

    Raises
    ------
    ValueError
        If *symbol* is empty or does not match known patterns.
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Symbol must be a non-empty string")

    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Symbol cannot be blank after stripping whitespace")

    if not _SYMBOL_RE.match(cleaned):
        raise ValueError(
            f"Symbol {cleaned!r} does not match a recognised pattern. "
            f"Expected e.g. R_100, 1HZ10V, BOOM300, CRASH300, EUR/USD."
        )
    return cleaned


def validate_stake(
    amount: float,
    min_val: float = 0.01,
    max_val: float = 10_000.0,
) -> bool:
    """Check that *amount* is a valid stake value in range.

    Parameters
    ----------
    amount:
        Stake amount to validate.
    min_val:
        Minimum allowed stake (inclusive).
    max_val:
        Maximum allowed stake (inclusive).

    Returns
    -------
    True if the amount is a finite number within [min_val, max_val].
    """
    try:
        amount_f = float(amount)
    except (TypeError, ValueError):
        return False
    return min_val <= amount_f <= max_val


def validate_barrier(barrier: int) -> bool:
    """Check that *barrier* is a valid digit-contract barrier (0-9).

    Parameters
    ----------
    barrier:
        Barrier value to validate.

    Returns
    -------
    True if *barrier* is an integer from 0 to 9 inclusive.
    """
    if not isinstance(barrier, int):
        return False
    return 0 <= barrier <= 9


def validate_duration(duration: int, unit: str) -> bool:
    """Check that *duration* is valid for the given *unit*.

    Duration rules (for Deriv.com style contracts):

    - ``t`` (ticks):     1  ≤ duration ≤ 10
    - ``s`` (seconds):   5  ≤ duration ≤ 3600  (1 hour)
    - ``m`` (minutes):   1  ≤ duration ≤ 525_600 (1 year)
    - ``h`` (hours):     1  ≤ duration ≤ 365   (1 year)
    - ``d`` (days):      1  ≤ duration ≤ 365   (1 year)

    Parameters
    ----------
    duration:
        The duration value (must be int).
    unit:
        One of ``t``, ``s``, ``m``, ``h``, ``d``.

    Returns
    -------
    True if the duration is within valid bounds for the given unit.
    """
    if not isinstance(duration, int) or duration <= 0:
        return False

    unit = unit.strip().lower()

    bounds = {
        "t": (1, 10),
        "s": (5, 3600),
        "m": (1, 525_600),
        "h": (1, 365),
        "d": (1, 365),
    }

    if unit not in bounds:
        return False

    lo, hi = bounds[unit]
    return lo <= duration <= hi
