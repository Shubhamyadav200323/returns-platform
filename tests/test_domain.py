from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from returns_platform.config import load_policy
from returns_platform.domain.disposition import decide_disposition
from returns_platform.domain.eligibility import aggregate_decision, evaluate_line
from returns_platform.domain.lifecycle import IllegalTransition, assert_transition, is_valid_path
from returns_platform.domain.money import allocate_pro_rata, proportional_slice, round_half_up
from returns_platform.domain.pricing import LineInput, price_order
from returns_platform.domain.refund import (
    LineEconomics, RefundExceedsPaid, compute_refund, refund_guard, refund_method,
)

P = load_policy()
D0 = date(2026, 3, 1)


# ---------- money ----------
def test_round_half_up():
    assert round_half_up(5, 10) == 1
    assert round_half_up(4, 10) == 0
    assert round_half_up(15, 10) == 2


@given(st.lists(st.integers(1, 10**7), min_size=1, max_size=8), st.data())
def test_allocation_sums_exactly(weights, data):
    total = data.draw(st.integers(0, sum(weights)))
    parts = allocate_pro_rata(total, weights)
    assert sum(parts) == total
    assert all(p >= 0 for p in parts)


@given(st.integers(0, 10**8), st.integers(1, 20), st.data())
def test_partial_slices_reconcile(base, units, data):
    cuts = data.draw(st.lists(st.integers(1, units), max_size=units))
    done, total = 0, 0
    for c in cuts:
        c = min(c, units - done)
        total += proportional_slice(base, units, done, c)
        done += c
    total += proportional_slice(base, units, done, units - done)
    assert total == base


# ---------- pricing ----------
def test_price_waterfall_reconciles():
    o = price_order(
        [LineInput(2, 49_900, 5_000, 1800), LineInput(1, 129_900, 0, 1200)],
        order_discount_paise=10_000, shipping_fee_paise=4_900,
    )
    assert o.subtotal_paise == 2 * 49_900 + 129_900
    assert sum(l.allocated_order_discount_paise for l in o.lines) == 10_000
    for l in o.lines:
        assert l.taxable_paise == l.gross_paise - l.line_discount_paise - l.allocated_order_discount_paise
        assert l.line_paid_paise == l.taxable_paise + l.tax_paise
    assert o.total_paid_paise == sum(l.line_paid_paise for l in o.lines) + 4_900


# ---------- eligibility ----------
def elig(**kw):
    base = dict(category="APPAREL", purchased_qty=3, previously_approved_qty=0, requested_qty=1,
                delivered_date=D0, request_date=date(2026, 3, 10))
    base.update(kw)
    return evaluate_line(P, **base)


def test_within_window_approved():
    d = elig()
    assert (d.decision, d.approved_qty, d.rejection_reason) == ("APPROVED", 1, None)


def test_window_boundary_inclusive():
    assert elig(request_date=date(2026, 3, 31)).decision == "APPROVED"      # day 30
    assert elig(request_date=date(2026, 4, 1)).rejection_reason == "WINDOW_EXPIRED"  # day 31


def test_override_bypasses_window_only():
    assert elig(request_date=date(2026, 5, 1), override=True).decision == "APPROVED"
    d = elig(category="BEAUTY", override=True)
    assert d.rejection_reason == "CATEGORY_EXCLUDED"


def test_quantity_clamped_to_returnable():
    d = elig(requested_qty=5, previously_approved_qty=1)
    assert (d.decision, d.approved_qty, d.rejection_reason) == ("PARTIALLY_APPROVED", 2, "QTY_EXCEEDS_RETURNABLE")


def test_nothing_returnable():
    assert elig(previously_approved_qty=3).rejection_reason == "NOTHING_RETURNABLE"


def test_request_before_delivery_rejected():
    with pytest.raises(ValueError):
        elig(request_date=date(2026, 2, 1))


def test_aggregate_decision():
    a, r = elig(), elig(category="GROCERY")
    assert aggregate_decision([a, a]) == "APPROVED"
    assert aggregate_decision([a, r]) == "PARTIALLY_APPROVED"
    assert aggregate_decision([r]) == "REJECTED"


@given(st.integers(1, 10), st.integers(0, 10), st.integers(1, 15), st.integers(0, 60))
def test_approved_never_exceeds_returnable(purchased, prior, requested, days):
    prior = min(prior, purchased)
    d = evaluate_line(P, category="TOYS", purchased_qty=purchased, previously_approved_qty=prior,
                      requested_qty=requested, delivered_date=D0,
                      request_date=date.fromordinal(D0.toordinal() + days))
    assert 0 <= d.approved_qty <= purchased - prior
    assert d.approved_qty <= requested


