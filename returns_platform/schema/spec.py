"""Single source of truth for the data model.

From this one spec we generate:
  * SQL DDL for SQLite and PostgreSQL          (schema/ddl.py)
  * the human-readable data dictionary         (schema/dictionary.py)
  * structural validation (types, required, enums, ranges, PK/FK/unique)
                                               (validation/checks.py)
so docs, database and validators can never drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from returns_platform.domain import enums as E

# Logical types -> (sqlite, postgres)
TYPES = {
    "id": ("TEXT", "VARCHAR(40)"),
    "text": ("TEXT", "TEXT"),
    "int": ("INTEGER", "BIGINT"),
    "money": ("INTEGER", "BIGINT"),     # paise (INR minor units)
    "bp": ("INTEGER", "INTEGER"),       # basis points
    "date": ("DATE", "DATE"),
    "ts": ("TIMESTAMP", "TIMESTAMP"),   # UTC, 'YYYY-MM-DD HH:MM:SS'
    "bool": ("BOOLEAN", "BOOLEAN"),
}


@dataclass(frozen=True)
class Col:
    name: str
    type: str
    desc: str
    required: bool = True
    pk: bool = False
    fk: str | None = None              # "table.column"
    unique: bool = False
    allowed: tuple[str, ...] | None = None
    min: int | None = None
    max: int | None = None
    unit: str | None = None
    id_kind: str | None = None         # key into ids.PATTERNS
    rule: str | None = None            # human-readable validation / derivation rule


@dataclass(frozen=True)
class Table:
    name: str
    entity: str
    desc: str
    cols: tuple[Col, ...]
    unique_together: tuple[tuple[str, ...], ...] = ()
    checks: tuple[tuple[str, str], ...] = ()       # (name, portable SQL expr)
    lifecycle: str | None = None
    relationships: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pk(self) -> Col:
        return next(c for c in self.cols if c.pk)

    def col(self, name: str) -> Col:
        return next(c for c in self.cols if c.name == name)


def pk(name, kind, desc):
    return Col(name, "id", desc, pk=True, unique=True, id_kind=kind, rule=f"Format per ids.PATTERNS['{kind}']; immutable")


def fk(name, ref, desc, required=True, kind=None, unique=False):
    return Col(name, "id", desc, required=required, fk=ref, id_kind=kind, unique=unique,
               rule=f"Must exist in {ref}")


def money(name, desc, rule=None, required=True):
    return Col(name, "money", desc, required=required, min=0, unit="paise (INR/100)", rule=rule)


SCHEMA: tuple[Table, ...] = (
    Table(
        "customer", "Customer", "Synthetic shopper. No real personal data.",
        (
            pk("customer_id", "customer", "Customer identifier"),
            Col("full_name", "text", "Synthetic display name"),
            Col("email", "text", "Synthetic email on the reserved example.com domain", unique=True,
                rule="Ends with @example.com"),
            Col("city", "text", "City of the default address"),
            Col("segment", "text", "Loyalty segment", allowed=E.CUSTOMER_SEGMENTS),
            Col("created_at", "date", "Account creation date"),
        ),
        relationships=("customer 1:N sales_order", "customer 1:N return_request"),
    ),
    Table(
        "product", "Product (reference)", "Catalogue item. Category drives the return policy.",
        (
            pk("product_id", "product", "Stock keeping unit"),
            Col("product_name", "text", "Display name"),
            Col("category", "text", "Policy category", allowed=E.CATEGORIES),
            money("list_price_paise", "Current list price", rule="> 0"),
            Col("is_active", "bool", "Sellable today"),
        ),
        relationships=("product 1:N order_line", "product 1:N inventory_disposition"),
    ),
    Table(
        "sales_order", "Order", "Customer purchase header. Totals are derived from its lines.",
        (
            pk("order_id", "order", "Order number"),
            fk("customer_id", "customer.customer_id", "Purchaser", kind="customer"),
            Col("order_date", "date", "Date placed"),
            Col("delivered_date", "date", "Date delivered; return window starts here",
                rule=">= order_date"),
            Col("channel", "text", "Sales channel", allowed=E.CHANNELS),
            Col("payment_method", "text", "Tender used", allowed=E.PAYMENT_METHODS),
            Col("status", "text", "Fulfilment status", allowed=E.ORDER_STATUSES),
            Col("coupon_code", "text", "Order-level coupon, if any", required=False),
            money("order_discount_paise", "Order-level coupon value, allocated to lines pro rata",
                  rule="= SUM(order_line.allocated_order_discount_paise)"),
            money("subtotal_paise", "Gross merchandise value",
                  rule="= SUM(order_line.quantity * unit_price_paise)"),
            money("line_discount_paise", "Sum of line-level discounts",
                  rule="= SUM(order_line.line_discount_paise)"),
            money("tax_paise", "GST collected", rule="= SUM(order_line.tax_paise)"),
            money("shipping_fee_paise", "Outbound shipping charged (non-refundable)"),
            money("total_paid_paise", "Amount the customer paid",
                  rule="= SUM(order_line.line_paid_paise) + shipping_fee_paise"),
        ),
        checks=(("ck_order_dates", "delivered_date >= order_date"),),
        relationships=("sales_order 1:N order_line (1..6 lines)", "sales_order 1:0..N return_request"),
    ),
    Table(
        "order_line", "OrderLine", "One product on an order with fully derived price waterfall.",
        (
            pk("order_line_id", "order_line", "Order line id (<order_id>-NN)"),
            fk("order_id", "sales_order.order_id", "Parent order", kind="order"),
            Col("line_no", "int", "Line number within the order", min=1, max=99),
            fk("product_id", "product.product_id", "Product sold", kind="product"),
            Col("category", "text", "Category snapshot at time of sale (effective-dated)",
                allowed=E.CATEGORIES, rule="= product.category at sale"),
            Col("quantity", "int", "Units purchased", min=1, unit="units"),
            money("unit_price_paise", "Selling price per unit before discounts", rule="> 0"),
            money("line_discount_paise", "Line-level promotion", rule="<= quantity * unit_price_paise"),
            money("allocated_order_discount_paise", "Share of order coupon (largest-remainder pro rata)"),
            money("taxable_paise", "Net value before tax",
                  rule="= quantity*unit_price - line_discount - allocated_order_discount"),
            Col("tax_rate_bp", "bp", "GST rate snapshot", min=0, max=2800, unit="basis points"),
            money("tax_paise", "GST on this line", rule="= round_half_up(taxable * tax_rate_bp / 10000)"),
            money("line_paid_paise", "What the customer paid for this line", rule="= taxable + tax"),
        ),
        unique_together=(("order_id", "line_no"),),
        checks=(
            ("ck_ol_taxable", "taxable_paise = quantity * unit_price_paise - line_discount_paise - allocated_order_discount_paise"),
            ("ck_ol_paid", "line_paid_paise = taxable_paise + tax_paise"),
        ),
        relationships=("order_line 1:0..N return_line (across RMAs, bounded by quantity)",),
    ),
    Table(
        "return_request", "ReturnRequest", "Customer's request to return part of one order (the RMA).",
        (
            pk("rma_id", "return_request", "RMA number"),
            fk("order_id", "sales_order.order_id", "Order being returned against", kind="order"),
            fk("customer_id", "customer.customer_id", "Requester", kind="customer",),
            Col("requested_at", "ts", "When the customer raised the request", rule="date >= order.delivered_date"),
            Col("request_type", "text", "Refund or exchange", allowed=E.REQUEST_TYPES),
            Col("channel", "text", "Where the request was raised", allowed=E.CHANNELS),
            Col("policy_version", "text", "Policy version applied"),
            Col("override_flag", "bool", "Agent overrode the return window"),
            Col("override_reason", "text", "Why the window was overridden", required=False,
                rule="Required when override_flag"),
            Col("override_by", "text", "Agent who approved the override", required=False,
                rule="Required when override_flag"),
            Col("status", "text", "Current lifecycle state (projection of the latest event)",
                allowed=E.RMA_STATES, rule="= to_state of latest RMA_STATE_CHANGED event"),
        ),
        checks=(("ck_rr_override", "(override_flag = FALSE) OR (override_reason IS NOT NULL AND override_by IS NOT NULL)"),),
        lifecycle="REQUESTED → AUTHORIZED | REJECTED; AUTHORIZED → IN_TRANSIT | EXPIRED; IN_TRANSIT → RECEIVED | SHIPMENT_LOST; RECEIVED → INSPECTED → COMPLETED",
        relationships=("return_request 1:N return_line (1..6)", "return_request 1:1 return_authorization",
                       "return_request 1:0..1 return_shipment", "return_request 1:N return_event"),
    ),
    Table(
        "return_line", "ReturnLine", "One order line on an RMA with its eligibility decision.",
        (
            pk("return_line_id", "return_line", "Return line id (RMAL-<rma_id>-NN)"),
            fk("rma_id", "return_request.rma_id", "Parent RMA", kind="return_request"),
            Col("line_no", "int", "Line number within the RMA", min=1, max=99),
            fk("order_line_id", "order_line.order_line_id", "Purchased line being returned", kind="order_line"),
            Col("reason_code", "text", "Customer-stated reason", allowed=E.REASON_CODES),
            Col("requested_qty", "int", "Units the customer asked to return", min=1, unit="units"),
            Col("approved_qty", "int", "Units authorised", min=0, unit="units",
                rule="<= returnable qty (purchased - previously approved); 0 when rejected"),
            Col("decision", "text", "Eligibility decision", allowed=E.LINE_DECISIONS),
            Col("rejection_reason", "text", "Why units were rejected/clamped", required=False,
                allowed=E.REJECTION_REASONS, rule="Required unless decision = APPROVED"),
            Col("days_since_delivery", "int", "Request date minus delivered date", min=0, unit="days"),
            Col("window_days", "int", "Return window applied (policy snapshot)", min=0, unit="days"),
            Col("resolution", "text", "How the line was settled", allowed=E.LINE_RESOLUTIONS),
            fk("exchange_product_id", "product.product_id", "Replacement SKU for exchanges",
               required=False, kind="product"),
        ),
        unique_together=(("rma_id", "line_no"), ("rma_id", "order_line_id")),
        checks=(("ck_rl_approved", "approved_qty <= requested_qty"),),
        relationships=("return_line 1:0..1 inspection", "return_line 1:0..N refund (attempts)",
                       "return_line 1:0..1 inventory_disposition"),
    ),
    Table(
        "return_authorization", "ReturnAuthorization", "Formal decision on an RMA (1:1).",
        (
            pk("auth_id", "return_authorization", "Authorization id"),
            fk("rma_id", "return_request.rma_id", "RMA decided", kind="return_request", unique=True),
            Col("decided_at", "ts", "Decision timestamp", rule=">= return_request.requested_at"),
            Col("decision", "text", "Aggregate of line decisions", allowed=E.AUTH_DECISIONS),
            Col("approved_line_count", "int", "Lines with approved_qty > 0", min=0),
            Col("approved_qty_total", "int", "Sum of approved_qty", min=0, unit="units"),
            Col("ship_by_date", "date", "Customer must ship by", required=False,
                rule="decided_at + ship_by_days; null when REJECTED"),
            Col("decided_by", "text", "SYSTEM or operator id"),
            Col("policy_version", "text", "Policy version applied"),
        ),
    ),
    Table(
        "return_shipment", "ReturnShipment", "Reverse-logistics parcel for an authorised RMA.",
        (
            pk("rts_id", "return_shipment", "Return shipment id"),
            fk("rma_id", "return_request.rma_id", "RMA shipped", kind="return_request", unique=True),
            Col("carrier", "text", "Courier", allowed=E.CARRIERS),
            Col("tracking_number", "text", "Courier AWB", unique=True),
            Col("label_created_at", "ts", "Label generated", rule=">= authorization.decided_at"),
            Col("shipped_at", "ts", "Picked up by courier", required=False, rule=">= label_created_at"),
            Col("received_at", "ts", "Received at warehouse", required=False, rule=">= shipped_at"),
            Col("status", "text", "Shipment status", allowed=E.SHIPMENT_STATUSES),
        ),
        lifecycle="LABEL_CREATED → IN_TRANSIT → RECEIVED | LOST; LABEL_CREATED → EXPIRED",
    ),
    Table(
        "inspection", "Inspection", "Warehouse grading of a received return line.",
        (
            pk("inspection_id", "inspection", "Inspection id"),
            fk("return_line_id", "return_line.return_line_id", "Line inspected", kind="return_line", unique=True),
            fk("rts_id", "return_shipment.rts_id", "Parcel it arrived in", kind="return_shipment"),
            Col("inspected_at", "ts", "Inspection timestamp", rule=">= shipment.received_at"),
            Col("received_qty", "int", "Units physically received", min=0, unit="units",
                rule="<= return_line.approved_qty"),
            Col("grade", "text", "Condition grade", allowed=E.GRADES),
            Col("damage_noted", "bool", "Visible damage recorded"),
            Col("inspector_id", "text", "Operator id"),
            Col("notes", "text", "Free-text remarks", required=False),
        ),
    ),
    Table(
        "refund", "Refund", "Every refund attempt, successful or blocked (append-only).",
        (
            pk("refund_id", "refund", "Refund attempt id"),
            fk("return_line_id", "return_line.return_line_id", "Line refunded", kind="return_line"),
            fk("rma_id", "return_request.rma_id", "RMA (denormalised for reporting)", kind="return_request"),
            Col("attempt_no", "int", "1 for first attempt on the line", min=1),
            Col("idempotency_key", "text", "Client-supplied key; replays return the stored result", unique=True),
            Col("requested_at", "ts", "Attempt timestamp", rule=">= inspection.inspected_at"),
            Col("refund_qty", "int", "Units refunded", min=0, unit="units", rule="= inspection.received_qty"),
            money("item_amount_paise", "Item value refunded after grade factor"),
            money("tax_amount_paise", "Tax refunded after grade factor"),
            Col("grade_refund_bp", "bp", "Grade factor applied", min=0, max=10000, unit="basis points"),
            money("restocking_fee_paise", "Deducted restocking fee"),
            money("shipping_fee_paise", "Deducted return-shipping fee (once per RMA)"),
            money("amount_paise", "Net paid out (0 for blocked attempts)",
                  rule="= max(0, item + tax - restocking - shipping); cumulative <= line_paid_paise"),
            Col("method", "text", "Payout rail", allowed=E.REFUND_METHODS),
            Col("status", "text", "Outcome of the attempt", allowed=E.REFUND_STATUSES),
            fk("duplicate_of_refund_id", "refund.refund_id", "Original refund for blocked duplicates",
               required=False, kind="refund"),
        ),
        unique_together=(("return_line_id", "attempt_no"),),
        checks=(("ck_ref_blocked_zero", "status = 'SUCCEEDED' OR amount_paise = 0"),),
    ),
    Table(
        "inventory_disposition", "InventoryDisposition", "Where returned units go; controls inventory movement.",
        (
            pk("disposition_id", "inventory_disposition", "Disposition id"),
            fk("return_line_id", "return_line.return_line_id", "Line disposed", kind="return_line", unique=True),
            fk("inspection_id", "inspection.inspection_id", "Inspection that justifies it", kind="inspection"),
            fk("product_id", "product.product_id", "SKU moved", kind="product"),
            Col("decided_at", "ts", "Decision timestamp", rule=">= inspection.inspected_at"),
            Col("disposition", "text", "Decision", allowed=E.DISPOSITIONS),
            Col("to_location", "text", "Destination bin", allowed=E.INVENTORY_LOCATIONS,
                rule="Mapped from disposition"),
            Col("quantity", "int", "Units moved", min=0, unit="units", rule="= inspection.received_qty"),
            Col("sellable_qty_delta", "int", "Change to sellable stock", min=0, unit="units",
                rule="= quantity when RESTOCK, else 0"),
        ),
    ),
    Table(
        "return_event", "ReturnEvent", "Append-only audit trail of every state change.",
        (
            pk("event_id", "return_event", "Event id (monotonic)"),
            fk("rma_id", "return_request.rma_id", "RMA the event belongs to", kind="return_request"),
            Col("entity_type", "text", "Entity that changed", allowed=E.EVENT_ENTITY_TYPES),
            Col("entity_id", "id", "Id of the entity that changed"),
            Col("from_state", "text", "Previous state", required=False),
            Col("to_state", "text", "New state"),
            Col("occurred_at", "ts", "When it happened"),
            Col("actor_type", "text", "Who caused it", allowed=E.ACTOR_TYPES),
            Col("actor_id", "text", "Actor identifier"),
        ),
    ),
)

TABLES: dict[str, Table] = {t.name: t for t in SCHEMA}
LOAD_ORDER: tuple[str, ...] = tuple(t.name for t in SCHEMA)   # parents before children
