"""Clean baseline generation.

Parents are generated before children and every child references a real parent id.
Every outcome (eligibility, approved qty, refund amount, disposition, state) is
produced by the SAME domain rule engines the API uses — nothing is randomly
labelled. Randomness only drives *inputs* (what customers buy, ask for, when,
what condition goods arrive in); rules decide the *outputs*.
"""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from returns_platform import ids
from returns_platform.config import Policy
from returns_platform.domain.disposition import decide_disposition
from returns_platform.domain.eligibility import aggregate_decision, evaluate_line
from returns_platform.domain.lifecycle import assert_transition
from returns_platform.domain.pricing import LineInput, price_order
from returns_platform.domain.refund import LineEconomics, compute_refund, refund_method
from returns_platform.generator import vocab as V
from returns_platform.generator.settings import GenSettings, stream

CARRIER_PREFIX = {"DELHIVERY": "DLV", "BLUEDART": "BDT", "ECOM_EXPRESS": "ECX", "XPRESSBEES": "XPB", "INDIA_POST": "IPS"}


@dataclass
class Dataset:
    tables: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    expected: list[dict] = field(default_factory=list)       # per return line, from rules
    anomalies: list[dict] = field(default_factory=list)      # injected issues (ground truth)
    settings: GenSettings | None = None
    policy_version: str = ""


# ----------------------------------------------------------------- helpers
def _wchoice(rng, items_weights):
    items, weights = zip(*items_weights)
    return rng.choices(items, weights=weights, k=1)[0]


def _weighted_sample(rng, items, weights, k):
    """Weighted sampling without replacement (Efraimidis–Spirakis), deterministic."""
    keyed = [(rng.random() ** (1.0 / w), i) for i, w in enumerate(weights) if w > 0]
    keyed.sort(reverse=True)
    return [items[i] for _, i in keyed[:k]]


def _at(d: date, rng, start_h=8, end_h=23) -> datetime:
    return datetime.combine(d, time(rng.randint(start_h, end_h - 1), rng.randint(0, 59), rng.randint(0, 59)))


def _idem_key(seed: int, *parts) -> str:
    return "idem-" + hashlib.sha256(":".join(map(str, (seed,) + parts)).encode()).hexdigest()[:24]


class EventLog:
    """Collects lifecycle events; ids are assigned chronologically at the end."""

    def __init__(self):
        self._events: list[dict] = []
        self._state: dict[tuple[str, str], str | None] = {}

    def transition(self, machine, entity_id, rma_id, to_state, at, actor_type, actor_id):
        key = (machine, entity_id)
        current = self._state.get(key)
        assert_transition(machine, current, to_state)     # generator obeys the same state machine
        self._state[key] = to_state
        self._add(rma_id, machine, entity_id, current, to_state, at, actor_type, actor_id)

    def record(self, entity_type, entity_id, rma_id, to_state, at, actor_type, actor_id):
        self._add(rma_id, entity_type, entity_id, None, to_state, at, actor_type, actor_id)

    def _add(self, rma_id, entity_type, entity_id, from_state, to_state, at, actor_type, actor_id):
        self._events.append(dict(rma_id=rma_id, entity_type=entity_type, entity_id=entity_id,
                                 from_state=from_state, to_state=to_state, occurred_at=at,
                                 actor_type=actor_type, actor_id=actor_id, _seq=len(self._events)))

    def state(self, machine, entity_id):
        return self._state.get((machine, entity_id))

    def finalize(self) -> list[dict]:
        rows = sorted(self._events, key=lambda e: (e["occurred_at"], e["_seq"]))
        for n, e in enumerate(rows, 1):
            e.pop("_seq")
            e["event_id"] = ids.event_id(n)
        return rows


# ----------------------------------------------------------------- master data
def gen_customers(s: GenSettings) -> list[dict]:
    rng = stream(s.seed, "customers")
    out = []
    start = date(2022, 1, 1).toordinal()
    end = date(2025, 12, 31).toordinal()
    for n in range(1, s.n_customers + 1):
        first, last = rng.choice(V.FIRST_NAMES), rng.choice(V.LAST_NAMES)
        handle = f"{first}.{last}".lower().replace("'", "")
        out.append(dict(
            customer_id=ids.customer_id(n),
            full_name=f"{first} {last}",
            email=f"{handle}.{n:06d}@example.com",
            city=_wchoice(rng, V.CITIES),
            segment=_wchoice(rng, V.SEGMENTS),
            created_at=date.fromordinal(rng.randint(start, end)),
        ))
    return out