# ---------- refund ----------
LINE = LineEconomics(quantity=2, taxable_paise=90_000, tax_paise=16_200, line_paid_paise=106_200)


def test_merchant_fault_full_refund_no_fees():
    r = compute_refund(P, LINE, category="ELECTRONICS", reason_code="DEFECTIVE", grade="D_DAMAGED", refund_qty=2)
    assert r.grade_refund_bp == 10000
    assert (r.restocking_fee_paise, r.shipping_fee_paise) == (0, 0)
    assert r.amount_paise == LINE.line_paid_paise


def test_customer_fault_fees_and_grade():
    r = compute_refund(P, LINE, category="ELECTRONICS", reason_code="CHANGED_MIND", grade="C_USED", refund_qty=1)
    assert r.item_amount_paise == 45_000 * 8000 // 10000
    assert r.tax_amount_paise == 8_100 * 8000 // 10000
    assert r.restocking_fee_paise == r.item_amount_paise * 1000 // 10000
    assert r.shipping_fee_paise == P.return_shipping_fee_paise
    assert r.amount_paise == r.item_amount_paise + r.tax_amount_paise - r.restocking_fee_paise - r.shipping_fee_paise


def test_shipping_fee_never_makes_refund_negative():
    tiny = LineEconomics(1, 5_000, 0, 5_000)
    r = compute_refund(P, tiny, category="BOOKS", reason_code="CHANGED_MIND", grade="D_DAMAGED", refund_qty=1)
    assert r.amount_paise == 0 and r.shipping_fee_paise == 2_500


def test_cap_enforced():
    with pytest.raises(RefundExceedsPaid):
        compute_refund(P, LINE, category="APPAREL", reason_code="DEFECTIVE", grade="A_NEW", refund_qty=1,
                       previously_refunded_qty=1, previously_refunded_paise=100_000)


@settings(max_examples=300)
@given(st.integers(1, 6), st.integers(100, 10**7), st.sampled_from(sorted(P.grade_refund_bp)),
       st.sampled_from(sorted(P.reason_fault)), st.sampled_from(["ELECTRONICS", "APPAREL", "BOOKS"]),
       st.data())
def test_refunds_never_exceed_paid(qty, taxable, grade, reason, cat, data):
    tax = taxable * P.category(cat).tax_rate_bp // 10000
    line = LineEconomics(qty, taxable, tax, taxable + tax)
    done, paid = 0, 0
    while done < qty:
        q = data.draw(st.integers(1, qty - done))
        r = compute_refund(P, line, category=cat, reason_code=reason, grade=grade, refund_qty=q,
                           previously_refunded_qty=done, previously_refunded_paise=paid,
                           charge_shipping_fee=(done == 0))
        assert r.amount_paise >= 0
        done += q
        paid += r.amount_paise
    assert paid <= line.line_paid_paise


def test_refund_guard():
    rows = [{"refund_id": "REF-2026-000001", "idempotency_key": "k1", "status": "SUCCEEDED"}]
    assert refund_guard([], "k1").outcome == "NEW"
    assert refund_guard(rows, "k1").outcome == "REPLAY"
    g = refund_guard(rows, "k2")
    assert (g.outcome, g.existing_refund_id) == ("DUPLICATE", "REF-2026-000001")


def test_refund_method():
    assert refund_method("COD") == "STORE_CREDIT"
    assert refund_method("UPI") == "ORIGINAL_PAYMENT"


# ---------- disposition ----------
@pytest.mark.parametrize("grade,cat,expected", [
    ("A_NEW", "APPAREL", "RESTOCK"),
    ("C_USED", "ELECTRONICS", "REFURBISH"),
    ("C_USED", "APPAREL", "SCRAP"),
    ("D_DAMAGED", "TOYS", "SCRAP"),
    ("E_DEFECTIVE", "ELECTRONICS", "QUARANTINE"),
])
def test_disposition_rules(grade, cat, expected):
    d = decide_disposition(P, grade=grade, category=cat, received_qty=2)
    assert d.disposition == expected
    assert d.sellable_qty_delta == (2 if expected == "RESTOCK" else 0)


# ---------- lifecycle ----------
def test_happy_path_valid():
    assert is_valid_path("RETURN_REQUEST", ["REQUESTED", "AUTHORIZED", "IN_TRANSIT", "RECEIVED", "INSPECTED", "COMPLETED"])


def test_cannot_skip_inspection():
    with pytest.raises(IllegalTransition):
        assert_transition("RETURN_REQUEST", "RECEIVED", "COMPLETED")
    assert not is_valid_path("RETURN_REQUEST", ["REQUESTED", "REJECTED", "AUTHORIZED"])
