-- Returns platform schema. GENERATED from returns_platform/schema/spec.py — do not edit by hand.
-- Dialect: postgres. Money in paise (INR minor units); timestamps UTC.
-- Customer: Synthetic shopper. No real personal data.
CREATE TABLE customer (
    customer_id VARCHAR(40) PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    segment TEXT NOT NULL CHECK (segment IN ('NEW', 'REGULAR', 'LOYAL', 'VIP')),
    created_at DATE NOT NULL
);

-- Product (reference): Catalogue item. Category drives the return policy.
CREATE TABLE product (
    product_id VARCHAR(40) PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('ELECTRONICS', 'APPLIANCES', 'APPAREL', 'FOOTWEAR', 'HOME_KITCHEN', 'BOOKS', 'TOYS', 'BEAUTY', 'GROCERY', 'GIFT_CARDS')),
    list_price_paise BIGINT NOT NULL CHECK (list_price_paise >= 0),
    is_active BOOLEAN NOT NULL
);

-- Order: Customer purchase header. Totals are derived from its lines.
CREATE TABLE sales_order (
    order_id VARCHAR(40) PRIMARY KEY,
    customer_id VARCHAR(40) NOT NULL REFERENCES customer(customer_id),
    order_date DATE NOT NULL,
    delivered_date DATE NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('WEB', 'APP', 'STORE')),
    payment_method TEXT NOT NULL CHECK (payment_method IN ('CARD', 'UPI', 'NETBANKING', 'COD', 'WALLET')),
    status TEXT NOT NULL CHECK (status IN ('DELIVERED')),
    coupon_code TEXT,
    order_discount_paise BIGINT NOT NULL CHECK (order_discount_paise >= 0),
    subtotal_paise BIGINT NOT NULL CHECK (subtotal_paise >= 0),
    line_discount_paise BIGINT NOT NULL CHECK (line_discount_paise >= 0),
    tax_paise BIGINT NOT NULL CHECK (tax_paise >= 0),
    shipping_fee_paise BIGINT NOT NULL CHECK (shipping_fee_paise >= 0),
    total_paid_paise BIGINT NOT NULL CHECK (total_paid_paise >= 0),
    CONSTRAINT ck_order_dates CHECK (delivered_date >= order_date)
);

CREATE INDEX ix_sales_order_customer_id ON sales_order(customer_id);

-- OrderLine: One product on an order with fully derived price waterfall.
CREATE TABLE order_line (
    order_line_id VARCHAR(40) PRIMARY KEY,
    order_id VARCHAR(40) NOT NULL REFERENCES sales_order(order_id),
    line_no BIGINT NOT NULL CHECK (line_no >= 1 AND line_no <= 99),
    product_id VARCHAR(40) NOT NULL REFERENCES product(product_id),
    category TEXT NOT NULL CHECK (category IN ('ELECTRONICS', 'APPLIANCES', 'APPAREL', 'FOOTWEAR', 'HOME_KITCHEN', 'BOOKS', 'TOYS', 'BEAUTY', 'GROCERY', 'GIFT_CARDS')),
    quantity BIGINT NOT NULL CHECK (quantity >= 1),
    unit_price_paise BIGINT NOT NULL CHECK (unit_price_paise >= 0),
    line_discount_paise BIGINT NOT NULL CHECK (line_discount_paise >= 0),
    allocated_order_discount_paise BIGINT NOT NULL CHECK (allocated_order_discount_paise >= 0),
    taxable_paise BIGINT NOT NULL CHECK (taxable_paise >= 0),
    tax_rate_bp INTEGER NOT NULL CHECK (tax_rate_bp >= 0 AND tax_rate_bp <= 2800),
    tax_paise BIGINT NOT NULL CHECK (tax_paise >= 0),
    line_paid_paise BIGINT NOT NULL CHECK (line_paid_paise >= 0),
    CONSTRAINT uq_order_line_order_id_line_no UNIQUE (order_id, line_no),
    CONSTRAINT ck_ol_taxable CHECK (taxable_paise = quantity * unit_price_paise - line_discount_paise - allocated_order_discount_paise),
    CONSTRAINT ck_ol_paid CHECK (line_paid_paise = taxable_paise + tax_paise)
);

CREATE INDEX ix_order_line_order_id ON order_line(order_id);

CREATE INDEX ix_order_line_product_id ON order_line(product_id);

