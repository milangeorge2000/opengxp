"""Exact decimal arithmetic. ICH examples use 'conventional' (half-up) rounding, which Python's
round() does not do (it rounds half to even), so all comparisons go through Decimal."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def D(x: float | int | str | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def decimals_of(s: str | Decimal) -> int:
    """Number of decimal places written in a threshold, e.g. '0.10' -> 2, '2' -> 0."""
    exp = D(s).as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def round_half_up(x: float | str | Decimal, places: int) -> Decimal:
    q = Decimal(1).scaleb(-places)
    return D(x).quantize(q, rounding=ROUND_HALF_UP)
