# Data dictionary

_Generated from `returns_platform/schema/spec.py`. Do not edit by hand — run `make docs`._

Conventions: money is integer **paise** (₹1 = 100 paise) to keep arithmetic exact; rates are **basis points** (10000 = 100%); timestamps are UTC `YYYY-MM-DD HH:MM:SS`; booleans are `true`/`false` in CSV/JSON.

## Relationships and cardinality

| Parent | Child | Cardinality | Optional? | Notes |
|---|---|---|---|---|
| customer | sales_order | 1 : N | child required | every order has exactly one customer |
| sales_order | order_line | 1 : N (1..6) | child required | line totals roll up to the order |
| product | order_line | 1 : N | — | category snapshot copied to the line at sale time |
| sales_order | return_request | 1 : 0..N | optional | most orders are never returned |
| customer | return_request | 1 : 0..N | optional | must equal the order's customer |
| return_request | return_line | 1 : N (1..6) | child required | |
| order_line | return_line | 1 : 0..N | optional | cumulative approved qty ≤ purchased qty |
| return_request | return_authorization | 1 : 1 | required | every request gets a decision (even REJECTED) |
| return_request | return_shipment | 1 : 0..1 | optional | none when rejected |
| return_line | inspection | 1 : 0..1 | optional | only for received lines |
| return_shipment | inspection | 1 : N | | |
| return_line | refund | 1 : 0..N | optional | one row per attempt; at most one SUCCEEDED |
| refund | refund (duplicate_of) | 1 : 0..N | optional | self-reference for blocked duplicates |
| return_line | inventory_disposition | 1 : 0..1 | optional | requires an inspection |
| inspection | inventory_disposition | 1 : 0..1 | | |
| return_request | return_event | 1 : N | required | append-only history |

Many-to-many: **sales_order ↔ product** is N:M, resolved by the bridge table `order_line`;
**return_request ↔ order_line** is N:M, resolved by the bridge table `return_line`.

## `customer` — Customer

Synthetic shopper. No real personal data.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `customer_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Customer identifier. Format per ids.PATTERNS['customer']; immutable |
| `full_name` | text (TEXT) | Y |  |  |  |  | Synthetic display name.  |
| `email` | text (TEXT) | Y |  |  |  | Y | Synthetic email on the reserved example.com domain. Ends with @example.com |
| `city` | text (TEXT) | Y |  |  |  |  | City of the default address.  |
| `segment` | text (TEXT) | Y |  | NEW, REGULAR, LOYAL, VIP |  |  | Loyalty segment.  |
| `created_at` | date (DATE) | Y |  |  |  |  | Account creation date.  |

## `product` — Product (reference)

Catalogue item. Category drives the return policy.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `product_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Stock keeping unit. Format per ids.PATTERNS['product']; immutable |
| `product_name` | text (TEXT) | Y |  |  |  |  | Display name.  |
| `category` | text (TEXT) | Y |  | ELECTRONICS, APPLIANCES, APPAREL, FOOTWEAR, HOME_KITCHEN, BOOKS, TOYS, BEAUTY, GROCERY, GIFT_CARDS |  |  | Policy category.  |
| `list_price_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Current list price. > 0 |
| `is_active` | bool (BOOLEAN) | Y |  |  |  |  | Sellable today.  |

## `sales_order` — Order

Customer purchase header. Totals are derived from its lines.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `order_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Order number. Format per ids.PATTERNS['order']; immutable |
| `customer_id` | id (VARCHAR(40)) | Y | FK → `customer.customer_id` |  |  |  | Purchaser. Must exist in customer.customer_id |
| `order_date` | date (DATE) | Y |  |  |  |  | Date placed.  |
| `delivered_date` | date (DATE) | Y |  |  |  |  | Date delivered; return window starts here. >= order_date |
| `channel` | text (TEXT) | Y |  | WEB, APP, STORE |  |  | Sales channel.  |
| `payment_method` | text (TEXT) | Y |  | CARD, UPI, NETBANKING, COD, WALLET |  |  | Tender used.  |
| `status` | text (TEXT) | Y |  | DELIVERED |  |  | Fulfilment status.  |
| `coupon_code` | text (TEXT) | N |  |  |  |  | Order-level coupon, if any.  |
| `order_discount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Order-level coupon value, allocated to lines pro rata. = SUM(order_line.allocated_order_discount_paise) |
| `subtotal_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Gross merchandise value. = SUM(order_line.quantity * unit_price_paise) |
| `line_discount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Sum of line-level discounts. = SUM(order_line.line_discount_paise) |
| `tax_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | GST collected. = SUM(order_line.tax_paise) |
| `shipping_fee_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Outbound shipping charged (non-refundable).  |
| `total_paid_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Amount the customer paid. = SUM(order_line.line_paid_paise) + shipping_fee_paise |

