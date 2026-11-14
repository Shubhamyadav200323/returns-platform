"""Return eligibility: category exclusions, return window, returnable quantity.

Order of evaluation is deliberate and documented, because it decides which
rejection reason is reported when several apply:
  1. CATEGORY_EXCLUDED   — hard rule, an override can NOT bypass it
  2. WINDOW_EXPIRED      — bypassed only by an explicit, attributed override
  3. NOTHING_RETURNABLE  — everything purchased was already approved on earlier RMAs
  4. QTY_EXCEEDS_RETURNABLE — request clamped to the returnable quantity (partial approval)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from returns_platform.config import Policy


@dataclass(frozen=True)
class LineDecision:
    decision: str                 # APPROVED | PARTIALLY_APPROVED | REJECTED
    approved_qty: int
    rejection_reason: str | None
    days_since_delivery: int
    window_days: int


def evaluate_line(
    policy: Policy,
    *,
    category: str,
    purchased_qty: int,
    previously_approved_qty: int,
    requested_qty: int,
    delivered_date: date,
    request_date: date,
    override: bool = False,
) -> LineDecision:
    if requested_qty < 1:
        raise ValueError("requested_qty must be >= 1")
    if request_date < delivered_date:
        raise ValueError("cannot request a return before delivery")
    rule = policy.category(category)
    days = (request_date - delivered_date).days

    def reject(reason: str) -> LineDecision:
        return LineDecision("REJECTED", 0, reason, days, rule.window_days)

    if not rule.returnable:
        return reject("CATEGORY_EXCLUDED")
    if days > rule.window_days and not override:
        return reject("WINDOW_EXPIRED")
    returnable = purchased_qty - previously_approved_qty
    if returnable <= 0:
        return reject("NOTHING_RETURNABLE")
    if requested_qty > returnable:
        return LineDecision("PARTIALLY_APPROVED", returnable, "QTY_EXCEEDS_RETURNABLE", days, rule.window_days)
    return LineDecision("APPROVED", requested_qty, None, days, rule.window_days)


def aggregate_decision(line_decisions: list[LineDecision]) -> str:
    approved = [d for d in line_decisions if d.approved_qty > 0]
    if not approved:
        return "REJECTED"
    if all(d.decision == "APPROVED" for d in line_decisions):
        return "APPROVED"
    return "PARTIALLY_APPROVED"
