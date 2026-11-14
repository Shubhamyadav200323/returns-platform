# Validation summary

**Overall: PASS** — 79/79 checks passed. Seed `20261022`, policy `d01b1abaf780`.

A check passes when the records it flags are *exactly* the ones ground truth says it should flag: integrity checks must flag nothing; anomaly detectors must flag every labelled anomaly of their type and nothing else.

## Anomaly detection vs ground truth

| Ground-truth anomalies | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| 30 | 48 | 0 | 0 | 1.0 | 1.0 |

By type: RETURN_QTY_EXCEEDS_PURCHASED = 6, REFUND_EXCEEDS_PAID = 6, RESTOCK_BEFORE_INSPECTION = 6, LATE_RETURN_WITHOUT_OVERRIDE = 6, DUPLICATE_REFUND = 6

Eligibility decisions agreeing with rule-derived expected outcomes (non-anomalous lines): **100.0%** of 770.

## Checks

| Check | Group | Expects | Flagged | Expected | Status |
|---|---|---|---|---|---|
| `customer.pk_unique` | integrity | — | 0 | 0 | PASS |
| `customer.id_format` | integrity | — | 0 | 0 | PASS |
| `customer.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `customer.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `customer.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `product.pk_unique` | integrity | — | 0 | 0 | PASS |
| `product.id_format` | integrity | — | 0 | 0 | PASS |
| `product.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `product.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `product.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `sales_order.pk_unique` | integrity | — | 0 | 0 | PASS |
| `sales_order.id_format` | integrity | — | 0 | 0 | PASS |
| `sales_order.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `sales_order.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `sales_order.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `order_line.pk_unique` | integrity | — | 0 | 0 | PASS |
| `order_line.id_format` | integrity | — | 0 | 0 | PASS |
| `order_line.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `order_line.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `order_line.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `return_request.pk_unique` | integrity | — | 0 | 0 | PASS |
| `return_request.id_format` | integrity | — | 0 | 0 | PASS |
| `return_request.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `return_request.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `return_request.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `return_line.pk_unique` | integrity | — | 0 | 0 | PASS |
| `return_line.id_format` | integrity | — | 0 | 0 | PASS |
| `return_line.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `return_line.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `return_line.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `return_authorization.pk_unique` | integrity | — | 0 | 0 | PASS |
| `return_authorization.id_format` | integrity | — | 0 | 0 | PASS |
| `return_authorization.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `return_authorization.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `return_authorization.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `return_shipment.pk_unique` | integrity | — | 0 | 0 | PASS |
| `return_shipment.id_format` | integrity | — | 0 | 0 | PASS |
| `return_shipment.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `return_shipment.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `return_shipment.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `inspection.pk_unique` | integrity | — | 0 | 0 | PASS |
| `inspection.id_format` | integrity | — | 0 | 0 | PASS |
| `inspection.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `inspection.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `inspection.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `refund.pk_unique` | integrity | — | 0 | 0 | PASS |
| `refund.id_format` | integrity | — | 0 | 0 | PASS |
| `refund.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `refund.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `refund.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `inventory_disposition.pk_unique` | integrity | — | 0 | 0 | PASS |
| `inventory_disposition.id_format` | integrity | — | 0 | 0 | PASS |
| `inventory_disposition.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `inventory_disposition.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `inventory_disposition.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `return_event.pk_unique` | integrity | — | 0 | 0 | PASS |
| `return_event.id_format` | integrity | — | 0 | 0 | PASS |
| `return_event.required_enum_range` | integrity | — | 0 | 0 | PASS |
| `return_event.fk_integrity` | integrity | — | 0 | 0 | PASS |
| `return_event.unique_constraints` | integrity | — | 0 | 0 | PASS |
| `order_line.price_waterfall` | arithmetic | — | 0 | 0 | PASS |
| `sales_order.totals_reconcile` | arithmetic | — | 0 | 0 | PASS |
| `order_line.category_snapshot` | consistency | — | 0 | 0 | PASS |
| `return_authorization.aggregates` | arithmetic | — | 0 | 0 | PASS |
| `return.cross_entity_links` | consistency | — | 0 | 0 | PASS |
| `return_line.decision_fields` | consistency | — | 0 | 0 | PASS |
| `return_line.days_since_delivery` | arithmetic | — | 0 | 0 | PASS |
| `dates.ordering` | date_order | — | 0 | 0 | PASS |
| `lifecycle.event_paths` | lifecycle | — | 0 | 0 | PASS |
| `lifecycle.state_artifacts` | lifecycle | — | 0 | 0 | PASS |
| `inventory_disposition.controls_inventory` | policy | — | 0 | 0 | PASS |
| `refund.row_arithmetic` | arithmetic | — | 0 | 0 | PASS |
| `detect.return_qty_exceeds_purchased` | detector | RETURN_QTY_EXCEEDS_PURCHASED | 6 | 6 | PASS |
| `detect.refund_exceeds_paid` | detector | REFUND_EXCEEDS_PAID | 6 | 6 | PASS |
| `detect.restock_before_inspection` | detector | RESTOCK_BEFORE_INSPECTION | 6 | 6 | PASS |
| `detect.late_return_without_override` | detector | LATE_RETURN_WITHOUT_OVERRIDE | 6 | 6 | PASS |
| `detect.duplicate_refund` | detector | DUPLICATE_REFUND | 6 | 6 | PASS |
| `policy.eligibility_reexecution` | policy | RETURN_QTY_EXCEEDS_PURCHASED, LATE_RETURN_WITHOUT_OVERRIDE | 12 | 12 | PASS |
| `policy.refund_reexecution` | policy | REFUND_EXCEEDS_PAID | 6 | 6 | PASS |

## Scenario coverage

| Scenario | Count |
|---|---|
| partial_qty_returns | 45 |
| partial_order_returns (subset of lines) | 277 |
| late_requests | 149 |
| late_requests_with_override_approved | 47 |
| category_exclusions | 68 |
| qty_over_requests_clamped | 39 |
| follow_up_rmas_same_order | 10 |
| nothing_left_to_return | 4 |
| exchanges | 126 |
| damaged_goods (grade D) | 77 |
| defective_goods (grade E) | 83 |
| missing_shipments (lost) | 6 |
| never_shipped (expired) | 13 |
| short_or_empty_receipts | 26 |
| duplicate_refund_attempts_blocked | 14 |
| overpay_attempts_blocked | 4 |
| in_flight_at_snapshot | 39 |

## Key metrics

- Volume: 3,000 orders, 6,406 order lines, 500 return requests, 800 return lines, 4,858 lifecycle events.
- Return lines per 100 order lines: 12.49; units approved 735 of 1,021 requested.
- Refunds: 414 payouts totalling ₹2,051,257.89 (91.58% of amount paid on refunded lines); fees retained ₹50,300.49.
- Blocked attempts: {'REJECTED_DUPLICATE': 14, 'REJECTED_EXCEEDS_PAID': 4} worth ₹76,449.72.
- Cycle time: median request→refund 8.86 days; receipt→inspection 13.89 h; inspection SLA breaches 11.27%.
- Inventory: 381 units back to sellable stock (list value ₹1,325,219.00); disposition mix {'RESTOCK': 381, 'QUARANTINE': 87, 'REFURBISH': 48, 'SCRAP': 103}.