- Check `ck_order_dates`: `delivered_date >= order_date`

## `order_line` — OrderLine

One product on an order with fully derived price waterfall.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `order_line_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Order line id (<order_id>-NN). Format per ids.PATTERNS['order_line']; immutable |
| `order_id` | id (VARCHAR(40)) | Y | FK → `sales_order.order_id` |  |  |  | Parent order. Must exist in sales_order.order_id |
| `line_no` | int (BIGINT) | Y |  | 1..99 |  |  | Line number within the order.  |
| `product_id` | id (VARCHAR(40)) | Y | FK → `product.product_id` |  |  |  | Product sold. Must exist in product.product_id |
| `category` | text (TEXT) | Y |  | ELECTRONICS, APPLIANCES, APPAREL, FOOTWEAR, HOME_KITCHEN, BOOKS, TOYS, BEAUTY, GROCERY, GIFT_CARDS |  |  | Category snapshot at time of sale (effective-dated). = product.category at sale |
| `quantity` | int (BIGINT) | Y |  | 1.. | units |  | Units purchased.  |
| `unit_price_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Selling price per unit before discounts. > 0 |
| `line_discount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Line-level promotion. <= quantity * unit_price_paise |
| `allocated_order_discount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Share of order coupon (largest-remainder pro rata).  |
| `taxable_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Net value before tax. = quantity*unit_price - line_discount - allocated_order_discount |
| `tax_rate_bp` | bp (INTEGER) | Y |  | 0..2800 | basis points |  | GST rate snapshot.  |
| `tax_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | GST on this line. = round_half_up(taxable * tax_rate_bp / 10000) |
| `line_paid_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | What the customer paid for this line. = taxable + tax |

- Unique together: `(order_id, line_no)`
- Check `ck_ol_taxable`: `taxable_paise = quantity * unit_price_paise - line_discount_paise - allocated_order_discount_paise`
- Check `ck_ol_paid`: `line_paid_paise = taxable_paise + tax_paise`

## `return_request` — ReturnRequest

Customer's request to return part of one order (the RMA).

**Lifecycle:** REQUESTED → AUTHORIZED | REJECTED; AUTHORIZED → IN_TRANSIT | EXPIRED; IN_TRANSIT → RECEIVED | SHIPMENT_LOST; RECEIVED → INSPECTED → COMPLETED

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `rma_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | RMA number. Format per ids.PATTERNS['return_request']; immutable |
| `order_id` | id (VARCHAR(40)) | Y | FK → `sales_order.order_id` |  |  |  | Order being returned against. Must exist in sales_order.order_id |
| `customer_id` | id (VARCHAR(40)) | Y | FK → `customer.customer_id` |  |  |  | Requester. Must exist in customer.customer_id |
| `requested_at` | ts (TIMESTAMP) | Y |  |  |  |  | When the customer raised the request. date >= order.delivered_date |
| `request_type` | text (TEXT) | Y |  | REFUND, EXCHANGE |  |  | Refund or exchange.  |
| `channel` | text (TEXT) | Y |  | WEB, APP, STORE |  |  | Where the request was raised.  |
| `policy_version` | text (TEXT) | Y |  |  |  |  | Policy version applied.  |
| `override_flag` | bool (BOOLEAN) | Y |  |  |  |  | Agent overrode the return window.  |
| `override_reason` | text (TEXT) | N |  |  |  |  | Why the window was overridden. Required when override_flag |
| `override_by` | text (TEXT) | N |  |  |  |  | Agent who approved the override. Required when override_flag |
| `status` | text (TEXT) | Y |  | REQUESTED, REJECTED, AUTHORIZED, IN_TRANSIT, SHIPMENT_LOST, EXPIRED, RECEIVED, INSPECTED, COMPLETED |  |  | Current lifecycle state (projection of the latest event). = to_state of latest RMA_STATE_CHANGED event |

