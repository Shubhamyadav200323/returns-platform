"""Application services: the returns workflow as transactional use-cases.

Every write: (1) locks the aggregate it changes, (2) asks the domain engines for
the decision, (3) validates the state transition, (4) appends an event, all in
ONE database transaction. Nothing here re-implements a business rule.
"""
from __future__ import annotations

import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.engine import Connection, Engine

from returns_platform import db, ids
from returns_platform.analytics.metrics import compute_metrics
from returns_platform.config import Policy
from returns_platform.domain import enums as E
from returns_platform.domain.disposition import decide_disposition
from returns_platform.domain.eligibility import aggregate_decision, evaluate_line
from returns_platform.domain.lifecycle import IllegalTransition, assert_transition
from returns_platform.domain.refund import (
    LineEconomics, RefundExceedsPaid, compute_refund, refund_guard, refund_method,
)


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, detail: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.detail = code, message, status, detail or {}


def now() -> datetime:
    """Business clock (UTC, second precision). RETURNS_CLOCK pins it for tests/demos."""
    pinned = os.environ.get("RETURNS_CLOCK")
    if pinned:
        return datetime.fromisoformat(pinned).replace(tzinfo=None, microsecond=0)
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class ReturnsService:
    def __init__(self, engine: Engine, policy: Policy):
        self.engine = engine
        self.policy = policy
        self._metrics_cache: tuple[int, dict] | None = None
        self._version = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers
    def _bump(self):
        with self._lock:
            self._version += 1

    def _event(self, conn, rma_id, entity_type, entity_id, from_state, to_state, actor_type, actor_id, at):
        db.insert(conn, "return_event", dict(
            event_id=ids.event_id(db.next_counter(conn, "return_event")), rma_id=rma_id, entity_type=entity_type,
            entity_id=entity_id, from_state=from_state, to_state=to_state, occurred_at=at,
            actor_type=actor_type, actor_id=actor_id))

    def _move_rma(self, conn, rma: dict, to_state: str, actor_type: str, actor_id: str, at: datetime):
        try:
            assert_transition("RETURN_REQUEST", rma["status"], to_state)
        except IllegalTransition as exc:
            raise DomainError("ILLEGAL_TRANSITION", str(exc), 409) from exc
        db.update(conn, "return_request", rma["rma_id"], status=to_state)
        self._event(conn, rma["rma_id"], "RETURN_REQUEST", rma["rma_id"], rma["status"], to_state, actor_type, actor_id, at)
        rma["status"] = to_state

    def _move_shipment(self, conn, s: dict, to_state: str, actor_type: str, actor_id: str, at: datetime, **fields):
        try:
            assert_transition("RETURN_SHIPMENT", s["status"], to_state)
        except IllegalTransition as exc:
            raise DomainError("ILLEGAL_TRANSITION", str(exc), 409) from exc
        db.update(conn, "return_shipment", s["rts_id"], status=to_state, **fields)
        self._event(conn, s["rma_id"], "RETURN_SHIPMENT", s["rts_id"], s["status"], to_state, actor_type, actor_id, at)
        s.update(status=to_state, **fields)

    def _must(self, conn, table, pk, for_update=False):
        row = db.get(conn, table, pk, for_update=for_update)
        if row is None:
            raise DomainError("NOT_FOUND", f"{table} {pk} not found", 404)
        return row

    def _approved_so_far(self, conn, order_line_id: str) -> int:
        rows = db.select(conn, "return_line", "order_line_id = :o", {"o": order_line_id})
        return sum(r["approved_qty"] for r in rows)

    # ------------------------------------------------------------------ reads
    def get_order(self, order_id: str) -> dict:
        with self.engine.connect() as conn:
            o = self._must(conn, "sales_order", order_id)
            today = now().date()
            lines = []
            for l in db.select(conn, "order_line", "order_id = :o", {"o": order_id}, order_by="line_no"):
                rule = self.policy.category(l["category"])
                prod = db.get(conn, "product", l["product_id"])
                approved = self._approved_so_far(conn, l["order_line_id"])
                days = (today - o["delivered_date"]).days
                lines.append(l | dict(
                    product_name=prod["product_name"], already_approved_qty=approved,
                    returnable_qty=max(0, l["quantity"] - approved), window_days=rule.window_days,
                    returnable_category=rule.returnable, days_since_delivery=days,
                    within_window=rule.returnable and days <= rule.window_days))
            cust = db.get(conn, "customer", o["customer_id"])
            rmas = db.select(conn, "return_request", "order_id = :o", {"o": order_id})
            return o | {"customer": cust, "lines": lines, "returns": [r["rma_id"] for r in rmas]}

    def get_return(self, rma_id: str) -> dict:
        with self.engine.connect() as conn:
            rr = self._must(conn, "return_request", rma_id)
            p = {"r": rma_id}
            lines = db.select(conn, "return_line", "rma_id = :r", p, order_by="line_no")
            ins = {i["return_line_id"]: i for i in db.select(conn, "inspection", "rts_id IN (SELECT rts_id FROM return_shipment WHERE rma_id = :r)", p)}
            disp = {d["return_line_id"]: d for d in db.select(conn, "inventory_disposition",
                    "return_line_id IN (SELECT return_line_id FROM return_line WHERE rma_id = :r)", p)}
            refunds = db.select(conn, "refund", "rma_id = :r", p, order_by="refund_id")
            for l in lines:
                ol = db.get(conn, "order_line", l["order_line_id"])
                l["order_line"] = ol
                l["product_name"] = db.get(conn, "product", ol["product_id"])["product_name"]
                l["inspection"] = ins.get(l["return_line_id"])
                l["disposition"] = disp.get(l["return_line_id"])
                l["refunds"] = [r for r in refunds if r["return_line_id"] == l["return_line_id"]]
            auth = db.select(conn, "return_authorization", "rma_id = :r", p)
            ship = db.select(conn, "return_shipment", "rma_id = :r", p)
            events = db.select(conn, "return_event", "rma_id = :r", p, order_by="occurred_at, event_id")
            return rr | dict(order=db.get(conn, "sales_order", rr["order_id"]), lines=lines,
                             authorization=auth[0] if auth else None, shipment=ship[0] if ship else None,
                             events=events, totals=dict(
                                 refunded_paise=sum(r["amount_paise"] for r in refunds if r["status"] == "SUCCEEDED"),
                                 blocked_attempts=sum(1 for r in refunds if r["status"] != "SUCCEEDED")))

    def list_returns(self, status: str | None = None, customer_id: str | None = None,
                     limit: int = 50, offset: int = 0) -> dict:
        where, params = [], {}
        if status:
            where.append("status = :s")
            params["s"] = status
        if customer_id:
            where.append("customer_id = :c")
            params["c"] = customer_id
        with self.engine.connect() as conn:
            rows = db.select(conn, "return_request", " AND ".join(where), params,
                             order_by="requested_at DESC, rma_id DESC", limit=min(limit, 200), offset=offset)
            return {"items": rows, "limit": limit, "offset": offset}

    # ------------------------------------------------------------------ 1. request + authorization
    def create_return(self, *, order_id: str, request_type: str, lines: list[dict], channel: str = "WEB",
                      override: dict | None = None, actor_id: str | None = None) -> dict:
        if request_type not in E.REQUEST_TYPES:
            raise DomainError("BAD_REQUEST_TYPE", f"request_type must be one of {E.REQUEST_TYPES}")
        if channel not in E.CHANNELS:
            raise DomainError("BAD_CHANNEL", f"channel must be one of {E.CHANNELS}")
        if not lines:
            raise DomainError("NO_LINES", "a return needs at least one line")
        if len({l["order_line_id"] for l in lines}) != len(lines):
            raise DomainError("DUPLICATE_LINE", "each order line may appear once per return")
        if override and not (override.get("reason") and override.get("by")):
            raise DomainError("OVERRIDE_UNATTRIBUTED", "an override needs a reason and the approving operator")
        at = now()
        with self.engine.begin() as conn:
            order = self._must(conn, "sales_order", order_id, for_update=True)   # serialises returns per order
            if at.date() < order["delivered_date"]:
                raise DomainError("NOT_DELIVERED", "order has not been delivered yet")
            decisions, prepared = [], []
            for i, req in enumerate(lines, 1):
                ol = db.get(conn, "order_line", req["order_line_id"])
                if ol is None or ol["order_id"] != order_id:
                    raise DomainError("LINE_NOT_ON_ORDER", f"{req['order_line_id']} is not a line of {order_id}")
                if req.get("reason_code") not in E.REASON_CODES:
                    raise DomainError("BAD_REASON", f"reason_code must be one of {E.REASON_CODES}")
                qty = int(req.get("quantity", 0))
                if qty < 1:
                    raise DomainError("BAD_QTY", "quantity must be at least 1")
                d = evaluate_line(self.policy, category=ol["category"], purchased_qty=ol["quantity"],
                                  previously_approved_qty=self._approved_so_far(conn, ol["order_line_id"]),
                                  requested_qty=qty, delivered_date=order["delivered_date"],
                                  request_date=at.date(), override=bool(override))
                decisions.append(d)
                prepared.append((i, ol, req, qty, d))
            rma_id = ids.rma_id(db.next_counter(conn, "return_request"))
            rma = dict(rma_id=rma_id, order_id=order_id, customer_id=order["customer_id"], requested_at=at,
                       request_type=request_type, channel=channel, policy_version=self.policy.version,
                       override_flag=bool(override), override_reason=(override or {}).get("reason"),
                       override_by=(override or {}).get("by"), status="REQUESTED")
            db.insert(conn, "return_request", rma)
            self._event(conn, rma_id, "RETURN_REQUEST", rma_id, None, "REQUESTED", "CUSTOMER", order["customer_id"], at)
            for i, ol, req, qty, d in prepared:
                db.insert(conn, "return_line", dict(
                    return_line_id=ids.return_line_id(rma_id, i), rma_id=rma_id, line_no=i,
                    order_line_id=ol["order_line_id"], reason_code=req["reason_code"], requested_qty=qty,
                    approved_qty=d.approved_qty, decision=d.decision, rejection_reason=d.rejection_reason,
                    days_since_delivery=d.days_since_delivery, window_days=d.window_days,
                    resolution="NONE" if d.approved_qty == 0 else "PENDING", exchange_product_id=None))
            decision = aggregate_decision(decisions)
            decided_by = override["by"] if override else "SYSTEM"
            ship_by = at.date() + timedelta(days=self.policy.ship_by_days) if decision != "REJECTED" else None
            db.insert(conn, "return_authorization", dict(
                auth_id=ids.auth_id(db.next_counter(conn, "return_authorization")), rma_id=rma_id, decided_at=at,
                decision=decision, approved_line_count=sum(1 for d in decisions if d.approved_qty),
                approved_qty_total=sum(d.approved_qty for d in decisions), ship_by_date=ship_by,
                decided_by=decided_by, policy_version=self.policy.version))
            actor_type = "OPERATOR" if override else "SYSTEM"
            self._move_rma(conn, rma, "AUTHORIZED" if decision != "REJECTED" else "REJECTED", actor_type, decided_by, at)
            if decision != "REJECTED":
                rts = ids.shipment_id(db.next_counter(conn, "return_shipment"))
                carrier = E.CARRIERS[ids.parse_counter(rts) % len(E.CARRIERS)]
                s = dict(rts_id=rts, rma_id=rma_id, carrier=carrier,
                         tracking_number=f"{carrier[:3]}{ids.parse_counter(rts):010d}",
                         label_created_at=at, shipped_at=None, received_at=None, status=None)
                db.insert(conn, "return_shipment", s | {"status": "LABEL_CREATED"})
                self._move_shipment(conn, s, "LABEL_CREATED", "SYSTEM", "SYSTEM", at)
        self._bump()
        return self.get_return(rma_id)

    # ------------------------------------------------------------------ 2. shipment
    def ship(self, rts_id: str, carrier_ref: str | None = None) -> dict:
        at = now()
        with self.engine.begin() as conn:
            s = self._must(conn, "return_shipment", rts_id, for_update=True)
            rma = self._must(conn, "return_request", s["rma_id"], for_update=True)
            auth = db.select(conn, "return_authorization", "rma_id = :r", {"r": rma["rma_id"]})[0]
            if auth["ship_by_date"] and at.date() > auth["ship_by_date"]:
                self._move_shipment(conn, s, "EXPIRED", "SYSTEM", "SYSTEM", at)
                self._move_rma(conn, rma, "EXPIRED", "SYSTEM", "SYSTEM", at)
                self._resolve_all(conn, rma["rma_id"], "NONE")
            else:
                self._move_shipment(conn, s, "IN_TRANSIT", "CARRIER", carrier_ref or s["carrier"], at, shipped_at=at)
                self._move_rma(conn, rma, "IN_TRANSIT", "CARRIER", carrier_ref or s["carrier"], at)
        self._bump()
        return self.get_return(s["rma_id"])

    def receive(self, rts_id: str, operator: str) -> dict:
        at = now()
        with self.engine.begin() as conn:
            s = self._must(conn, "return_shipment", rts_id, for_update=True)
            rma = self._must(conn, "return_request", s["rma_id"], for_update=True)
            self._move_shipment(conn, s, "RECEIVED", "OPERATOR", operator, at, received_at=at)
            self._move_rma(conn, rma, "RECEIVED", "OPERATOR", operator, at)
        self._bump()
        return self.get_return(s["rma_id"])

    def mark_lost(self, rts_id: str, operator: str) -> dict:
        at = now()
        with self.engine.begin() as conn:
            s = self._must(conn, "return_shipment", rts_id, for_update=True)
            rma = self._must(conn, "return_request", s["rma_id"], for_update=True)
            self._move_shipment(conn, s, "LOST", "OPERATOR", operator, at)
            self._move_rma(conn, rma, "SHIPMENT_LOST", "OPERATOR", operator, at)
            self._resolve_all(conn, rma["rma_id"], "NONE")
        self._bump()
        return self.get_return(s["rma_id"])

    def _resolve_all(self, conn, rma_id, resolution):
        for rl in db.select(conn, "return_line", "rma_id = :r AND resolution = 'PENDING'", {"r": rma_id}):
            db.update(conn, "return_line", rl["return_line_id"], resolution=resolution)

    # ------------------------------------------------------------------ 3. inspection
    def inspect(self, return_line_id: str, *, received_qty: int, grade: str, inspector_id: str,
                damage_noted: bool = False, notes: str | None = None) -> dict:
        if grade not in E.GRADES:
            raise DomainError("BAD_GRADE", f"grade must be one of {E.GRADES}")
        at = now()
        with self.engine.begin() as conn:
            rl = self._must(conn, "return_line", return_line_id, for_update=True)
            rma = self._must(conn, "return_request", rl["rma_id"], for_update=True)
            if rma["status"] != "RECEIVED":
                raise DomainError("NOT_RECEIVED", f"RMA is {rma['status']}; inspect only after the parcel is received", 409)
            if rl["approved_qty"] == 0:
                raise DomainError("NOT_APPROVED", "this line was not approved for return", 409)
            if db.select(conn, "inspection", "return_line_id = :l", {"l": return_line_id}):
                raise DomainError("ALREADY_INSPECTED", "line already inspected; inspections are immutable", 409)
            if not 0 <= received_qty <= rl["approved_qty"]:
                raise DomainError("BAD_QTY", f"received_qty must be between 0 and approved {rl['approved_qty']}")
            s = db.select(conn, "return_shipment", "rma_id = :r", {"r": rma["rma_id"]})[0]
            db.insert(conn, "inspection", dict(
                inspection_id=ids.inspection_id(db.next_counter(conn, "inspection")), return_line_id=return_line_id,
                rts_id=s["rts_id"], inspected_at=at, received_qty=received_qty, grade=grade,
                damage_noted=damage_noted or grade == "D_DAMAGED", inspector_id=inspector_id, notes=notes))
            if received_qty == 0:
                db.update(conn, "return_line", return_line_id, resolution="NONE")
            approved = db.select(conn, "return_line", "rma_id = :r AND approved_qty > 0", {"r": rma["rma_id"]})
            inspected = db.select(conn, "inspection", "rts_id = :s", {"s": s["rts_id"]})
            if len(inspected) == len(approved):
                self._move_rma(conn, rma, "INSPECTED", "OPERATOR", inspector_id, at)
                self._maybe_complete(conn, rma, at)
        self._bump()
        return self.get_return(rl["rma_id"])

    # ------------------------------------------------------------------ 4. disposition
    def dispose(self, return_line_id: str, operator: str) -> dict:
        at = now()
        with self.engine.begin() as conn:
            rl = self._must(conn, "return_line", return_line_id, for_update=True)
            rma = self._must(conn, "return_request", rl["rma_id"], for_update=True)
            ins = db.select(conn, "inspection", "return_line_id = :l", {"l": return_line_id})
            if not ins:
                raise DomainError("NOT_INSPECTED", "inventory cannot move before inspection", 409)
            ins = ins[0]
            if ins["received_qty"] == 0:
                raise DomainError("NOTHING_RECEIVED", "no units were received for this line", 409)
            if db.select(conn, "inventory_disposition", "return_line_id = :l", {"l": return_line_id}):
                raise DomainError("ALREADY_DISPOSED", "disposition already recorded", 409)
            ol = db.get(conn, "order_line", rl["order_line_id"])
            d = decide_disposition(self.policy, grade=ins["grade"], category=ol["category"], received_qty=ins["received_qty"])
            dsp = ids.disposition_id(db.next_counter(conn, "inventory_disposition"))
            db.insert(conn, "inventory_disposition", dict(
                disposition_id=dsp, return_line_id=return_line_id, inspection_id=ins["inspection_id"],
                product_id=ol["product_id"], decided_at=at, disposition=d.disposition, to_location=d.to_location,
                quantity=d.quantity, sellable_qty_delta=d.sellable_qty_delta))
            self._event(conn, rma["rma_id"], "INVENTORY_DISPOSITION", dsp, None, d.disposition, "OPERATOR", operator, at)
            if rma["request_type"] == "EXCHANGE":
                db.update(conn, "return_line", return_line_id, resolution="EXCHANGE", exchange_product_id=ol["product_id"])
            self._maybe_complete(conn, rma, at)
        self._bump()
        return self.get_return(rl["rma_id"])

    # ------------------------------------------------------------------ 5. refund
    def refund(self, return_line_id: str, *, idempotency_key: str, operator: str,
               amount_paise: int | None = None) -> tuple[dict, int]:
        """Returns (refund_row, http_status). Replays return the original row unchanged."""
        if not idempotency_key or len(idempotency_key) > 80:
            raise DomainError("BAD_IDEMPOTENCY_KEY", "Idempotency-Key header (1-80 chars) is required", 400)
        at = now()
        with self.engine.begin() as conn:
            rl = self._must(conn, "return_line", return_line_id, for_update=True)   # serialises payouts per line
            rma = self._must(conn, "return_request", rl["rma_id"], for_update=True)
            replay = db.select(conn, "refund", "idempotency_key = :k", {"k": idempotency_key})
            if replay:
                if replay[0]["return_line_id"] != return_line_id:
                    raise DomainError("KEY_REUSED", "idempotency key already used for another line", 409)
                return replay[0], 200
            if rma["request_type"] != "REFUND":
                raise DomainError("NOT_A_REFUND", "exchange requests are settled by replacement, not refund", 409)
            ins = db.select(conn, "inspection", "return_line_id = :l", {"l": return_line_id})
            if not ins:
                raise DomainError("NOT_INSPECTED", "refunds are issued only after inspection", 409)
            ins = ins[0]
            if ins["received_qty"] == 0:
                raise DomainError("NOTHING_RECEIVED", "no units were received for this line", 409)
            existing = db.select(conn, "refund", "return_line_id = :l", {"l": return_line_id}, order_by="attempt_no")
            guard = refund_guard(existing, idempotency_key)
            attempt = (existing[-1]["attempt_no"] + 1) if existing else 1
            ol = db.get(conn, "order_line", rl["order_line_id"])
            order = db.get(conn, "sales_order", ol["order_id"])
            prior = [r for r in db.select(conn, "refund", "status = 'SUCCEEDED' AND return_line_id IN "
                     "(SELECT return_line_id FROM return_line WHERE order_line_id = :o)", {"o": ol["order_line_id"]})]
            rma_prior = db.select(conn, "refund", "rma_id = :r AND status = 'SUCCEEDED'", {"r": rma["rma_id"]})
            fee_taken = any(not self.policy.is_merchant_fault(self._must(conn, "return_line", r["return_line_id"])["reason_code"])
                            for r in rma_prior)
            base = dict(return_line_id=return_line_id, rma_id=rma["rma_id"], attempt_no=attempt,
                        idempotency_key=idempotency_key, requested_at=at, processed_at=at,
                        method=refund_method(order["payment_method"]))
            rid = ids.refund_id(db.next_counter(conn, "refund"))

            components = ("refund_qty", "item_amount_paise", "tax_amount_paise", "grade_refund_bp",
                          "restocking_fee_paise", "shipping_fee_paise")

            def blocked(status, requested, calc=None, dup_of=None, source=None):
                vals = {k: (source[k] if source else getattr(calc, k) if calc else 0) for k in components}
                row = base | vals | dict(refund_id=rid, requested_amount_paise=requested, amount_paise=0,
                                         status=status, duplicate_of_refund_id=dup_of)
                db.insert(conn, "refund", row)
                self._event(conn, rma["rma_id"], "REFUND", rid, None, status, "OPERATOR", operator, at)
                return row

            if guard.outcome == "DUPLICATE":
                orig = next(r for r in existing if r["refund_id"] == guard.existing_refund_id)
                row = blocked("REJECTED_DUPLICATE", orig["amount_paise"], dup_of=orig["refund_id"], source=orig)
                self._bump()
                return row, 409
            try:
                calc = compute_refund(self.policy, LineEconomics(ol["quantity"], ol["taxable_paise"], ol["tax_paise"], ol["line_paid_paise"]),
                                      category=ol["category"], reason_code=rl["reason_code"], grade=ins["grade"],
                                      refund_qty=ins["received_qty"],
                                      previously_refunded_qty=sum(r["refund_qty"] for r in prior),
                                      previously_refunded_paise=sum(r["amount_paise"] for r in prior),
                                      charge_shipping_fee=not fee_taken)
            except RefundExceedsPaid as exc:
                row = blocked("REJECTED_EXCEEDS_PAID", amount_paise or 0)
                self._bump()
                return row, 422
            if amount_paise is not None and amount_paise != calc.amount_paise:
                remaining = ol["line_paid_paise"] - sum(r["amount_paise"] for r in prior)
                if amount_paise > remaining:
                    row = blocked("REJECTED_EXCEEDS_PAID", amount_paise, calc)
                    self._bump()
                    return row, 422
                raise DomainError("AMOUNT_MISMATCH", f"refund amount is computed by policy ({calc.amount_paise} paise); "
                                  "manual amounts are not accepted", 422, {"computed_amount_paise": calc.amount_paise})
            row = base | dict(refund_id=rid, refund_qty=calc.refund_qty, item_amount_paise=calc.item_amount_paise,
                              tax_amount_paise=calc.tax_amount_paise, grade_refund_bp=calc.grade_refund_bp,
                              restocking_fee_paise=calc.restocking_fee_paise, shipping_fee_paise=calc.shipping_fee_paise,
                              requested_amount_paise=calc.amount_paise, amount_paise=calc.amount_paise,
                              status="SUCCEEDED", duplicate_of_refund_id=None)
            db.insert(conn, "refund", row)
            self._event(conn, rma["rma_id"], "REFUND", rid, None, "SUCCEEDED", "SYSTEM", "PAYMENTS", at)
            db.update(conn, "return_line", return_line_id, resolution="REFUND")
            self._maybe_complete(conn, rma, at)
        self._bump()
        return row, 201

    def _maybe_complete(self, conn, rma, at):
        if rma["status"] != "INSPECTED":
            return
        lines = db.select(conn, "return_line", "rma_id = :r AND approved_qty > 0", {"r": rma["rma_id"]})
        disposed = {d["return_line_id"] for d in db.select(conn, "inventory_disposition",
                    "return_line_id IN (SELECT return_line_id FROM return_line WHERE rma_id = :r)", {"r": rma["rma_id"]})}
        for l in lines:
            if l["resolution"] == "PENDING":
                return
            if l["resolution"] in ("REFUND", "EXCHANGE") and l["return_line_id"] not in disposed:
                return
        self._move_rma(conn, rma, "COMPLETED", "SYSTEM", "SYSTEM", at)

    # ------------------------------------------------------------------ analytics
    def tables(self) -> dict[str, list[dict]]:
        with self.engine.connect() as conn:
            return db.load_all(conn)

    def metrics(self) -> dict:
        v = self._version
        if self._metrics_cache and self._metrics_cache[0] == v:
            return self._metrics_cache[1]
        m = compute_metrics(self.tables(), self.policy)
        self._metrics_cache = (v, m)
        return m

    def exceptions(self, limit: int = 100) -> dict:
        """Operational exception queue — what needs a human today."""
        t = now()
        sla = self.policy.sla
        out = defaultdict(list)
        with self.engine.connect() as conn:
            for s in db.select(conn, "return_shipment", "status = 'LOST'", order_by="rts_id DESC", limit=limit):
                out["lost_shipments"].append(dict(rma_id=s["rma_id"], rts_id=s["rts_id"], carrier=s["carrier"],
                                                  action="raise carrier claim; contact customer"))
            for s in db.select(conn, "return_shipment", "status = 'RECEIVED'", order_by="received_at", limit=2000):
                rma = db.get(conn, "return_request", s["rma_id"])
                if rma["status"] == "RECEIVED" and (t - s["received_at"]).days >= sla.get("receipt_to_inspection_days", 2):
                    out["awaiting_inspection_past_sla"].append(dict(rma_id=s["rma_id"], received_at=s["received_at"]))
            for r in db.select(conn, "refund", "status <> 'SUCCEEDED'", order_by="refund_id DESC", limit=limit):
                out["blocked_refund_attempts"].append(dict(refund_id=r["refund_id"], rma_id=r["rma_id"], status=r["status"],
                                                           requested_amount_paise=r["requested_amount_paise"]))
            for i in db.select(conn, "inspection", "received_qty = 0", order_by="inspection_id DESC", limit=limit):
                out["empty_or_missing_parcels"].append(dict(inspection_id=i["inspection_id"], return_line_id=i["return_line_id"]))
            for r in db.select(conn, "return_request", "override_flag = :t", {"t": True}, order_by="rma_id DESC", limit=limit):
                out["window_overrides"].append(dict(rma_id=r["rma_id"], by=r["override_by"], reason=r["override_reason"]))
        return {k: v[:limit] for k, v in out.items()} | {"counts": {k: len(v) for k, v in out.items()}}

    def integrity(self) -> dict:
        """Run the five anomaly detectors + core checks over live data."""
        from returns_platform.validation.checks import run_suite
        suite = run_suite(self.tables(), self.policy, [])
        return {"checks": [dict(name=r.name, group=r.group, description=r.description, flagged=len(r.flagged),
                                examples=r.examples) for r in suite.results if r.flagged or r.group == "detector"]}
