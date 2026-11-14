"""Disposition: where inspected units go, and what that does to sellable stock.

Disposition can only be decided from an inspection — the function signature
requires a grade, and the services refuse to call it without an inspection row.
Only RESTOCK increases sellable inventory.
"""
from __future__ import annotations

from dataclasses import dataclass

from returns_platform.config import Policy
from returns_platform.domain.enums import DISPOSITION_LOCATION


@dataclass(frozen=True)
class DispositionDecision:
    disposition: str
    to_location: str
    quantity: int
    sellable_qty_delta: int


def decide_disposition(policy: Policy, *, grade: str, category: str, received_qty: int) -> DispositionDecision:
    if received_qty < 0:
        raise ValueError("received_qty must be >= 0")
    branch = policy.disposition_by_grade[grade]
    refurbishable = policy.category(category).refurbishable
    disp = branch["if_refurbishable"] if refurbishable else branch["otherwise"]
    return DispositionDecision(
        disposition=disp,
        to_location=DISPOSITION_LOCATION[disp],
        quantity=received_qty,
        sellable_qty_delta=received_qty if disp == "RESTOCK" else 0,
    )