- Check `ck_rr_override`: `(override_flag = FALSE) OR (override_reason IS NOT NULL AND override_by IS NOT NULL)`

## `return_line` — ReturnLine

One order line on an RMA with its eligibility decision.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `return_line_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Return line id (RMAL-<rma_id>-NN). Format per ids.PATTERNS['return_line']; immutable |
| `rma_id` | id (VARCHAR(40)) | Y | FK → `return_request.rma_id` |  |  |  | Parent RMA. Must exist in return_request.rma_id |
| `line_no` | int (BIGINT) | Y |  | 1..99 |  |  | Line number within the RMA.  |
| `order_line_id` | id (VARCHAR(40)) | Y | FK → `order_line.order_line_id` |  |  |  | Purchased line being returned. Must exist in order_line.order_line_id |
| `reason_code` | text (TEXT) | Y |  | DEFECTIVE, WRONG_ITEM, NOT_AS_DESCRIBED, ARRIVED_DAMAGED, SIZE_FIT, CHANGED_MIND, BETTER_PRICE |  |  | Customer-stated reason.  |
| `requested_qty` | int (BIGINT) | Y |  | 1.. | units |  | Units the customer asked to return.  |
| `approved_qty` | int (BIGINT) | Y |  | 0.. | units |  | Units authorised. <= returnable qty (purchased - previously approved); 0 when rejected |
| `decision` | text (TEXT) | Y |  | APPROVED, PARTIALLY_APPROVED, REJECTED |  |  | Eligibility decision.  |
| `rejection_reason` | text (TEXT) | N |  | WINDOW_EXPIRED, CATEGORY_EXCLUDED, QTY_EXCEEDS_RETURNABLE, NOTHING_RETURNABLE |  |  | Why units were rejected/clamped. Required unless decision = APPROVED |
| `days_since_delivery` | int (BIGINT) | Y |  | 0.. | days |  | Request date minus delivered date.  |
| `window_days` | int (BIGINT) | Y |  | 0.. | days |  | Return window applied (policy snapshot).  |
| `resolution` | text (TEXT) | Y |  | REFUND, EXCHANGE, NONE, PENDING |  |  | How the line was settled.  |
| `exchange_product_id` | id (VARCHAR(40)) | N | FK → `product.product_id` |  |  |  | Replacement SKU for exchanges. Must exist in product.product_id |

- Unique together: `(rma_id, line_no)`
- Unique together: `(rma_id, order_line_id)`
- Check `ck_rl_approved`: `approved_qty <= requested_qty`

## `return_authorization` — ReturnAuthorization

Formal decision on an RMA (1:1; system decides synchronously, overrides by an operator).

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `auth_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Authorization id. Format per ids.PATTERNS['return_authorization']; immutable |
| `rma_id` | id (VARCHAR(40)) | Y | FK → `return_request.rma_id` |  |  | Y | RMA decided. Must exist in return_request.rma_id |
| `decided_at` | ts (TIMESTAMP) | Y |  |  |  |  | Decision timestamp. >= return_request.requested_at |
| `decision` | text (TEXT) | Y |  | APPROVED, PARTIALLY_APPROVED, REJECTED |  |  | Aggregate of line decisions.  |
| `approved_line_count` | int (BIGINT) | Y |  | 0.. |  |  | Lines with approved_qty > 0.  |
| `approved_qty_total` | int (BIGINT) | Y |  | 0.. | units |  | Sum of approved_qty.  |
| `ship_by_date` | date (DATE) | N |  |  |  |  | Customer must ship by. decided_at + ship_by_days; null when REJECTED |
| `decided_by` | text (TEXT) | Y |  |  |  |  | SYSTEM or operator id.  |
| `policy_version` | text (TEXT) | Y |  |  |  |  | Policy version applied.  |