-- ReturnRequest: Customer's request to return part of one order (the RMA).
CREATE TABLE return_request (
    rma_id VARCHAR(40) PRIMARY KEY,
    order_id VARCHAR(40) NOT NULL REFERENCES sales_order(order_id),
    customer_id VARCHAR(40) NOT NULL REFERENCES customer(customer_id),
    requested_at TIMESTAMP NOT NULL,
    request_type TEXT NOT NULL CHECK (request_type IN ('REFUND', 'EXCHANGE')),
    channel TEXT NOT NULL CHECK (channel IN ('WEB', 'APP', 'STORE')),
    policy_version TEXT NOT NULL,
    override_flag BOOLEAN NOT NULL,
    override_reason TEXT,
    override_by TEXT,
    status TEXT NOT NULL CHECK (status IN ('REQUESTED', 'REJECTED', 'AUTHORIZED', 'IN_TRANSIT', 'SHIPMENT_LOST', 'EXPIRED', 'RECEIVED', 'INSPECTED', 'COMPLETED')),
    CONSTRAINT ck_rr_override CHECK ((override_flag = FALSE) OR (override_reason IS NOT NULL AND override_by IS NOT NULL))
);

CREATE INDEX ix_return_request_order_id ON return_request(order_id);

CREATE INDEX ix_return_request_customer_id ON return_request(customer_id);

-- ReturnLine: One order line on an RMA with its eligibility decision.
CREATE TABLE return_line (
    return_line_id VARCHAR(40) PRIMARY KEY,
    rma_id VARCHAR(40) NOT NULL REFERENCES return_request(rma_id),
    line_no BIGINT NOT NULL CHECK (line_no >= 1 AND line_no <= 99),
    order_line_id VARCHAR(40) NOT NULL REFERENCES order_line(order_line_id),
    reason_code TEXT NOT NULL CHECK (reason_code IN ('DEFECTIVE', 'WRONG_ITEM', 'NOT_AS_DESCRIBED', 'ARRIVED_DAMAGED', 'SIZE_FIT', 'CHANGED_MIND', 'BETTER_PRICE')),
    requested_qty BIGINT NOT NULL CHECK (requested_qty >= 1),
    approved_qty BIGINT NOT NULL CHECK (approved_qty >= 0),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'PARTIALLY_APPROVED', 'REJECTED')),
    rejection_reason TEXT CHECK (rejection_reason IN ('WINDOW_EXPIRED', 'CATEGORY_EXCLUDED', 'QTY_EXCEEDS_RETURNABLE', 'NOTHING_RETURNABLE')),
    days_since_delivery BIGINT NOT NULL CHECK (days_since_delivery >= 0),
    window_days BIGINT NOT NULL CHECK (window_days >= 0),
    resolution TEXT NOT NULL CHECK (resolution IN ('REFUND', 'EXCHANGE', 'NONE', 'PENDING')),
    exchange_product_id VARCHAR(40) REFERENCES product(product_id),
    CONSTRAINT uq_return_line_rma_id_line_no UNIQUE (rma_id, line_no),
    CONSTRAINT uq_return_line_rma_id_order_line_id UNIQUE (rma_id, order_line_id),
    CONSTRAINT ck_rl_approved CHECK (approved_qty <= requested_qty)
);

CREATE INDEX ix_return_line_rma_id ON return_line(rma_id);

CREATE INDEX ix_return_line_order_line_id ON return_line(order_line_id);

CREATE INDEX ix_return_line_exchange_product_id ON return_line(exchange_product_id);

-- ReturnAuthorization: Formal decision on an RMA (1:1; system decides synchronously, overrides by an operator).
CREATE TABLE return_authorization (
    auth_id VARCHAR(40) PRIMARY KEY,
    rma_id VARCHAR(40) NOT NULL UNIQUE REFERENCES return_request(rma_id),
    decided_at TIMESTAMP NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'PARTIALLY_APPROVED', 'REJECTED')),
    approved_line_count BIGINT NOT NULL CHECK (approved_line_count >= 0),
    approved_qty_total BIGINT NOT NULL CHECK (approved_qty_total >= 0),
    ship_by_date DATE,
    decided_by TEXT NOT NULL,
    policy_version TEXT NOT NULL
);

-- ReturnShipment: Reverse-logistics parcel for an authorised RMA.
CREATE TABLE return_shipment (
    rts_id VARCHAR(40) PRIMARY KEY,
    rma_id VARCHAR(40) NOT NULL UNIQUE REFERENCES return_request(rma_id),
    carrier TEXT NOT NULL CHECK (carrier IN ('DELHIVERY', 'BLUEDART', 'ECOM_EXPRESS', 'XPRESSBEES', 'INDIA_POST')),
    tracking_number TEXT NOT NULL UNIQUE,
    label_created_at TIMESTAMP NOT NULL,
    shipped_at TIMESTAMP,
    received_at TIMESTAMP,
    status TEXT NOT NULL CHECK (status IN ('LABEL_CREATED', 'IN_TRANSIT', 'RECEIVED', 'LOST', 'EXPIRED'))
);

