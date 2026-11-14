"""Operational KPIs and scenario coverage, computed from source records only."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict


def _median(xs):
    return round(statistics.median(xs), 2) if xs else None


def _pct(a, b):
    return round(100.0 * a / b, 2) if b else 0.0


def compute_metrics(T: dict[str, list[dict]], policy=None) -> dict:
    ols = {l["order_line_id"]: l for l in T["order_line"]}
    rrs = {r["rma_id"]: r for r in T["return_request"]}
    rls = {r["return_line_id"]: r for r in T["return_line"]}
    ship = {s["rma_id"]: s for s in T["return_shipment"]}
    ins = {i["return_line_id"]: i for i in T["inspection"]}
    prod = {p["product_id"]: p for p in T["product"]}

    units_sold = sum(l["quantity"] for l in T["order_line"])
    sold_by_cat = Counter()
    for l in T["order_line"]:
        sold_by_cat[l["category"]] += l["quantity"]
    req_by_cat, appr_by_cat = Counter(), Counter()
    for rl in T["return_line"]:
        cat = ols[rl["order_line_id"]]["category"]
        req_by_cat[cat] += rl["requested_qty"]
        appr_by_cat[cat] += rl["approved_qty"]

    succeeded = [r for r in T["refund"] if r["status"] == "SUCCEEDED"]
    blocked = [r for r in T["refund"] if r["status"] != "SUCCEEDED"]
    refunded_line_ids = {r["return_line_id"] for r in succeeded}
    paid_on_refunded = sum(ols[rls[x]["order_line_id"]]["line_paid_paise"] for x in refunded_line_ids)

    first_ok = {}
    for r in sorted(succeeded, key=lambda r: r["requested_at"]):
        first_ok.setdefault(r["return_line_id"], r)
    req_to_refund_days, recv_to_ins_h, ins_to_ref_h = [], [], []
    for line_id, r in first_ok.items():
        rr = rrs[rls[line_id]["rma_id"]]
        req_to_refund_days.append((r["processed_at"] - rr["requested_at"]).total_seconds() / 86400)
        i = ins.get(line_id)
        if i:
            ins_to_ref_h.append((r["requested_at"] - i["inspected_at"]).total_seconds() / 3600)
    for i in T["inspection"]:
        s = next((s for s in [ship.get(rls[i["return_line_id"]]["rma_id"])] if s), None)
        if s and s["received_at"]:
            recv_to_ins_h.append((i["inspected_at"] - s["received_at"]).total_seconds() / 3600)
    sla = (policy.sla if policy else {}) or {"receipt_to_inspection_days": 2, "inspection_to_refund_days": 3}

    disp_units = Counter()
    recovered_value = 0
    for d in T["inventory_disposition"]:
        disp_units[d["disposition"]] += d["quantity"]
        if d["disposition"] == "RESTOCK":
            recovered_value += d["quantity"] * prod[d["product_id"]]["list_price_paise"]

    return {
        "volume": {
            "customers": len(T["customer"]), "products": len(T["product"]), "orders": len(T["sales_order"]),
            "order_lines": len(T["order_line"]), "units_sold": units_sold,
            "return_requests": len(T["return_request"]), "return_lines": len(T["return_line"]),
            "events": len(T["return_event"]),
        },
        "return_rate": {
            "return_lines_per_100_order_lines": _pct(len(T["return_line"]), len(T["order_line"])),
            "units_requested_pct_of_sold": _pct(sum(req_by_cat.values()), units_sold),
            "by_category": {c: {"units_sold": sold_by_cat[c], "units_requested": req_by_cat[c],
                                "units_approved": appr_by_cat[c],
                                "request_rate_pct": _pct(req_by_cat[c], sold_by_cat[c]),
                                "approval_rate_pct": _pct(appr_by_cat[c], req_by_cat[c])}
                            for c in sorted(sold_by_cat)},
        },
        "eligibility": {
            "line_decisions": dict(Counter(rl["decision"] for rl in T["return_line"])),
            "rejection_reasons": dict(Counter(rl["rejection_reason"] for rl in T["return_line"] if rl["rejection_reason"])),
            "authorization_decisions": dict(Counter(a["decision"] for a in T["return_authorization"])),
            "window_overrides": sum(1 for r in T["return_request"] if r["override_flag"]),
            "units_requested": sum(rl["requested_qty"] for rl in T["return_line"]),
            "units_approved": sum(rl["approved_qty"] for rl in T["return_line"]),
        },
        "lifecycle": {
            "rma_status": dict(Counter(r["status"] for r in T["return_request"])),
            "shipment_status": dict(Counter(s["status"] for s in T["return_shipment"])),
            "median_request_to_refund_days": _median(req_to_refund_days),
            "median_receipt_to_inspection_hours": _median(recv_to_ins_h),
            "median_inspection_to_refund_hours": _median(ins_to_ref_h),
            "inspection_sla_breach_pct": _pct(sum(h > 24 * sla["receipt_to_inspection_days"] for h in recv_to_ins_h), len(recv_to_ins_h)),
            "refund_sla_breach_pct": _pct(sum(h > 24 * sla["inspection_to_refund_days"] for h in ins_to_ref_h), len(ins_to_ref_h)),
        },
        "refunds": {
            "payouts": len(succeeded),
            "total_refunded_paise": sum(r["amount_paise"] for r in succeeded),
            "avg_refund_paise": round(statistics.mean([r["amount_paise"] for r in succeeded])) if succeeded else 0,
            "refund_pct_of_paid_on_refunded_lines": _pct(sum(r["amount_paise"] for r in succeeded), paid_on_refunded),
            "restocking_fees_paise": sum(r["restocking_fee_paise"] for r in succeeded),
            "shipping_fees_paise": sum(r["shipping_fee_paise"] for r in succeeded),
            "blocked_attempts": dict(Counter(r["status"] for r in blocked)),
            "blocked_value_paise": sum(r["requested_amount_paise"] for r in blocked),
            "by_method": dict(Counter(r["method"] for r in succeeded)),
        },
        "inventory": {
            "units_by_disposition": dict(disp_units),
            "sellable_units_recovered": sum(d["sellable_qty_delta"] for d in T["inventory_disposition"]),
            "recovered_list_value_paise": recovered_value,
            "grade_mix": dict(Counter(i["grade"] for i in T["inspection"])),
        },
        "reasons": {
            "by_reason": dict(Counter(rl["reason_code"] for rl in T["return_line"])),
            "merchant_fault_pct": _pct(sum(1 for rl in T["return_line"] if policy and policy.is_merchant_fault(rl["reason_code"])),
                                       len(T["return_line"])) if policy else None,
        },
        "operations": operations(T),
    }


OPEN_STATES = ("REQUESTED", "AUTHORIZED", "IN_TRANSIT", "RECEIVED", "INSPECTED")


def operations(T) -> dict:
    """What an operations manager steers by: backlog and its age, carrier reliability,
    who overrides policy, and weekly throughput. Ages are measured at the latest event
    time (the data's own 'now'), so results don't depend on when you run this."""
    events = T["return_event"]
    as_of = max((e["occurred_at"] for e in events), default=None)
    last_change = {}
    completed_week = Counter()
    for e in events:
        if e["entity_type"] == "RETURN_REQUEST":
            last_change[e["entity_id"]] = max(last_change.get(e["entity_id"], e["occurred_at"]), e["occurred_at"])
            if e["to_state"] == "COMPLETED":
                y, w, _ = e["occurred_at"].isocalendar()
                completed_week[f"{y}-W{w:02d}"] += 1
    backlog = {}
    for st in OPEN_STATES:
        ages = [(as_of - last_change[r["rma_id"]]).total_seconds() / 86400
                for r in T["return_request"] if r["status"] == st and r["rma_id"] in last_change]
        backlog[st] = {"count": len(ages), "oldest_days": round(max(ages), 1) if ages else 0,
                       "over_7_days": sum(a > 7 for a in ages)}

    carriers = {}
    for c in sorted({s["carrier"] for s in T["return_shipment"]}):
        ships = [s for s in T["return_shipment"] if s["carrier"] == c and s["shipped_at"]]
        lost = sum(1 for s in ships if s["status"] == "LOST")
        transit = [(s["received_at"] - s["shipped_at"]).total_seconds() / 86400 for s in ships if s["received_at"]]
        carriers[c] = {"shipped": len(ships), "lost": lost, "lost_pct": _pct(lost, len(ships)),
                       "median_transit_days": _median(transit)}

    by_op = Counter(r["override_by"] for r in T["return_request"] if r["override_flag"])
    weeks = sorted(completed_week)[-8:]
    return {
        "as_of": as_of.isoformat(sep=" ") if as_of else None,
        "backlog": backlog,
        "open_total": sum(b["count"] for b in backlog.values()),
        "carriers": carriers,
        "overrides_by_operator": dict(by_op.most_common(10)),
        "overrides_total": sum(by_op.values()),
        "completed_per_week": {w: completed_week[w] for w in weeks},
    }


def coverage(T) -> dict:
    """Counts of every scenario the brief asks for, so coverage is evidenced, not asserted."""
    ols = {l["order_line_id"]: l for l in T["order_line"]}
    rrs = {r["rma_id"]: r for r in T["return_request"]}
    rls = {r["return_line_id"]: r for r in T["return_line"]}
    lines_per_order = Counter(l["order_id"] for l in T["order_line"])
    rl_per_rma = Counter(rl["rma_id"] for rl in T["return_line"])
    rmas_per_order = Counter(r["order_id"] for r in T["return_request"])
    ins = T["inspection"]
    return {
        "partial_qty_returns": sum(1 for rl in T["return_line"] if 0 < rl["requested_qty"] < ols[rl["order_line_id"]]["quantity"]),
        "partial_order_returns (subset of lines)": sum(1 for r in T["return_request"] if rl_per_rma[r["rma_id"]] < lines_per_order[r["order_id"]]),
        "late_requests": sum(1 for rl in T["return_line"] if rl["days_since_delivery"] > rl["window_days"] and rl["window_days"] > 0),
        "late_requests_with_override_approved": sum(1 for rl in T["return_line"] if rl["days_since_delivery"] > rl["window_days"] > 0 and rrs[rl["rma_id"]]["override_flag"] and rl["approved_qty"] > 0),
        "category_exclusions": sum(1 for rl in T["return_line"] if rl["rejection_reason"] == "CATEGORY_EXCLUDED"),
        "qty_over_requests_clamped": sum(1 for rl in T["return_line"] if rl["rejection_reason"] == "QTY_EXCEEDS_RETURNABLE"),
        "follow_up_rmas_same_order": sum(n - 1 for n in rmas_per_order.values() if n > 1),
        "nothing_left_to_return": sum(1 for rl in T["return_line"] if rl["rejection_reason"] == "NOTHING_RETURNABLE"),
        "exchanges": sum(1 for rl in T["return_line"] if rl["resolution"] == "EXCHANGE"),
        "damaged_goods (grade D)": sum(1 for i in ins if i["grade"] == "D_DAMAGED"),
        "defective_goods (grade E)": sum(1 for i in ins if i["grade"] == "E_DEFECTIVE"),
        "missing_shipments (lost)": sum(1 for s in T["return_shipment"] if s["status"] == "LOST"),
        "never_shipped (expired)": sum(1 for s in T["return_shipment"] if s["status"] == "EXPIRED"),
        "short_or_empty_receipts": sum(1 for i in ins if i["received_qty"] < rls[i["return_line_id"]]["approved_qty"]),
        "duplicate_refund_attempts_blocked": sum(1 for r in T["refund"] if r["status"] == "REJECTED_DUPLICATE"),
        "overpay_attempts_blocked": sum(1 for r in T["refund"] if r["status"] == "REJECTED_EXCEEDS_PAID"),
        "in_flight_at_snapshot": sum(1 for r in T["return_request"] if r["status"] in ("AUTHORIZED", "IN_TRANSIT", "RECEIVED", "INSPECTED")),
    }