## `return_shipment` — ReturnShipment

Reverse-logistics parcel for an authorised RMA.

**Lifecycle:** LABEL_CREATED → IN_TRANSIT → RECEIVED | LOST; LABEL_CREATED → EXPIRED

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `rts_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Return shipment id. Format per ids.PATTERNS['return_shipment']; immutable |
| `rma_id` | id (VARCHAR(40)) | Y | FK → `return_request.rma_id` |  |  | Y | RMA shipped. Must exist in return_request.rma_id |
| `carrier` | text (TEXT) | Y |  | DELHIVERY, BLUEDART, ECOM_EXPRESS, XPRESSBEES, INDIA_POST |  |  | Courier.  |
| `tracking_number` | text (TEXT) | Y |  |  |  | Y | Courier AWB.  |
| `label_created_at` | ts (TIMESTAMP) | Y |  |  |  |  | Label generated. >= authorization.decided_at |
| `shipped_at` | ts (TIMESTAMP) | N |  |  |  |  | Picked up by courier. >= label_created_at |
| `received_at` | ts (TIMESTAMP) | N |  |  |  |  | Received at warehouse. >= shipped_at |
| `status` | text (TEXT) | Y |  | LABEL_CREATED, IN_TRANSIT, RECEIVED, LOST, EXPIRED |  |  | Shipment status.  |

## `inspection` — Inspection

Warehouse grading of a received return line.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `inspection_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Inspection id. Format per ids.PATTERNS['inspection']; immutable |
| `return_line_id` | id (VARCHAR(40)) | Y | FK → `return_line.return_line_id` |  |  | Y | Line inspected. Must exist in return_line.return_line_id |
| `rts_id` | id (VARCHAR(40)) | Y | FK → `return_shipment.rts_id` |  |  |  | Parcel it arrived in. Must exist in return_shipment.rts_id |
| `inspected_at` | ts (TIMESTAMP) | Y |  |  |  |  | Inspection timestamp. >= shipment.received_at |
| `received_qty` | int (BIGINT) | Y |  | 0.. | units |  | Units physically received. <= return_line.approved_qty |
| `grade` | text (TEXT) | Y |  | A_NEW, B_LIKE_NEW, C_USED, D_DAMAGED, E_DEFECTIVE |  |  | Condition grade.  |
| `damage_noted` | bool (BOOLEAN) | Y |  |  |  |  | Visible damage recorded.  |
| `inspector_id` | text (TEXT) | Y |  |  |  |  | Operator id.  |
| `notes` | text (TEXT) | N |  |  |  |  | Free-text remarks.  |

## `refund` — Refund

Every refund attempt, successful or blocked (append-only).

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `refund_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Refund attempt id. Format per ids.PATTERNS['refund']; immutable |
| `return_line_id` | id (VARCHAR(40)) | Y | FK → `return_line.return_line_id` |  |  |  | Line refunded. Must exist in return_line.return_line_id |
| `rma_id` | id (VARCHAR(40)) | Y | FK → `return_request.rma_id` |  |  |  | RMA (denormalised for reporting). Must exist in return_request.rma_id |
| `attempt_no` | int (BIGINT) | Y |  | 1.. |  |  | 1 for first attempt on the line.  |
| `idempotency_key` | text (TEXT) | Y |  |  |  | Y | Client-supplied key; replays return the stored result.  |
| `requested_at` | ts (TIMESTAMP) | Y |  |  |  |  | Attempt timestamp. >= inspection.inspected_at |
| `processed_at` | ts (TIMESTAMP) | N |  |  |  |  | When the payment rail confirmed (or the guard rejected) the attempt. >= requested_at; required when SUCCEEDED |
| `refund_qty` | int (BIGINT) | Y |  | 0.. | units |  | Units refunded. = inspection.received_qty |
| `item_amount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Item value refunded after grade factor.  |
| `tax_amount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Tax refunded after grade factor.  |
| `grade_refund_bp` | bp (INTEGER) | Y |  | 0..10000 | basis points |  | Grade factor applied.  |
| `restocking_fee_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Deducted restocking fee.  |
| `shipping_fee_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Deducted return-shipping fee (once per RMA).  |
| `requested_amount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Amount this attempt asked to pay (computed, or an operator goodwill amount). = item + tax - restocking - shipping for computed attempts |
| `amount_paise` | money (BIGINT) | Y |  | 0.. | paise (INR/100) |  | Net paid out (0 for blocked attempts). = max(0, item + tax - restocking - shipping); cumulative <= line_paid_paise |
| `method` | text (TEXT) | Y |  | ORIGINAL_PAYMENT, STORE_CREDIT |  |  | Payout rail.  |
| `status` | text (TEXT) | Y |  | SUCCEEDED, REJECTED_DUPLICATE, REJECTED_EXCEEDS_PAID |  |  | Outcome of the attempt.  |
| `duplicate_of_refund_id` | id (VARCHAR(40)) | N | FK → `refund.refund_id` |  |  |  | Original refund for blocked duplicates. Must exist in refund.refund_id |