def gen_products(s: GenSettings) -> list[dict]:
    rng = stream(s.seed, "products")
    cats = list(V.CATEGORY_PROFILE)
    weights = [V.CATEGORY_PROFILE[c][0] for c in cats]
    out = []
    for n in range(1, s.n_products + 1):
        cat = cats[n - 1] if n <= len(cats) else rng.choices(cats, weights)[0]   # every category present
        _, _, _, (lo, hi), nouns = V.CATEGORY_PROFILE[cat]
        rupees = int(math.exp(rng.uniform(math.log(lo), math.log(hi))))
        if rupees >= 200:
            rupees = rupees // 100 * 100 + 99                                   # ₹x99 price points
        out.append(dict(
            product_id=ids.product_id(n),
            product_name=f"{rng.choice(V.BRANDS)} {rng.choice(nouns)}",
            category=cat,
            list_price_paise=max(lo, rupees) * 100,
            is_active=rng.random() > 0.05,
        ))
    return out


# ----------------------------------------------------------------- orders
def _day_weights(s: GenSettings) -> tuple[list[date], list[float]]:
    days, weights = [], []
    d = s.first_order_date
    while d <= s.last_order_date:
        w = 1.0
        if d.weekday() >= 5:
            w *= 1.25
        if (d.month, d.day) in {(1, 26), (8, 15)}:                 # national-holiday sales
            w *= 2.5
        if d.month == 10 and d.day <= 12:                           # festive season sale
            w *= 2.2
        days.append(d)
        weights.append(w)
        d += timedelta(days=1)
    return days, weights


