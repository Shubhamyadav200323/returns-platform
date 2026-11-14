"""Deterministic, immutable business identifiers.

Conventions (from the brief, extended consistently for the other entities):
    ORD-2026-000001          order
    ORD-2026-000001-01       order line  (child of order, 2-digit line no.)
    RMA-2026-000001          return request (the RMA number the customer sees)
    RMAL-RMA-2026-000001-01  return line (child of RMA, 2-digit line no.)
    AUTH-2026-000001         return authorization (1:1 with RMA)
    RTS-2026-000001          return shipment
    INS-2026-000001          inspection
    REF-2026-000001          refund (one row per attempt)
    DSP-2026-000001          inventory disposition
    EVT-2026-0000001         lifecycle event (7 digits: high volume)
    CUST-000001 / SKU-00001  master data
"""
from __future__ import annotations

import re

YEAR = 2026

PATTERNS: dict[str, re.Pattern[str]] = {
    "customer": re.compile(r"^CUST-\d{6}$"),
    "product": re.compile(r"^SKU-\d{5}$"),
    "order": re.compile(r"^ORD-\d{4}-\d{6}$"),
    "order_line": re.compile(r"^ORD-\d{4}-\d{6}-\d{2}$"),
    "return_request": re.compile(r"^RMA-\d{4}-\d{6}$"),
    "return_line": re.compile(r"^RMAL-RMA-\d{4}-\d{6}-\d{2}$"),
    "return_authorization": re.compile(r"^AUTH-\d{4}-\d{6}$"),
    "return_shipment": re.compile(r"^RTS-\d{4}-\d{6}$"),
    "inspection": re.compile(r"^INS-\d{4}-\d{6}$"),
    "refund": re.compile(r"^REF-\d{4}-\d{6}$"),
    "inventory_disposition": re.compile(r"^DSP-\d{4}-\d{6}$"),
    "return_event": re.compile(r"^EVT-\d{4}-\d{7}$"),
}


def customer_id(n: int) -> str:
    return f"CUST-{n:06d}"


def product_id(n: int) -> str:
    return f"SKU-{n:05d}"


def order_id(n: int, year: int = YEAR) -> str:
    return f"ORD-{year}-{n:06d}"


def order_line_id(order: str, line_no: int) -> str:
    return f"{order}-{line_no:02d}"


def rma_id(n: int, year: int = YEAR) -> str:
    return f"RMA-{year}-{n:06d}"


def return_line_id(rma: str, line_no: int) -> str:
    return f"RMAL-{rma}-{line_no:02d}"


def auth_id(n: int, year: int = YEAR) -> str:
    return f"AUTH-{year}-{n:06d}"


def shipment_id(n: int, year: int = YEAR) -> str:
    return f"RTS-{year}-{n:06d}"


def inspection_id(n: int, year: int = YEAR) -> str:
    return f"INS-{year}-{n:06d}"


def refund_id(n: int, year: int = YEAR) -> str:
    return f"REF-{year}-{n:06d}"


def disposition_id(n: int, year: int = YEAR) -> str:
    return f"DSP-{year}-{n:06d}"


def event_id(n: int, year: int = YEAR) -> str:
    return f"EVT-{year}-{n:07d}"


def parse_counter(identifier: str) -> int:
    """Trailing counter of a top-level id (e.g. RMA-2026-000042 -> 42)."""
    return int(identifier.rsplit("-", 1)[1])


class Sequence:
    """Monotonic counter that hands out formatted ids. Never reuses a value."""

    def __init__(self, formatter, start: int = 1):
        self._fmt = formatter
        self._next = start

    def next(self) -> str:
        value = self._fmt(self._next)
        self._next += 1
        return value

    @property
    def issued(self) -> int:
        return self._next - 1
