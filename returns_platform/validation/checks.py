"""Validation suite.

Each check declares which ground-truth issue types it is EXPECTED to catch:
  * structural / integrity checks expect nothing → must flag zero rows
  * business-rule detectors expect specific issue types → must flag exactly
    the labelled records (precision = recall = 1.0)
A check passes only if what it flags equals what ground truth says it should.
This turns validation into a measurable evaluation instead of a pass/fail hope.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from returns_platform import ids
from returns_platform.config import Policy
from returns_platform.domain.disposition import decide_disposition
from returns_platform.domain.eligibility import aggregate_decision, evaluate_line
from returns_platform.domain.enums import DISPOSITION_LOCATION
from returns_platform.domain.lifecycle import is_valid_path
from returns_platform.domain.money import proportional_slice, round_half_up
from returns_platform.domain.refund import LineEconomics, RefundExceedsPaid, compute_refund
from returns_platform.schema.spec import SCHEMA, TABLES


@dataclass
class CheckResult:
    name: str
    group: str
    description: str
    expects: tuple[str, ...] = ()
    flagged: set[str] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)
    expected_ids: set[str] = field(default_factory=set)

    def flag(self, record_id: str, why: str = ""):
        if record_id not in self.flagged and len(self.examples) < 5:
            self.examples.append(f"{record_id}: {why}" if why else record_id)
        self.flagged.add(record_id)

    @property
    def true_pos(self):
        return len(self.flagged & self.expected_ids)

    @property
    def false_pos(self):
        return len(self.flagged - self.expected_ids)

    @property
    def false_neg(self):
        return len(self.expected_ids - self.flagged)

    @property
    def passed(self) -> bool:
        return self.flagged == self.expected_ids

    def as_dict(self):
        return dict(name=self.name, group=self.group, description=self.description,
                    expects=list(self.expects), flagged=len(self.flagged), expected=len(self.expected_ids),
                    true_positive=self.true_pos, false_positive=self.false_pos, false_negative=self.false_neg,
                    status="PASS" if self.passed else "FAIL", examples=self.examples)


class Suite:
    def __init__(self):
        self.results: list[CheckResult] = []

    def check(self, name, group, description, expects=()):
        r = CheckResult(name, group, description, tuple(expects))
        self.results.append(r)
        return r


# ======================================================================= structural
def structural_checks(suite: Suite, T: dict[str, list[dict]]):
    pk_index = {t.name: {r[t.pk.name] for r in T[t.name]} for t in SCHEMA}
    for t in SCHEMA:
        rows = T[t.name]
        pkname = t.pk.name
        c = suite.check(f"{t.name}.pk_unique", "integrity", f"{pkname} is unique and non-null")
        seen = Counter(r[pkname] for r in rows)
        for k, n in seen.items():
            if k is None or n > 1:
                c.flag(str(k), f"appears {n}x")
        c = suite.check(f"{t.name}.id_format", "integrity", "ids match the naming convention")
        for col in t.cols:
            if col.id_kind:
                pat = ids.PATTERNS[col.id_kind]
                for r in rows:
                    v = r[col.name]
                    if v is not None and not pat.match(v):
                        c.flag(r[pkname], f"{col.name}={v}")
        c = suite.check(f"{t.name}.required_enum_range", "integrity", "required fields present, enums and ranges valid")
        for col in t.cols:
            for r in rows:
                v = r[col.name]
                if v is None:
                    if col.required:
                        c.flag(r[pkname], f"{col.name} missing")
                    continue
                if col.allowed and v not in col.allowed:
                    c.flag(r[pkname], f"{col.name}={v} not allowed")
                if col.min is not None and v < col.min:
                    c.flag(r[pkname], f"{col.name}={v} < {col.min}")
                if col.max is not None and v > col.max:
                    c.flag(r[pkname], f"{col.name}={v} > {col.max}")
        c = suite.check(f"{t.name}.fk_integrity", "integrity", "every foreign key resolves to a parent row")
        for col in t.cols:
            if col.fk:
                parent = col.fk.split(".")[0]
                for r in rows:
                    v = r[col.name]
                    if v is not None and v not in pk_index[parent]:
                        c.flag(r[pkname], f"{col.name}={v} missing in {parent}")
        c = suite.check(f"{t.name}.unique_constraints", "integrity", "unique columns / composite keys hold")
        for col in t.cols:
            if col.unique and not col.pk:
                cnt = Counter(r[col.name] for r in rows if r[col.name] is not None)
                for r in rows:
                    if r[col.name] is not None and cnt[r[col.name]] > 1:
                        c.flag(r[pkname], f"duplicate {col.name}")
        for cols in t.unique_together:
            cnt = Counter(tuple(r[x] for x in cols) for r in rows)
            for r in rows:
                if cnt[tuple(r[x] for x in cols)] > 1:
                    c.flag(r[pkname], f"duplicate {cols}")


# ======================================================================= relational / derived
def consistency_checks(suite: Suite, T, policy: Policy):
    cust = {r["customer_id"]: r for r in T["customer"]}
    prod = {r["product_id"]: r for r in T["product"]}
    orders = {r["order_id"]: r for r in T["sales_order"]}
    ols = {r["order_line_id"]: r for r in T["order_line"]}
    rrs = {r["rma_id"]: r for r in T["return_request"]}
    rls = {r["return_line_id"]: r for r in T["return_line"]}
    auth = {r["rma_id"]: r for r in T["return_authorization"]}
    ship = {r["rma_id"]: r for r in T["return_shipment"]}
    ins_by_line = {r["return_line_id"]: r for r in T["inspection"]}
    disp_by_line = {r["return_line_id"]: r for r in T["inventory_disposition"]}
    lines_by_order = defaultdict(list)
    for l in T["order_line"]:
        lines_by_order[l["order_id"]].append(l)
    rl_by_rma = defaultdict(list)
    for rl in T["return_line"]:
        rl_by_rma[rl["rma_id"]].append(rl)
    refunds_by_line = defaultdict(list)
    for r in T["refund"]:
        refunds_by_line[r["return_line_id"]].append(r)

    # ---- arithmetic reconciliation
    c = suite.check("order_line.price_waterfall", "arithmetic",
                    "taxable = gross − discounts; tax = round(taxable × rate); paid = taxable + tax")
    for l in T["order_line"]:
        gross = l["quantity"] * l["unit_price_paise"]
        if l["taxable_paise"] != gross - l["line_discount_paise"] - l["allocated_order_discount_paise"]:
            c.flag(l["order_line_id"], "taxable mismatch")
        if l["tax_paise"] != round_half_up(l["taxable_paise"] * l["tax_rate_bp"], 10000):
            c.flag(l["order_line_id"], "tax mismatch")
        if l["line_paid_paise"] != l["taxable_paise"] + l["tax_paise"]:
            c.flag(l["order_line_id"], "paid mismatch")
        if l["tax_rate_bp"] != policy.category(l["category"]).tax_rate_bp:
            c.flag(l["order_line_id"], "tax rate not per policy")

    c = suite.check("sales_order.totals_reconcile", "arithmetic", "order header totals equal the sum of its lines")
    for o in T["sales_order"]:
        ls = lines_by_order[o["order_id"]]
        if not ls:
            c.flag(o["order_id"], "order without lines")
            continue
        sums = dict(
            subtotal_paise=sum(l["quantity"] * l["unit_price_paise"] for l in ls),
            line_discount_paise=sum(l["line_discount_paise"] for l in ls),
            order_discount_paise=sum(l["allocated_order_discount_paise"] for l in ls),
            tax_paise=sum(l["tax_paise"] for l in ls),
            total_paid_paise=sum(l["line_paid_paise"] for l in ls) + o["shipping_fee_paise"],
        )
        for k, v in sums.items():
            if o[k] != v:
                c.flag(o["order_id"], f"{k} {o[k]} != {v}")

    c = suite.check("order_line.category_snapshot", "consistency", "line category equals the product's category")
    for l in T["order_line"]:
        if prod[l["product_id"]]["category"] != l["category"]:
            c.flag(l["order_line_id"])

    c = suite.check("return_authorization.aggregates", "arithmetic", "decision and totals aggregate the RMA's lines")
    for rma, a in auth.items():
        lines = rl_by_rma[rma]
        if a["approved_qty_total"] != sum(rl["approved_qty"] for rl in lines):
            c.flag(a["auth_id"], "approved_qty_total")
        if a["approved_line_count"] != sum(1 for rl in lines if rl["approved_qty"] > 0):
            c.flag(a["auth_id"], "approved_line_count")
        if (a["ship_by_date"] is None) != (a["decision"] == "REJECTED"):
            c.flag(a["auth_id"], "ship_by_date presence")

    # ---- relational consistency across entities
    c = suite.check("return.cross_entity_links", "consistency",
                    "RMA customer = order customer; return lines belong to the RMA's order; child rows point to the same RMA")
    for rr in T["return_request"]:
        if orders[rr["order_id"]]["customer_id"] != rr["customer_id"]:
            c.flag(rr["rma_id"], "customer differs from order")
        if rr["rma_id"] not in auth:
            c.flag(rr["rma_id"], "no authorization")
    for rl in T["return_line"]:
        if ols[rl["order_line_id"]]["order_id"] != rrs[rl["rma_id"]]["order_id"]:
            c.flag(rl["return_line_id"], "order line from another order")
    for i in T["inspection"]:
        rl = rls[i["return_line_id"]]
        if ship.get(rl["rma_id"], {}).get("rts_id") != i["rts_id"]:
            c.flag(i["inspection_id"], "inspection shipment not the RMA's shipment")
    for r in T["refund"]:
        if rls[r["return_line_id"]]["rma_id"] != r["rma_id"]:
            c.flag(r["refund_id"], "refund rma mismatch")
    for d in T["inventory_disposition"]:
        rl = rls[d["return_line_id"]]
        if ins_by_line.get(d["return_line_id"], {}).get("inspection_id") != d["inspection_id"]:
            c.flag(d["disposition_id"], "inspection belongs to another line")
        if ols[rl["order_line_id"]]["product_id"] != d["product_id"]:
            c.flag(d["disposition_id"], "product differs from returned line")

    c = suite.check("return_line.decision_fields", "consistency",
                    "rejection_reason iff not APPROVED; approved=0 iff REJECTED; override fields attributed; exchange SKU present")
    for rl in T["return_line"]:
        rid = rl["return_line_id"]
        if (rl["rejection_reason"] is None) != (rl["decision"] == "APPROVED"):
            c.flag(rid, "rejection_reason presence")
        if (rl["approved_qty"] == 0) != (rl["decision"] == "REJECTED"):
            c.flag(rid, "approved_qty vs decision")
        if (rl["resolution"] == "EXCHANGE") != (rl["exchange_product_id"] is not None):
            c.flag(rid, "exchange product presence")
        if rl["resolution"] == "EXCHANGE" and rrs[rl["rma_id"]]["request_type"] != "EXCHANGE":
            c.flag(rid, "exchange resolution on refund request")
    for rr in T["return_request"]:
        if rr["override_flag"] != (rr["override_reason"] is not None) or rr["override_flag"] != (rr["override_by"] is not None):
            c.flag(rr["rma_id"], "override attribution")

    c = suite.check("return_line.days_since_delivery", "arithmetic", "days_since_delivery = request date − delivered date")
    for rl in T["return_line"]:
        rr = rrs[rl["rma_id"]]
        days = (rr["requested_at"].date() - orders[rr["order_id"]]["delivered_date"]).days
        if days != rl["days_since_delivery"]:
            c.flag(rl["return_line_id"], f"{rl['days_since_delivery']} != {days}")
        if rl["window_days"] != policy.category(ols[rl["order_line_id"]]["category"]).window_days:
            c.flag(rl["return_line_id"], "window not per policy")

    # ---- date ordering
    c = suite.check("dates.ordering", "date_order",
                    "order ≤ delivery ≤ request ≤ decision ≤ label ≤ ship ≤ receipt ≤ inspection ≤ refund")
    for o in T["sales_order"]:
        if o["delivered_date"] < o["order_date"]:
            c.flag(o["order_id"], "delivered before ordered")
    for rr in T["return_request"]:
        o = orders[rr["order_id"]]
        if rr["requested_at"].date() < o["delivered_date"]:
            c.flag(rr["rma_id"], "requested before delivery")
        a = auth.get(rr["rma_id"])
        if a and a["decided_at"] < rr["requested_at"]:
            c.flag(a["auth_id"], "decided before requested")
        s = ship.get(rr["rma_id"])
        if s:
            if a and s["label_created_at"] < a["decided_at"]:
                c.flag(s["rts_id"], "label before authorization")
            if s["shipped_at"] and s["shipped_at"] < s["label_created_at"]:
                c.flag(s["rts_id"], "shipped before label")
            if s["shipped_at"] and a and a["ship_by_date"] and s["shipped_at"].date() > a["ship_by_date"]:
                c.flag(s["rts_id"], "shipped after ship-by date")
            if s["received_at"] and (s["shipped_at"] is None or s["received_at"] < s["shipped_at"]):
                c.flag(s["rts_id"], "received before shipped")
    rts = {s["rts_id"]: s for s in T["return_shipment"]}
    for i in T["inspection"]:
        s = rts[i["rts_id"]]
        if s["received_at"] is None or i["inspected_at"] < s["received_at"]:
            c.flag(i["inspection_id"], "inspected before receipt")
    for r in T["refund"]:
        i = ins_by_line.get(r["return_line_id"])
        if i is None or r["requested_at"] < i["inspected_at"]:
            c.flag(r["refund_id"], "refund before inspection")
        if r["processed_at"] and r["processed_at"] < r["requested_at"]:
            c.flag(r["refund_id"], "processed before requested")

    # ---- lifecycle
    c = suite.check("lifecycle.event_paths", "lifecycle",
                    "event history per RMA/shipment is a legal state-machine path and status = latest event")
    by_entity = defaultdict(list)
    for e in T["return_event"]:
        by_entity[(e["entity_type"], e["entity_id"])].append(e)
    for key in by_entity:
        by_entity[key].sort(key=lambda e: (e["occurred_at"], e["event_id"]))
    for rr in T["return_request"]:
        evs = by_entity.get(("RETURN_REQUEST", rr["rma_id"]), [])
        states = [e["to_state"] for e in evs]
        if not is_valid_path("RETURN_REQUEST", states):
            c.flag(rr["rma_id"], f"illegal path {states}")
        elif states[-1] != rr["status"]:
            c.flag(rr["rma_id"], f"status {rr['status']} != last event {states[-1]}")
        if any(e["from_state"] != (evs[i - 1]["to_state"] if i else None) for i, e in enumerate(evs)):
            c.flag(rr["rma_id"], "from_state does not chain")
    for s in T["return_shipment"]:
        states = [e["to_state"] for e in by_entity.get(("RETURN_SHIPMENT", s["rts_id"]), [])]
        if not is_valid_path("RETURN_SHIPMENT", states) or states[-1] != s["status"]:
            c.flag(s["rts_id"], f"shipment path {states}")
        if (s["received_at"] is not None) != (s["status"] == "RECEIVED"):
            c.flag(s["rts_id"], "received_at vs status")
    for r in T["refund"]:
        evs = by_entity.get(("REFUND", r["refund_id"]), [])
        if len(evs) != 1 or evs[0]["to_state"] != r["status"]:
            c.flag(r["refund_id"], "refund event missing or mismatched")
    for d in T["inventory_disposition"]:
        evs = by_entity.get(("INVENTORY_DISPOSITION", d["disposition_id"]), [])
        if len(evs) != 1 or evs[0]["to_state"] != d["disposition"]:
            c.flag(d["disposition_id"], "disposition event missing or mismatched")

    c = suite.check("lifecycle.state_artifacts", "lifecycle",
                    "each RMA state has exactly the downstream records it implies (no refunds before receipt, etc.)")
    for rr in T["return_request"]:
        st, rma = rr["status"], rr["rma_id"]
        lines = rl_by_rma[rma]
        has_ship = rma in ship
        if st == "REJECTED" and (has_ship or any(rl["approved_qty"] for rl in lines)):
            c.flag(rma, "rejected but has approved lines/shipment")
        if st not in ("REQUESTED", "REJECTED") and not has_ship:
            c.flag(rma, "authorized without shipment label")
        if st in ("AUTHORIZED", "IN_TRANSIT", "SHIPMENT_LOST", "EXPIRED") and any(
                rl["return_line_id"] in ins_by_line or refunds_by_line.get(rl["return_line_id"]) for rl in lines):
            c.flag(rma, f"{st} but has inspection/refund")
        if st == "COMPLETED" and any(rl["resolution"] == "PENDING" for rl in lines):
            c.flag(rma, "completed with pending lines")
        if st in ("SHIPMENT_LOST", "EXPIRED", "REJECTED") and any(rl["resolution"] not in ("NONE",) for rl in lines):
            c.flag(rma, "terminal failure with resolved lines")
    for rl in T["return_line"]:
        rr = rrs[rl["rma_id"]]
        if refunds_by_line.get(rl["return_line_id"]) and rr["request_type"] != "REFUND":
            c.flag(rl["return_line_id"], "refund on exchange request")
        i = ins_by_line.get(rl["return_line_id"])
        if i and i["received_qty"] > rl["approved_qty"]:
            c.flag(rl["return_line_id"], "received more than approved")

    # ---- disposition controls inventory
    c = suite.check("inventory_disposition.controls_inventory", "policy",
                    "disposition follows grade policy; only RESTOCK adds sellable stock; qty = received qty")
    for d in T["inventory_disposition"]:
        i = ins_by_line.get(d["return_line_id"])
        if i is None:
            c.flag(d["disposition_id"], "no inspection")
            continue
        ol = ols[rls[d["return_line_id"]]["order_line_id"]]
        exp = decide_disposition(policy, grade=i["grade"], category=ol["category"], received_qty=i["received_qty"])
        if (d["disposition"], d["to_location"], d["quantity"], d["sellable_qty_delta"]) != \
                (exp.disposition, exp.to_location, exp.quantity, exp.sellable_qty_delta):
            c.flag(d["disposition_id"], f"{d['disposition']} != {exp.disposition}")
        if DISPOSITION_LOCATION[d["disposition"]] != d["to_location"]:
            c.flag(d["disposition_id"], "location mapping")

    # ---- refund rows: internal arithmetic and duplicate-guard bookkeeping
    c = suite.check("refund.row_arithmetic", "arithmetic",
                    "amount = item + tax − fees for payouts; blocked attempts pay 0 and reference their cause")
    for r in T["refund"]:
        if r["status"] == "SUCCEEDED":
            net = r["item_amount_paise"] + r["tax_amount_paise"] - r["restocking_fee_paise"] - r["shipping_fee_paise"]
            if r["amount_paise"] != net or r["requested_amount_paise"] != net:
                c.flag(r["refund_id"], "amount != components")
            if r["processed_at"] is None:
                c.flag(r["refund_id"], "payout not processed")
        else:
            if r["amount_paise"] != 0:
                c.flag(r["refund_id"], "blocked attempt paid out")
        if r["status"] == "REJECTED_DUPLICATE":
            orig = next((x for x in refunds_by_line[r["return_line_id"]] if x["refund_id"] == r["duplicate_of_refund_id"]), None)
            if orig is None or orig["status"] != "SUCCEEDED":
                c.flag(r["refund_id"], "duplicate without a successful original on the same line")
        elif r["duplicate_of_refund_id"] is not None:
            c.flag(r["refund_id"], "duplicate_of set on non-duplicate")
        i = ins_by_line.get(r["return_line_id"])
        if r["status"] != "REJECTED_EXCEEDS_PAID" and i and r["refund_qty"] != i["received_qty"]:
            c.flag(r["refund_id"], "refund qty != received qty")

    # ===================================================================== anomaly detectors
    # 1. return qty exceeds purchased
    c = suite.check("detect.return_qty_exceeds_purchased", "detector",
                    "cumulative approved qty on an order line exceeds the purchased qty",
                    expects=("RETURN_QTY_EXCEEDS_PURCHASED",))
    approved_by_ol = defaultdict(list)
    for rl in sorted(T["return_line"], key=lambda x: (rrs[x["rma_id"]]["requested_at"], x["return_line_id"])):
        approved_by_ol[rl["order_line_id"]].append(rl)
    for ol_id, rlist in approved_by_ol.items():
        running = 0
        for rl in rlist:
            running += rl["approved_qty"]
            if rl["approved_qty"] and running > ols[ol_id]["quantity"]:
                c.flag(rl["return_line_id"], f"{running} > purchased {ols[ol_id]['quantity']}")

    # 2. refund exceeds paid amount
    c = suite.check("detect.refund_exceeds_paid", "detector",
                    "a payout is larger than what the customer paid for the refunded units",
                    expects=("REFUND_EXCEEDS_PAID",))
    for r in T["refund"]:
        if r["status"] != "SUCCEEDED":
            continue
        ol = ols[rls[r["return_line_id"]]["order_line_id"]]
        qty = min(r["refund_qty"], ol["quantity"])
        cap = proportional_slice(ol["line_paid_paise"], ol["quantity"], 0, qty)
        if r["amount_paise"] > cap + 1:     # +1 paisa tolerance for cumulative-floor rounding
            c.flag(r["refund_id"], f"{r['amount_paise']} > {cap}")

    # 3. restock / any disposition before inspection
    c = suite.check("detect.restock_before_inspection", "detector",
                    "inventory moved before the units were inspected",
                    expects=("RESTOCK_BEFORE_INSPECTION",))
    for d in T["inventory_disposition"]:
        i = ins_by_line.get(d["return_line_id"])
        if i is None or d["decided_at"] < i["inspected_at"]:
            c.flag(d["disposition_id"], "before inspection")

    # 4. late return approved without override
    c = suite.check("detect.late_return_without_override", "detector",
                    "units approved after the return window with no attributed override",
                    expects=("LATE_RETURN_WITHOUT_OVERRIDE",))
    for rl in T["return_line"]:
        rr = rrs[rl["rma_id"]]
        if rl["approved_qty"] > 0 and rl["days_since_delivery"] > rl["window_days"] and not rr["override_flag"]:
            c.flag(rl["return_line_id"], f"day {rl['days_since_delivery']} > {rl['window_days']}")

    # 5. duplicate payout
    c = suite.check("detect.duplicate_refund", "detector",
                    "more than one successful payout for the same return line",
                    expects=("DUPLICATE_REFUND",))
    for line_id, rs in refunds_by_line.items():
        ok = sorted((r for r in rs if r["status"] == "SUCCEEDED"), key=lambda r: (r["requested_at"], r["refund_id"]))
        for r in ok[1:]:
            c.flag(r["refund_id"], f"repeat of {ok[0]['refund_id']}")

    # ===================================================================== policy re-execution
    c = suite.check("policy.eligibility_reexecution", "policy",
                    "re-running the eligibility engine on source records reproduces every stored decision",
                    expects=("RETURN_QTY_EXCEEDS_PURCHASED", "LATE_RETURN_WITHOUT_OVERRIDE"))
    prior = defaultdict(int)
    reqs = sorted(T["return_request"], key=lambda r: (r["requested_at"], r["rma_id"]))
    for rr in reqs:
        o = orders[rr["order_id"]]
        decisions = []
        for rl in sorted(rl_by_rma[rr["rma_id"]], key=lambda x: x["line_no"]):
            ol = ols[rl["order_line_id"]]
            d = evaluate_line(policy, category=ol["category"], purchased_qty=ol["quantity"],
                              previously_approved_qty=prior[ol["order_line_id"]],
                              requested_qty=rl["requested_qty"], delivered_date=o["delivered_date"],
                              request_date=rr["requested_at"].date(), override=rr["override_flag"])
            prior[ol["order_line_id"]] += d.approved_qty
            decisions.append(d)
            if (d.decision, d.approved_qty, d.rejection_reason) != (rl["decision"], rl["approved_qty"], rl["rejection_reason"]):
                c.flag(rl["return_line_id"], f"stored {rl['decision']}/{rl['approved_qty']} vs rule {d.decision}/{d.approved_qty}")
        a = auth.get(rr["rma_id"])
        stored_agg = aggregate_decision([type(d)(rl["decision"], rl["approved_qty"], None, 0, 0)
                                         for rl, d in zip(sorted(rl_by_rma[rr["rma_id"]], key=lambda x: x["line_no"]), decisions)])
        if a and a["decision"] != stored_agg:
            c.flag(a["auth_id"], "authorization decision does not aggregate its lines")

    c = suite.check("policy.refund_reexecution", "policy",
                    "re-running the refund calculator reproduces every first successful payout",
                    expects=("REFUND_EXCEEDS_PAID",))
    ledger_qty, ledger_paid = defaultdict(int), defaultdict(int)
    fee_done: set[str] = set()
    firsts = []
    for line_id, rs in refunds_by_line.items():
        ok = sorted((r for r in rs if r["status"] == "SUCCEEDED"), key=lambda r: (r["requested_at"], r["refund_id"]))
        if ok:
            firsts.append(ok[0])
    for r in sorted(firsts, key=lambda r: (r["requested_at"], r["refund_id"])):
        rl = rls[r["return_line_id"]]
        ol = ols[rl["order_line_id"]]
        i = ins_by_line.get(r["return_line_id"])
        if i is None:
            c.flag(r["refund_id"], "no inspection")
            continue
        try:
            calc = compute_refund(policy, LineEconomics(ol["quantity"], ol["taxable_paise"], ol["tax_paise"], ol["line_paid_paise"]),
                                  category=ol["category"], reason_code=rl["reason_code"], grade=i["grade"],
                                  refund_qty=min(i["received_qty"], ol["quantity"] - ledger_qty[ol["order_line_id"]]),
                                  previously_refunded_qty=ledger_qty[ol["order_line_id"]],
                                  previously_refunded_paise=ledger_paid[ol["order_line_id"]],
                                  charge_shipping_fee=r["rma_id"] not in fee_done)
        except (RefundExceedsPaid, ValueError) as exc:
            c.flag(r["refund_id"], str(exc))
            continue
        if not policy.is_merchant_fault(rl["reason_code"]):
            fee_done.add(r["rma_id"])
        ledger_qty[ol["order_line_id"]] += calc.refund_qty
        ledger_paid[ol["order_line_id"]] += calc.amount_paise
        stored = (r["item_amount_paise"], r["tax_amount_paise"], r["grade_refund_bp"], r["restocking_fee_paise"],
                  r["shipping_fee_paise"], r["amount_paise"])
        exp = (calc.item_amount_paise, calc.tax_amount_paise, calc.grade_refund_bp, calc.restocking_fee_paise,
               calc.shipping_fee_paise, calc.amount_paise)
        if stored != exp:
            c.flag(r["refund_id"], f"stored {stored[-1]} vs rule {exp[-1]}")


# ======================================================================= entry point
def prune_unresolvable(T) -> dict[str, list[dict]]:
    """Drop rows whose PK or required fields are missing or whose FKs dangle (already reported by
    the integrity checks) so business-rule checks can rely on joins. Repeats until stable, because
    dropping a parent can orphan its children."""
    T = {k: list(v) for k, v in T.items()}
    while True:
        removed = 0
        pk_index = {t.name: {r[t.pk.name] for r in T[t.name]} for t in SCHEMA}
        for t in SCHEMA:
            keep = []
            for r in T[t.name]:
                ok = r.get(t.pk.name) is not None and all(
                    (r.get(c.name) is not None or not c.required) and
                    (not c.fk or r.get(c.name) is None or r[c.name] in pk_index[c.fk.split(".")[0]])
                    for c in t.cols)
                if ok:
                    keep.append(r)
            removed += len(T[t.name]) - len(keep)
            T[t.name] = keep
        if not removed:
            return T


def run_suite(T, policy: Policy, anomalies: list[dict]) -> Suite:
    suite = Suite()
    structural_checks(suite, T)
    consistency_checks(suite, prune_unresolvable(T), policy)
    gt_by_type = defaultdict(set)
    for a in anomalies:
        gt_by_type[a["issue_type"]].add(a["record_id"])
    for r in suite.results:
        for t in r.expects:
            r.expected_ids |= gt_by_type.get(t, set())
    return suite