def gen_orders(s: GenSettings, policy: Policy, customers: list[dict], products: list[dict]):
    rng = stream(s.seed, "orders")
    cust_ids = [c["customer_id"] for c in customers]
    cust_w = [rng.lognormvariate(0, 1.0) for _ in cust_ids]          # heavy-tailed purchase frequency
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        if p["is_active"]:
            by_cat[p["category"]].append(p)
    cats = sorted(by_cat)
    cat_w = [V.CATEGORY_PROFILE[c][1] for c in cats]
    pop_w = {p["product_id"]: rng.lognormvariate(0, 0.8) for p in products}

    days, day_w = _day_weights(s)
    order_days = rng.choices(days, weights=day_w, k=s.n_orders)
    hour_w = [1, 1, 1, 1, 1, 1, 2, 4, 6, 8, 9, 10, 11, 11, 10, 10, 10, 11, 13, 15, 16, 14, 9, 4]
    stamps = [datetime.combine(d, time(rng.choices(range(24), hour_w)[0], rng.randint(0, 59), rng.randint(0, 59)))
              for d in order_days]
    order_idx = sorted(range(s.n_orders), key=lambda i: (stamps[i], i))   # ids follow time

    orders, lines = [], []
    for n, i in enumerate(order_idx, 1):
        oid = ids.order_id(n)
        odate = stamps[i].date()
        channel = _wchoice(rng, (("WEB", 45), ("APP", 45), ("STORE", 10)))
        n_lines = _wchoice(rng, ((1, 45), (2, 25), (3, 15), (4, 8), (5, 4), (6, 3)))
        chosen, seen = [], set()
        while len(chosen) < n_lines:
            cat = rng.choices(cats, cat_w)[0]
            pool = by_cat[cat]
            p = rng.choices(pool, [pop_w[x["product_id"]] for x in pool])[0]
            if p["product_id"] not in seen:
                seen.add(p["product_id"])
                chosen.append(p)
        inputs = []
        for p in chosen:
            cat = p["category"]
            if cat == "GIFT_CARDS":
                qty = 1
            elif cat in ("GROCERY", "BOOKS", "BEAUTY"):
                qty = _wchoice(rng, ((1, 55), (2, 25), (3, 12), (4, 8)))
            else:
                qty = _wchoice(rng, ((1, 82), (2, 14), (3, 4)))
            gross = qty * p["list_price_paise"]
            disc = 0
            if cat != "GIFT_CARDS" and rng.random() < 0.28:
                disc = gross * rng.choice((5, 10, 15, 20, 25, 30)) // 100 // 100 * 100
            inputs.append(LineInput(qty, p["list_price_paise"], disc, policy.category(cat).tax_rate_bp))
        net = sum(li.quantity * li.unit_price_paise - li.line_discount_paise for li in inputs)
        coupon, coupon_val = None, 0
        if rng.random() < 0.18:
            coupon = rng.choice(("FLAT50", "FLAT100", "FLAT250", "SAVE10"))
            coupon_val = {"FLAT50": 5_000, "FLAT100": 10_000, "FLAT250": 25_000}.get(coupon, min(net // 10, 50_000))
            if coupon_val * 4 > net:                    # coupon terms: min basket 4x coupon value
                coupon, coupon_val = None, 0
        ship = 0 if channel == "STORE" or net - coupon_val >= 49_900 else 4_900
        priced = price_order(inputs, coupon_val, ship)
        delivered = odate if channel == "STORE" else odate + timedelta(days=_wchoice(rng, ((1, 10), (2, 25), (3, 30), (4, 18), (5, 10), (6, 4), (7, 3))))
        pay = _wchoice(rng, (("CARD", 25), ("UPI", 42), ("WALLET", 8), ("NETBANKING", 8))
                       + ((("COD", 0),) if channel == "STORE" else (("COD", 20),)))
        orders.append(dict(
            order_id=oid,
            customer_id=rng.choices(cust_ids, cust_w)[0],
            order_date=odate, delivered_date=delivered, channel=channel, payment_method=pay,
            status="DELIVERED", coupon_code=coupon,
            order_discount_paise=priced.order_discount_paise, subtotal_paise=priced.subtotal_paise,
            line_discount_paise=priced.line_discount_paise, tax_paise=priced.tax_paise,
            shipping_fee_paise=priced.shipping_fee_paise, total_paid_paise=priced.total_paid_paise,
        ))
        for ln_no, (p, lp) in enumerate(zip(chosen, priced.lines), 1):
            lines.append(dict(
                order_line_id=ids.order_line_id(oid, ln_no), order_id=oid, line_no=ln_no,
                product_id=p["product_id"], category=p["category"], quantity=lp.quantity,
                unit_price_paise=lp.unit_price_paise, line_discount_paise=lp.line_discount_paise,
                allocated_order_discount_paise=lp.allocated_order_discount_paise,
                taxable_paise=lp.taxable_paise, tax_rate_bp=lp.tax_rate_bp, tax_paise=lp.tax_paise,
                line_paid_paise=lp.line_paid_paise,
            ))
    return orders, lines


# ----------------------------------------------------------------- returns
def _plan_requests(s, policy, rng, orders, lines_by_order):
    """Pick which orders/lines are returned, hitting the exact request and line counts."""
    as_of_d = s.as_of.date()
    cand = [o for o in orders if o["delivered_date"] < as_of_d]
    w = []
    for o in cand:
        props = [V.CATEGORY_PROFILE[l["category"]][2] for l in lines_by_order[o["order_id"]]]
        w.append(max(props) * (1 + 0.15 * (len(props) - 1)))
    n_follow = round(s.n_requests * s.follow_up_request_share)
    primary = _weighted_sample(rng, cand, w, s.n_requests - n_follow)
    if len(primary) < s.n_requests - n_follow:
        raise ValueError("not enough delivered orders for the requested return volume")
    follow = rng.sample(primary, n_follow)
    slots = [dict(order=o, follow_up=False) for o in primary] + [dict(order=o, follow_up=True) for o in follow]

    # exact return-line total: 1 line each, then spread the remainder over spare capacity
    for sl in slots:
        sl["k"] = 1
    extra = s.n_return_lines - len(slots)
    tokens = [i for i, sl in enumerate(slots) for _ in range(len(lines_by_order[sl["order"]["order_id"]]) - 1)]
    if extra > len(tokens) or extra < 0:
        raise ValueError("return line volume incompatible with order line capacity")
    rng.shuffle(tokens)
    for i in tokens[:extra]:
        slots[i]["k"] += 1

    for sl in slots:
        ols = lines_by_order[sl["order"]["order_id"]]
        props = [V.CATEGORY_PROFILE[l["category"]][2] for l in ols]
        picked = _weighted_sample(rng, ols, props, sl["k"])
        sl["lines"] = sorted(picked, key=lambda l: l["line_no"])
    return slots


def _request_time(s, policy, rng, order, lines):
    """Days after delivery: most within window, a share deliberately late."""
    windows = [policy.category(l["category"]).window_days for l in lines]
    window = max(windows) or 7
    if rng.random() < s.rates["late_request"]:
        days = window + rng.randint(1, 30)
    else:
        days = int(rng.triangular(0, window, window * 0.25))
    max_days = (s.as_of.date() - order["delivered_date"]).days
    if days > max_days:
        days = rng.randint(0, max_days)
    d = order["delivered_date"] + timedelta(days=days)
    at = _at(d, rng)
    return min(at, s.as_of)


def gen_returns(s: GenSettings, policy: Policy, ds: Dataset, customers, products, orders, order_lines):
    rng = stream(s.seed, "returns")
    lines_by_order = defaultdict(list)
    for l in order_lines:
        lines_by_order[l["order_id"]].append(l)
    order_by_id = {o["order_id"]: o for o in orders}
    seg_by_cust = {c["customer_id"]: c["segment"] for c in customers}
    prod_by_cat = defaultdict(list)
    for p in products:
        if p["is_active"]:
            prod_by_cat[p["category"]].append(p["product_id"])

    slots = _plan_requests(s, policy, rng, orders, lines_by_order)
    primary_time = {}
    for sl in slots:
        if not sl["follow_up"]:
            sl["at"] = _request_time(s, policy, rng, sl["order"], sl["lines"])
            primary_time[sl["order"]["order_id"]] = sl["at"]
    for sl in slots:
        if sl["follow_up"]:
            base = primary_time[sl["order"]["order_id"]]
            sl["at"] = min(base + timedelta(days=rng.randint(2, 20), minutes=rng.randint(0, 600)), s.as_of)
    order_of_slots = sorted(range(len(slots)), key=lambda i: (slots[i]["at"], slots[i]["follow_up"], i))

    ev = EventLog()
    seq = {k: ids.Sequence(f) for k, f in (
        ("rma", ids.rma_id), ("auth", ids.auth_id), ("rts", ids.shipment_id), ("ins", ids.inspection_id),
        ("ref", ids.refund_id), ("dsp", ids.disposition_id))}
    approved_so_far: dict[str, int] = defaultdict(int)      # per order line, across RMAs
    refunded_qty: dict[str, int] = defaultdict(int)
    refunded_paise: dict[str, int] = defaultdict(int)
    T = ds.tables
    R = s.rates
    as_of = s.as_of

    for idx in order_of_slots:
        sl = slots[idx]
        order, olines, req_at = sl["order"], sl["lines"], sl["at"]
        rma = seq["rma"].next()
        cust = order["customer_id"]
        soft = any(l["category"] in ("APPAREL", "FOOTWEAR") for l in olines)
        req_type = "EXCHANGE" if rng.random() < (0.30 if soft else 0.05) else "REFUND"
        req_date = req_at.date()

        # --- inputs chosen by the "customer"
        line_inputs = []
        for l in olines:
            cat = l["category"]
            if req_type == "EXCHANGE":
                rw = {"SIZE_FIT": 60, "DEFECTIVE": 15, "WRONG_ITEM": 15, "ARRIVED_DAMAGED": 10} if cat in ("APPAREL", "FOOTWEAR") \
                    else {"DEFECTIVE": 45, "WRONG_ITEM": 30, "ARRIVED_DAMAGED": 25}
            else:
                rw = V.REASON_WEIGHTS.get(cat, V.REASON_WEIGHTS["default"])
            reason = rng.choices(list(rw), list(rw.values()))[0]
            q = l["quantity"]
            r = rng.random()
            if sl["follow_up"]:
                req_qty = rng.randint(1, q)
            elif r < R["over_request"]:
                req_qty = q + rng.randint(1, 2)
            elif q > 1 and r < R["over_request"] + R["partial_qty"]:
                req_qty = rng.randint(1, q - 1)
            else:
                req_qty = q
            line_inputs.append((l, reason, req_qty))

        late = any((req_date - order["delivered_date"]).days > policy.category(l["category"]).window_days
                   and policy.category(l["category"]).returnable for l in olines)
        ovr_p = R["override_given_late"] * (2 if seg_by_cust[cust] in ("LOYAL", "VIP") else 1)
        override = late and rng.random() < ovr_p

        # --- rules decide
        decisions = []
        for l, reason, req_qty in line_inputs:
            d = evaluate_line(policy, category=l["category"], purchased_qty=l["quantity"],
                              previously_approved_qty=approved_so_far[l["order_line_id"]],
                              requested_qty=req_qty, delivered_date=order["delivered_date"],
                              request_date=req_date, override=override)
            approved_so_far[l["order_line_id"]] += d.approved_qty
            decisions.append(d)
        auth_decision = aggregate_decision(decisions)
        if override:
            decided_by = f"OPR-{rng.randint(1, 40):03d}"
            decided_at = req_at + timedelta(hours=rng.uniform(2, 30))
        else:
            decided_by = "SYSTEM"
            decided_at = req_at + timedelta(minutes=rng.uniform(1, 20))
        decided_at = min(decided_at, as_of).replace(microsecond=0)

        ev.transition("RETURN_REQUEST", rma, rma, "REQUESTED", req_at, "CUSTOMER", cust)
        rma_state = "AUTHORIZED" if auth_decision != "REJECTED" else "REJECTED"
        ev.transition("RETURN_REQUEST", rma, rma, rma_state, decided_at,
                      "SYSTEM" if decided_by == "SYSTEM" else "OPERATOR", decided_by)

        rlines = []
        for n, ((l, reason, req_qty), d) in enumerate(zip(line_inputs, decisions), 1):
            rlines.append(dict(
                return_line_id=ids.return_line_id(rma, n), rma_id=rma, line_no=n,
                order_line_id=l["order_line_id"], reason_code=reason, requested_qty=req_qty,
                approved_qty=d.approved_qty, decision=d.decision, rejection_reason=d.rejection_reason,
                days_since_delivery=d.days_since_delivery, window_days=d.window_days,
                resolution="NONE" if d.approved_qty == 0 else "PENDING", exchange_product_id=None,
                _ol=l,
            ))
        ship_by = decided_at.date() + timedelta(days=policy.ship_by_days) if auth_decision != "REJECTED" else None
        T["return_authorization"].append(dict(
            auth_id=seq["auth"].next(), rma_id=rma, decided_at=decided_at, decision=auth_decision,
            approved_line_count=sum(1 for d in decisions if d.approved_qty > 0),
            approved_qty_total=sum(d.approved_qty for d in decisions), ship_by_date=ship_by,
            decided_by=decided_by, policy_version=policy.version,
        ))

        if auth_decision != "REJECTED":
            _simulate_fulfilment(s, policy, rng, ev, seq, T, rma, order, rlines, req_type, decided_at, ship_by,
                                 prod_by_cat, refunded_qty, refunded_paise)

        status = ev.state("RETURN_REQUEST", rma)
        T["return_request"].append(dict(
            rma_id=rma, order_id=order["order_id"], customer_id=cust, requested_at=req_at,
            request_type=req_type, channel=_wchoice(rng, (("WEB", 40), ("APP", 50), ("STORE", 10))),
            policy_version=policy.version, override_flag=override,
            override_reason=rng.choice(V.OVERRIDE_REASONS) if override else None,
            override_by=decided_by if override else None, status=status,
        ))
        for rl in rlines:
            rl.pop("_ol")
            T["return_line"].append(rl)

    T["return_event"] = ev.finalize()
    T["return_request"].sort(key=lambda r: r["rma_id"])


def _simulate_fulfilment(s, policy, rng, ev, seq, T, rma, order, rlines, req_type, decided_at, ship_by,
                         prod_by_cat, refunded_qty, refunded_paise):
    R, as_of = s.rates, s.as_of
    rts = seq["rts"].next()
    carrier = _wchoice(rng, (("DELHIVERY", 35), ("BLUEDART", 20), ("ECOM_EXPRESS", 18), ("XPRESSBEES", 17), ("INDIA_POST", 10)))
    label_at = min(decided_at + timedelta(minutes=rng.randint(1, 10)), as_of)
    shipment = dict(rts_id=rts, rma_id=rma, carrier=carrier,
                    tracking_number=f"{CARRIER_PREFIX[carrier]}{ids.parse_counter(rts):010d}",
                    label_created_at=label_at, shipped_at=None, received_at=None, status="LABEL_CREATED")
    T["return_shipment"].append(shipment)
    ev.transition("RETURN_SHIPMENT", rts, rma, "LABEL_CREATED", label_at, "SYSTEM", "SYSTEM")

    deadline = datetime.combine(ship_by, time(23, 59, 59))
    if rng.random() < R["never_shipped"]:
        if deadline <= as_of:
            shipment["status"] = "EXPIRED"
            ev.transition("RETURN_SHIPMENT", rts, rma, "EXPIRED", deadline, "SYSTEM", "SYSTEM")
            ev.transition("RETURN_REQUEST", rma, rma, "EXPIRED", deadline, "SYSTEM", "SYSTEM")
            for rl in rlines:
                if rl["approved_qty"]:
                    rl["resolution"] = "NONE"
        return
    gap_days = _wchoice(rng, ((0, 20), (1, 25), (2, 18), (3, 12), (4, 8), (5, 6), (7, 5), (10, 4), (13, 2)))
    shipped_at = min(label_at + timedelta(days=gap_days, hours=rng.uniform(2, 20)), deadline).replace(microsecond=0)
    if shipped_at > as_of:
        return
    shipment.update(shipped_at=shipped_at, status="IN_TRANSIT")
    ev.transition("RETURN_SHIPMENT", rts, rma, "IN_TRANSIT", shipped_at, "CARRIER", carrier)
    ev.transition("RETURN_REQUEST", rma, rma, "IN_TRANSIT", shipped_at, "CARRIER", carrier)

    if rng.random() < R["lost_in_transit"]:
        lost_at = shipped_at + timedelta(days=15)
        if lost_at <= as_of:
            shipment["status"] = "LOST"
            ev.transition("RETURN_SHIPMENT", rts, rma, "LOST", lost_at, "CARRIER", carrier)
            ev.transition("RETURN_REQUEST", rma, rma, "SHIPMENT_LOST", lost_at, "SYSTEM", "SYSTEM")
            for rl in rlines:
                if rl["approved_qty"]:
                    rl["resolution"] = "NONE"
        return
    received_at = (shipped_at + timedelta(days=_wchoice(rng, ((1, 10), (2, 25), (3, 28), (4, 17), (5, 10), (6, 5), (8, 5))),
                                          hours=rng.uniform(0, 10))).replace(microsecond=0)
    if received_at > as_of:
        return
    shipment.update(received_at=received_at, status="RECEIVED")
    ev.transition("RETURN_SHIPMENT", rts, rma, "RECEIVED", received_at, "OPERATOR", "WH-BLR-01")
    ev.transition("RETURN_REQUEST", rma, rma, "RECEIVED", received_at, "OPERATOR", "WH-BLR-01")

    payment = order["payment_method"]
    inspected_all, done_times, fee_charged = True, [], False
    approved = [rl for rl in rlines if rl["approved_qty"] > 0]
    inspections = []
    for rl in approved:
        ins_at = (received_at + timedelta(hours=_wchoice(rng, ((2, 15), (8, 25), (20, 25), (36, 18), (60, 10), (100, 7))) * rng.uniform(0.6, 1.0))).replace(microsecond=0)
        if ins_at > as_of:
            inspected_all = False
            continue
        qty = rl["approved_qty"]
        recv = qty
        if rng.random() < R["short_received"]:
            recv = 0 if rng.random() < 0.25 else max(0, qty - 1)
        grade = _grade(rng, rl["reason_code"])
        ins = dict(inspection_id=seq["ins"].next(), return_line_id=rl["return_line_id"], rts_id=rts,
                   inspected_at=ins_at, received_qty=recv, grade=grade,
                   damage_noted=grade == "D_DAMAGED" or (grade == "E_DEFECTIVE" and rng.random() < 0.3),
                   inspector_id=f"INSP-{rng.randint(1, 25):03d}",
                   notes="Parcel empty or short" if recv < qty else rng.choice(V.INSPECTION_NOTES[grade]))
        T["inspection"].append(ins)
        inspections.append((rl, ins))

    if inspected_all and inspections:
        ev.transition("RETURN_REQUEST", rma, rma, "INSPECTED", max(i["inspected_at"] for _, i in inspections), "OPERATOR", "WH-BLR-01")

    # refunds in chronological order so the once-per-RMA shipping fee lands on the first one
    plans = []
    for rl, ins in inspections:
        ol = rl["_ol"]
        if ins["received_qty"] == 0:
            rl["resolution"] = "NONE"
            done_times.append(ins["inspected_at"])
            continue
        disp_at = (ins["inspected_at"] + timedelta(minutes=rng.randint(30, 48 * 60))).replace(microsecond=0)
        ref_at = (ins["inspected_at"] + timedelta(minutes=rng.randint(60, 96 * 60))).replace(microsecond=0)
        plans.append((ref_at, rl, ins, disp_at))
    plans.sort(key=lambda p: (p[0], p[1]["line_no"]))

    for ref_at, rl, ins, disp_at in plans:
        ol = rl["_ol"]
        line_done = True
        # disposition — only ever from an inspection
        if disp_at <= as_of:
            dd = decide_disposition(policy, grade=ins["grade"], category=ol["category"], received_qty=ins["received_qty"])
            dsp = seq["dsp"].next()
            T["inventory_disposition"].append(dict(
                disposition_id=dsp, return_line_id=rl["return_line_id"], inspection_id=ins["inspection_id"],
                product_id=ol["product_id"], decided_at=disp_at, disposition=dd.disposition,
                to_location=dd.to_location, quantity=dd.quantity, sellable_qty_delta=dd.sellable_qty_delta))
            ev.record("INVENTORY_DISPOSITION", dsp, rma, dd.disposition, disp_at, "OPERATOR", ins["inspector_id"])
        else:
            line_done = False

        if req_type == "EXCHANGE":
            if line_done:
                rl["resolution"] = "EXCHANGE"
                same = rng.random() < 0.7 or not prod_by_cat[ol["category"]]
                rl["exchange_product_id"] = ol["product_id"] if same else rng.choice(prod_by_cat[ol["category"]])
                done_times.append(disp_at)
            continue

        if ref_at > as_of:
            line_done = False
        else:
            econ = LineEconomics(ol["quantity"], ol["taxable_paise"], ol["tax_paise"], ol["line_paid_paise"])
            calc = compute_refund(policy, econ, category=ol["category"], reason_code=rl["reason_code"],
                                  grade=ins["grade"], refund_qty=ins["received_qty"],
                                  previously_refunded_qty=refunded_qty[ol["order_line_id"]],
                                  previously_refunded_paise=refunded_paise[ol["order_line_id"]],
                                  charge_shipping_fee=not fee_charged)
            if not policy.is_merchant_fault(rl["reason_code"]):
                fee_charged = True          # fee applies to the first customer-fault refund only
            refunded_qty[ol["order_line_id"]] += calc.refund_qty
            refunded_paise[ol["order_line_id"]] += calc.amount_paise
            first = _refund_row(s, seq, rma, rl, 1, ref_at, calc, refund_method(payment), "SUCCEEDED",
                                calc.amount_paise, None)
            first["processed_at"] = ref_at + timedelta(minutes=rng.randint(1, 60))
            T["refund"].append(first)
            ev.record("REFUND", first["refund_id"], rma, "SUCCEEDED", ref_at, "SYSTEM", "PAYMENTS")
            attempt = 1
            if rng.random() < R["duplicate_refund_attempt"]:            # client retry, new key -> blocked
                at = ref_at + timedelta(minutes=rng.randint(1, 48 * 60))
                if at <= as_of:
                    attempt += 1
                    dup = _refund_row(s, seq, rma, rl, attempt, at, calc, refund_method(payment),
                                      "REJECTED_DUPLICATE", calc.amount_paise, first["refund_id"])
                    T["refund"].append(dup)
                    ev.record("REFUND", dup["refund_id"], rma, "REJECTED_DUPLICATE", at, "SYSTEM", "PAYMENTS")
            if rng.random() < R["goodwill_overpay_attempt"]:            # operator asks for more than owed -> blocked
                at = ref_at + timedelta(days=rng.randint(1, 5))
                if at <= as_of:
                    attempt += 1
                    ask = ol["line_paid_paise"] - refunded_paise[ol["order_line_id"]] + rng.randint(10, 500) * 100
                    gw = _refund_row(s, seq, rma, rl, attempt, at, None, refund_method(payment),
                                     "REJECTED_EXCEEDS_PAID", ask, None)
                    T["refund"].append(gw)
                    ev.record("REFUND", gw["refund_id"], rma, "REJECTED_EXCEEDS_PAID", at, "OPERATOR", f"OPR-{rng.randint(1, 40):03d}")
            if line_done:
                rl["resolution"] = "REFUND"
                done_times.append(max(first["processed_at"], disp_at))
        if not line_done:
            continue

    all_resolved = inspected_all and all(rl["resolution"] != "PENDING" for rl in approved)
    if all_resolved and done_times:
        done_at = max(done_times) + timedelta(minutes=5)
        if done_at <= as_of:
            ev.transition("RETURN_REQUEST", rma, rma, "COMPLETED", done_at, "SYSTEM", "SYSTEM")


def _refund_row(s, seq, rma, rl, attempt, at, calc, method, status, requested_amount, dup_of):
    rid = seq["ref"].next()
    return dict(
        refund_id=rid, return_line_id=rl["return_line_id"], rma_id=rma, attempt_no=attempt,
        idempotency_key=_idem_key(s.seed, rl["return_line_id"], attempt), requested_at=at,
        processed_at=at if status != "SUCCEEDED" else None,
        refund_qty=calc.refund_qty if calc else 0,
        item_amount_paise=calc.item_amount_paise if calc else 0,
        tax_amount_paise=calc.tax_amount_paise if calc else 0,
        grade_refund_bp=calc.grade_refund_bp if calc else 0,
        restocking_fee_paise=calc.restocking_fee_paise if calc else 0,
        shipping_fee_paise=calc.shipping_fee_paise if calc else 0,
        requested_amount_paise=requested_amount,
        amount_paise=calc.amount_paise if (calc and status == "SUCCEEDED") else 0,
        method=method, status=status, duplicate_of_refund_id=dup_of,
    )


def _grade(rng, reason):
    if reason == "DEFECTIVE":
        return _wchoice(rng, (("E_DEFECTIVE", 80), ("D_DAMAGED", 10), ("B_LIKE_NEW", 10)))
    if reason == "ARRIVED_DAMAGED":
        return _wchoice(rng, (("D_DAMAGED", 85), ("E_DEFECTIVE", 10), ("B_LIKE_NEW", 5)))
    if reason in ("WRONG_ITEM", "NOT_AS_DESCRIBED"):
        return _wchoice(rng, (("A_NEW", 55), ("B_LIKE_NEW", 33), ("C_USED", 12)))
    return _wchoice(rng, (("A_NEW", 42), ("B_LIKE_NEW", 36), ("C_USED", 16), ("D_DAMAGED", 6)))


# ----------------------------------------------------------------- expected labels
def expected_outcomes(ds: Dataset) -> list[dict]:
    """Rule-derived expected outcome per return line, captured BEFORE anomaly injection."""
    paid = defaultdict(int)
    for r in ds.tables["refund"]:
        if r["status"] == "SUCCEEDED":
            paid[r["return_line_id"]] += r["amount_paise"]
    disp = {d["return_line_id"]: d["disposition"] for d in ds.tables["inventory_disposition"]}
    return [dict(
        return_line_id=rl["return_line_id"], rma_id=rl["rma_id"],
        expected_decision=rl["decision"], expected_approved_qty=rl["approved_qty"],
        expected_rejection_reason=rl["rejection_reason"], expected_resolution=rl["resolution"],
        expected_refund_paise=paid.get(rl["return_line_id"]),
        expected_disposition=disp.get(rl["return_line_id"]),
    ) for rl in ds.tables["return_line"]]