-- Inspection: Warehouse grading of a received return line.
CREATE TABLE inspection (
    inspection_id VARCHAR(40) PRIMARY KEY,
    return_line_id VARCHAR(40) NOT NULL UNIQUE REFERENCES return_line(return_line_id),
    rts_id VARCHAR(40) NOT NULL REFERENCES return_shipment(rts_id),
    inspected_at TIMESTAMP NOT NULL,
    received_qty BIGINT NOT NULL CHECK (received_qty >= 0),
    grade TEXT NOT NULL CHECK (grade IN ('A_NEW', 'B_LIKE_NEW', 'C_USED', 'D_DAMAGED', 'E_DEFECTIVE')),
    damage_noted BOOLEAN NOT NULL,
    inspector_id TEXT NOT NULL,
    notes TEXT
);

CREATE INDEX ix_inspection_rts_id ON inspection(rts_id);

-- Refund: Every refund attempt, successful or blocked (append-only).
CREATE TABLE refund (
    refund_id VARCHAR(40) PRIMARY KEY,
    return_line_id VARCHAR(40) NOT NULL REFERENCES return_line(return_line_id),
    rma_id VARCHAR(40) NOT NULL REFERENCES return_request(rma_id),
    attempt_no BIGINT NOT NULL CHECK (attempt_no >= 1),
    idempotency_key TEXT NOT NULL UNIQUE,
    requested_at TIMESTAMP NOT NULL,
    processed_at TIMESTAMP,
    refund_qty BIGINT NOT NULL CHECK (refund_qty >= 0),
    item_amount_paise BIGINT NOT NULL CHECK (item_amount_paise >= 0),
    tax_amount_paise BIGINT NOT NULL CHECK (tax_amount_paise >= 0),
    grade_refund_bp INTEGER NOT NULL CHECK (grade_refund_bp >= 0 AND grade_refund_bp <= 10000),
    restocking_fee_paise BIGINT NOT NULL CHECK (restocking_fee_paise >= 0),
    shipping_fee_paise BIGINT NOT NULL CHECK (shipping_fee_paise >= 0),
    requested_amount_paise BIGINT NOT NULL CHECK (requested_amount_paise >= 0),
    amount_paise BIGINT NOT NULL CHECK (amount_paise >= 0),
    method TEXT NOT NULL CHECK (method IN ('ORIGINAL_PAYMENT', 'STORE_CREDIT')),
    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'REJECTED_DUPLICATE', 'REJECTED_EXCEEDS_PAID')),
    duplicate_of_refund_id VARCHAR(40) REFERENCES refund(refund_id),
    CONSTRAINT uq_refund_return_line_id_attempt_no UNIQUE (return_line_id, attempt_no),
    CONSTRAINT ck_ref_blocked_zero CHECK (status = 'SUCCEEDED' OR amount_paise = 0)
);

CREATE INDEX ix_refund_return_line_id ON refund(return_line_id);

CREATE INDEX ix_refund_rma_id ON refund(rma_id);

CREATE INDEX ix_refund_duplicate_of_refund_id ON refund(duplicate_of_refund_id);

-- InventoryDisposition: Where returned units go; controls inventory movement.
CREATE TABLE inventory_disposition (
    disposition_id VARCHAR(40) PRIMARY KEY,
    return_line_id VARCHAR(40) NOT NULL UNIQUE REFERENCES return_line(return_line_id),
    inspection_id VARCHAR(40) NOT NULL REFERENCES inspection(inspection_id),
    product_id VARCHAR(40) NOT NULL REFERENCES product(product_id),
    decided_at TIMESTAMP NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('RESTOCK', 'REFURBISH', 'QUARANTINE', 'SCRAP')),
    to_location TEXT NOT NULL CHECK (to_location IN ('SELLABLE', 'REFURB_BAY', 'QUARANTINE_CAGE', 'SCRAP_BIN')),
    quantity BIGINT NOT NULL CHECK (quantity >= 0),
    sellable_qty_delta BIGINT NOT NULL CHECK (sellable_qty_delta >= 0)
);

CREATE INDEX ix_inventory_disposition_inspection_id ON inventory_disposition(inspection_id);

CREATE INDEX ix_inventory_disposition_product_id ON inventory_disposition(product_id);

-- ReturnEvent: Append-only audit trail of every state change.
CREATE TABLE return_event (
    event_id VARCHAR(40) PRIMARY KEY,
    rma_id VARCHAR(40) NOT NULL REFERENCES return_request(rma_id),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('RETURN_REQUEST', 'RETURN_SHIPMENT', 'REFUND', 'INVENTORY_DISPOSITION')),
    entity_id VARCHAR(40) NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('CUSTOMER', 'SYSTEM', 'OPERATOR', 'CARRIER')),
    actor_id TEXT NOT NULL
);

CREATE INDEX ix_return_event_rma_id ON return_event(rma_id);
