"""Labelled anomaly injection.

Runs AFTER the clean baseline, on its own RNG stream, and changes only the
target record (plus the records that must move with it to keep the anomaly
single-cause). Each injection writes one ground-truth row with the original
and injected values, so nothing is silently corrupted and every anomaly is
explainable and detectable by exactly one documented rule.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from returns_platform import ids
from returns_platform.config import Policy
from returns_platform.domain.money import proportional_slice
from returns_platform.generator.baseline import Dataset, _idem_key
from returns_platform.generator.settings import GenSettings, stream


def _gt(n, issue, table, record_id, field, original, injected, related, description, rule):
    return dict(anomaly_id=f"ANOM-{n:05d}", issue_type=issue, table_name=table, record_id=record_id,
                field_name=field, original_value=original, injected_value=injected,
                related_ids="|".join(related), description=description, detection_rule=rule)


def inject(s: GenSettings, policy: Policy, ds: Dataset) -> None:
    rng = stream(s.seed, "anomalies")
    T = ds.tables
    k = s.anomalies_per_type
    ol_by_id = {l["order_line_id"]: l for l in T["order_line"]}
    order_by_id = {o["order_id"]: o for o in T["sales_order"]}
    rr_by_id = {r["rma_id"]: r for r in T["return_request"]}
    ins_by_line = {i["return_line_id"]: i for i in T["inspection"]}
    auth_by_rma = {a["rma_id"]: a for a in T["return_authorization"]}
    lines_per_rma = Counter(rl["rma_id"] for rl in T["return_line"])
    rmas_per_order = Counter(r["order_id"] for r in T["return_request"])
    lines_per_ol = Counter(rl["order_line_id"] for rl in T["return_line"])
    used_lines: set[str] = set()       # one anomaly per return line -> labels stay single-cause
    gt = ds.anomalies

    def take(pool, n):
        pool = [p for p in pool if p["return_line_id"] not in used_lines]
        picked = rng.sample(pool, min(n, len(pool)))
        for p in picked:
            used_lines.add(p["return_line_id"])
        return sorted(picked, key=lambda p: p["return_line_id"])

    # 1. Return quantity exceeds purchased quantity -------------------------------
    pool = [rl for rl in T["return_line"]
            if rl["decision"] == "APPROVED" and rl["approved_qty"] == ol_by_id[rl["order_line_id"]]["quantity"]
            and lines_per_ol[rl["order_line_id"]] == 1]
    for rl in take(pool, k):
        purchased = ol_by_id[rl["order_line_id"]]["quantity"]
        bad = purchased + rng.randint(1, 3)
        gt.append(_gt(len(gt) + 1, "RETURN_QTY_EXCEEDS_PURCHASED", "return_line", rl["return_line_id"],
                      "approved_qty", rl["approved_qty"], bad, [rl["order_line_id"], rl["rma_id"]],
                      f"approved {bad} units but only {purchased} were purchased",
                      "SUM(approved_qty) per order_line > order_line.quantity"))
        auth_by_rma[rl["rma_id"]]["approved_qty_total"] += bad - rl["approved_qty"]   # moves with the line
        rl["requested_qty"] = bad
        rl["approved_qty"] = bad

    # 2. Refund exceeds paid amount ------------------------------------------------
    rl_by_id = {rl["return_line_id"]: rl for rl in T["return_line"]}
    refunds_per_line = Counter(r["return_line_id"] for r in T["refund"])
    pool = [r for r in T["refund"] if r["status"] == "SUCCEEDED" and refunds_per_line[r["return_line_id"]] == 1
            and r["amount_paise"] > 0]
    for r in take(pool, k):
        ol = ol_by_id[rl_by_id[r["return_line_id"]]["order_line_id"]]
        cap = proportional_slice(ol["line_paid_paise"], ol["quantity"], 0, r["refund_qty"])
        new_amount = cap + max(100, cap * rng.randint(10, 60) // 100)
        delta = new_amount - r["amount_paise"]
        gt.append(_gt(len(gt) + 1, "REFUND_EXCEEDS_PAID", "refund", r["refund_id"], "amount_paise",
                      r["amount_paise"], new_amount, [r["return_line_id"], ol["order_line_id"]],
                      f"refund {new_amount} paise for {r['refund_qty']} unit(s) exceeds paid {cap} paise",
                      "refund.amount_paise > line_paid_paise share for refund_qty"))
        r["item_amount_paise"] += delta          # keep the refund row internally consistent
        r["requested_amount_paise"] = new_amount
        r["amount_paise"] = new_amount

    # 3. Restock before inspection -------------------------------------------------
    ev_by_entity = {e["entity_id"]: e for e in T["return_event"] if e["entity_type"] == "INVENTORY_DISPOSITION"}
    pool = [d for d in T["inventory_disposition"] if d["disposition"] == "RESTOCK"]
    for d in take(pool, k):
        ins = ins_by_line[d["return_line_id"]]
        bad = ins["inspected_at"] - timedelta(hours=rng.randint(1, 48))
        gt.append(_gt(len(gt) + 1, "RESTOCK_BEFORE_INSPECTION", "inventory_disposition", d["disposition_id"],
                      "decided_at", d["decided_at"], bad, [d["return_line_id"], ins["inspection_id"]],
                      "units were restocked to sellable inventory before they were inspected",
                      "disposition.decided_at < inspection.inspected_at"))
        d["decided_at"] = bad
        ev_by_entity[d["disposition_id"]]["occurred_at"] = bad   # the audit log carries the same bad time

    # 4. Return after policy window without override -------------------------------
    pool = []
    for rl in T["return_line"]:
        rr = rr_by_id[rl["rma_id"]]
        o = order_by_id[rr["order_id"]]
        if (rl["decision"] == "APPROVED" and not rr["override_flag"] and lines_per_rma[rl["rma_id"]] == 1
                and rmas_per_order[rr["order_id"]] == 1):
            pool.append(rl)
    feasible = []
    for rl in pool:
        rr = rr_by_id[rl["rma_id"]]
        o = order_by_id[rr["order_id"]]
        shift = rl["window_days"] - rl["days_since_delivery"] + rng.randint(3, 25)
        if o["order_date"] - timedelta(days=shift) >= s.first_order_date:
            feasible.append((rl, shift))
    chosen = {rl["return_line_id"] for rl in take([f[0] for f in feasible], k)}
    for rl, shift in feasible:
        if rl["return_line_id"] not in chosen:
            continue
        rr = rr_by_id[rl["rma_id"]]
        o = order_by_id[rr["order_id"]]
        new_delivered = o["delivered_date"] - timedelta(days=shift)
        new_days = rl["days_since_delivery"] + shift
        gt.append(_gt(len(gt) + 1, "LATE_RETURN_WITHOUT_OVERRIDE", "return_line", rl["return_line_id"],
                      "days_since_delivery", rl["days_since_delivery"], new_days, [rr["rma_id"], o["order_id"]],
                      f"approved {new_days} days after delivery; window is {rl['window_days']} days and no override",
                      "decision approves units AND days_since_delivery > window_days AND override_flag = false"))
        o["order_date"] -= timedelta(days=shift)          # order moved earlier as a whole (dates stay ordered)
        o["delivered_date"] = new_delivered
        rl["days_since_delivery"] = new_days

    # 5. Duplicate refund (second payout that slipped past the guard) ---------------
    refunds_per_line = Counter(r["return_line_id"] for r in T["refund"])
    pool = [r for r in T["refund"] if r["status"] == "SUCCEEDED" and refunds_per_line[r["return_line_id"]] == 1
            and r["amount_paise"] > 0]
    next_ref = max(ids.parse_counter(r["refund_id"]) for r in T["refund"]) + 1
    next_evt = max(ids.parse_counter(e["event_id"]) for e in T["return_event"]) + 1
    for r in take(pool, k):
        at = r["requested_at"] + timedelta(minutes=rng.randint(2, 72 * 60))
        dup = dict(r)
        dup.update(refund_id=ids.refund_id(next_ref), attempt_no=r["attempt_no"] + 1,
                   idempotency_key=_idem_key(s.seed, "dup", r["return_line_id"]), requested_at=at,
                   processed_at=at + timedelta(minutes=rng.randint(1, 30)), duplicate_of_refund_id=None)
        next_ref += 1
        T["refund"].append(dup)
        T["return_event"].append(dict(event_id=ids.event_id(next_evt), rma_id=r["rma_id"], entity_type="REFUND",
                                      entity_id=dup["refund_id"], from_state=None, to_state="SUCCEEDED",
                                      occurred_at=at, actor_type="SYSTEM", actor_id="PAYMENTS"))
        next_evt += 1
        gt.append(_gt(len(gt) + 1, "DUPLICATE_REFUND", "refund", dup["refund_id"], "status", None, "SUCCEEDED",
                      [r["refund_id"], r["return_line_id"]],
                      f"second successful payout of {r['amount_paise']} paise for the same return line",
                      "COUNT(refund WHERE status = SUCCEEDED) per return_line > 1"))