- Unique together: `(return_line_id, attempt_no)`
- Check `ck_ref_blocked_zero`: `status = 'SUCCEEDED' OR amount_paise = 0`

## `inventory_disposition` — InventoryDisposition

Where returned units go; controls inventory movement.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `disposition_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Disposition id. Format per ids.PATTERNS['inventory_disposition']; immutable |
| `return_line_id` | id (VARCHAR(40)) | Y | FK → `return_line.return_line_id` |  |  | Y | Line disposed. Must exist in return_line.return_line_id |
| `inspection_id` | id (VARCHAR(40)) | Y | FK → `inspection.inspection_id` |  |  |  | Inspection that justifies it. Must exist in inspection.inspection_id |
| `product_id` | id (VARCHAR(40)) | Y | FK → `product.product_id` |  |  |  | SKU moved. Must exist in product.product_id |
| `decided_at` | ts (TIMESTAMP) | Y |  |  |  |  | Decision timestamp. >= inspection.inspected_at |
| `disposition` | text (TEXT) | Y |  | RESTOCK, REFURBISH, QUARANTINE, SCRAP |  |  | Decision.  |
| `to_location` | text (TEXT) | Y |  | SELLABLE, REFURB_BAY, QUARANTINE_CAGE, SCRAP_BIN |  |  | Destination bin. Mapped from disposition |
| `quantity` | int (BIGINT) | Y |  | 0.. | units |  | Units moved. = inspection.received_qty |
| `sellable_qty_delta` | int (BIGINT) | Y |  | 0.. | units |  | Change to sellable stock. = quantity when RESTOCK, else 0 |

## `return_event` — ReturnEvent

Append-only audit trail of every state change.

| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |
|---|---|---|---|---|---|---|---|
| `event_id` | id (VARCHAR(40)) | Y | PK |  |  | Y | Event id (monotonic). Format per ids.PATTERNS['return_event']; immutable |
| `rma_id` | id (VARCHAR(40)) | Y | FK → `return_request.rma_id` |  |  |  | RMA the event belongs to. Must exist in return_request.rma_id |
| `entity_type` | text (TEXT) | Y |  | RETURN_REQUEST, RETURN_SHIPMENT, REFUND, INVENTORY_DISPOSITION |  |  | Entity that changed.  |
| `entity_id` | id (VARCHAR(40)) | Y |  |  |  |  | Id of the entity that changed.  |
| `from_state` | text (TEXT) | N |  |  |  |  | Previous state.  |
| `to_state` | text (TEXT) | Y |  |  |  |  | New state.  |
| `occurred_at` | ts (TIMESTAMP) | Y |  |  |  |  | When it happened.  |
| `actor_type` | text (TEXT) | Y |  | CUSTOMER, SYSTEM, OPERATOR, CARRIER |  |  | Who caused it.  |
| `actor_id` | text (TEXT) | Y |  |  |  |  | Actor identifier.  |
