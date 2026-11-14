"""Exact integer money helpers. All amounts are paise; no floats anywhere."""
from __future__ import annotations


def round_half_up(numerator: int, denominator: int) -> int:
    """Round numerator/denominator to the nearest int, halves away from zero (non-negative inputs)."""
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if numerator < 0:
        raise ValueError("numerator must be non-negative")
    return (2 * numerator + denominator) // (2 * denominator)


def apply_bp(amount: int, bp: int) -> int:
    """amount * bp / 10000, floored. Flooring guarantees we never pay out more than owed."""
    return amount * bp // 10000


def allocate_pro_rata(total: int, weights: list[int]) -> list[int]:
    """Split `total` across `weights` so the parts sum EXACTLY to total.

    Largest-remainder method; ties broken by position so it is deterministic.
    """
    if total < 0 or any(w < 0 for w in weights):
        raise ValueError("negative input")
    wsum = sum(weights)
    if total == 0 or wsum == 0:
        return [0] * len(weights)
    if total > wsum:
        raise ValueError("cannot allocate more than the weight base")
    floors = [total * w // wsum for w in weights]
    remainders = [(total * w % wsum, -i) for i, w in enumerate(weights)]
    short = total - sum(floors)
    for _, neg_i in sorted(remainders, reverse=True)[:short]:
        floors[-neg_i] += 1
    return floors


def proportional_slice(base: int, total_units: int, already_units: int, units: int) -> int:
    """Value of `units` out of `total_units`, given `already_units` were sliced before.

    Uses cumulative flooring, so slicing a line in any number of partial returns
    always sums exactly to `base` once every unit is returned (no paisa drift).
    """
    if not (0 <= already_units and units >= 0 and already_units + units <= total_units):
        raise ValueError("slice out of range")
    return base * (already_units + units) // total_units - base * already_units // total_units


def fmt_inr(paise: int) -> str:
    return f"₹{paise // 100:,}.{paise % 100:02d}"
