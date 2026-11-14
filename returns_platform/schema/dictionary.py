"""Render the data dictionary (markdown + machine-readable JSON) from the spec."""
from __future__ import annotations

from returns_platform.schema.spec import SCHEMA, TYPES

CARDINALITY = """\
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
"""


def to_markdown() -> str:
    out = [
        "# Data dictionary",
        "",
        "_Generated from `returns_platform/schema/spec.py`. Do not edit by hand — run `make docs`._",
        "",
        "Conventions: money is integer **paise** (₹1 = 100 paise) to keep arithmetic exact; "
        "rates are **basis points** (10000 = 100%); timestamps are UTC `YYYY-MM-DD HH:MM:SS`; "
        "booleans are `true`/`false` in CSV/JSON.",
        "",
        "## Relationships and cardinality",
        "",
        CARDINALITY,
    ]
    for t in SCHEMA:
        out += [f"## `{t.name}` — {t.entity}", "", t.desc, ""]
        if t.lifecycle:
            out += [f"**Lifecycle:** {t.lifecycle}", ""]
        out += [
            "| Field | Type | Req | Key | Allowed values / range | Unit | Unique | Validation / derivation |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in t.cols:
            key = "PK" if c.pk else (f"FK → `{c.fk}`" if c.fk else "")
            allowed = ", ".join(c.allowed) if c.allowed else ""
            if c.min is not None or c.max is not None:
                rng = f"{'' if c.min is None else c.min}..{'' if c.max is None else c.max}"
                allowed = f"{allowed} {rng}".strip()
            out.append(
                f"| `{c.name}` | {c.type} ({TYPES[c.type][1]}) | {'Y' if c.required else 'N'} | {key} | "
                f"{allowed} | {c.unit or ''} | {'Y' if c.unique else ''} | {c.desc}. {c.rule or ''} |"
            )
        extras = []
        for cols in t.unique_together:
            extras.append(f"- Unique together: `({', '.join(cols)})`")
        for name, expr in t.checks:
            extras.append(f"- Check `{name}`: `{expr}`")
        if extras:
            out += [""] + extras
        out.append("")
    return "\n".join(out)


def to_json() -> list[dict]:
    return [
        {
            "table": t.name,
            "entity": t.entity,
            "description": t.desc,
            "lifecycle": t.lifecycle,
            "unique_together": [list(u) for u in t.unique_together],
            "checks": [{"name": n, "expr": e} for n, e in t.checks],
            "fields": [
                {
                    "name": c.name, "type": c.type, "required": c.required, "primary_key": c.pk,
                    "foreign_key": c.fk, "unique": c.unique,
                    "allowed": list(c.allowed) if c.allowed else None,
                    "min": c.min, "max": c.max, "unit": c.unit, "rule": c.rule, "description": c.desc,
                }
                for c in t.cols
            ],
        }
        for t in SCHEMA
    ]
