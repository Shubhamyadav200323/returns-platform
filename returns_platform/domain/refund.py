"""Refund calculation and duplicate-payout guard.

Refund for `q` units of a line =
    item  = slice(taxable_paise) × grade factor
    tax   = slice(tax_paise)     × grade factor        (tax follows the value refunded)
  − restocking fee  (customer-fault reasons only; category rate × item)
  − return shipping (customer-fault only; flat fee, once per RMA, never below zero)

`slice` is the cumulative proportional share of the line (see money.proportional_slice),
so returning a line in several parts reconciles exactly to what was paid.
Outbound shipping on the order is never refunded.
Invariant enforced here: cumulative refunds on a line ≤ line_paid_paise.
"""
from __future__ import annotations

from dataclasses import dataclass

from returns_platform.config import Policy
from returns_platform.domain.money import apply_bp, proportional_slice


class RefundExceedsPaid(Exception):
    pass


@dataclass(frozen=True)
class LineEconomics:
    quantity: int
    taxable_paise: int
    tax_paise: int
    line_paid_paise: int


@dataclass(frozen=True)
class RefundCalc:
    refund_qty: int
    item_amount_paise: int
    tax_amount_paise: int
    grade_refund_bp: int
    restocking_fee_paise: int
    shipping_fee_paise: int
    amount_paise: int


def grade_factor_bp(policy: Policy, grade: str, reason_code: str) -> int:
    bp = policy.grade_refund_bp[grade]
    if policy.is_merchant_fault(reason_code):
        bp = max(bp, policy.merchant_fault_grade_floor_bp)
    return bp


def compute_refund(
    policy: Policy,
    line: LineEconomics,
    *,
    category: str,
    reason_code: str,
    grade: str,
    refund_qty: int,
    previously_refunded_qty: int = 0,
    previously_refunded_paise: int = 0,
    charge_shipping_fee: bool = True,
) -> RefundCalc:
    if refund_qty < 0:
        raise ValueError("refund_qty must be >= 0")
    item_base = proportional_slice(line.taxable_paise, line.quantity, previously_refunded_qty, refund_qty)
    tax_base = proportional_slice(line.tax_paise, line.quantity, previously_refunded_qty, refund_qty)
    bp = grade_factor_bp(policy, grade, reason_code)
    item = apply_bp(item_base, bp)
    tax = apply_bp(tax_base, bp)

    customer_fault = not policy.is_merchant_fault(reason_code)
    restock = apply_bp(item, policy.category(category).restocking_fee_bp) if customer_fault else 0
    ship = 0
    if customer_fault and charge_shipping_fee:
        ship = min(policy.return_shipping_fee_paise, item + tax - restock)
    amount = item + tax - restock - ship

    if previously_refunded_paise + amount > line.line_paid_paise:
        raise RefundExceedsPaid(
            f"refund {amount} + prior {previously_refunded_paise} exceeds paid {line.line_paid_paise}"
        )
    return RefundCalc(refund_qty, item, tax, bp, restock, ship, amount)


@dataclass(frozen=True)
class GuardResult:
    outcome: str                       # NEW | REPLAY | DUPLICATE
    existing_refund_id: str | None = None


def refund_guard(existing: list[dict], idempotency_key: str) -> GuardResult:
    """Decide what to do with a refund request for one return line.

    existing: prior refund rows for the SAME return line (dicts with refund_id,
    idempotency_key, status).
      * same idempotency key seen before → REPLAY (return stored result, no new row)
      * a SUCCEEDED refund already exists → DUPLICATE (record a blocked attempt)
      * otherwise                         → NEW
    """
    for r in existing:
        if r["idempotency_key"] == idempotency_key:
            return GuardResult("REPLAY", r["refund_id"])
    for r in existing:
        if r["status"] == "SUCCEEDED":
            return GuardResult("DUPLICATE", r["refund_id"])
    return GuardResult("NEW")


def refund_method(payment_method: str) -> str:
    """COD orders cannot be reversed to the original tender."""
    return "STORE_CREDIT" if payment_method == "COD" else "ORIGINAL_PAYMENT"
