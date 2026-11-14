"""Render the schema spec as SQL DDL for SQLite or PostgreSQL."""
from __future__ import annotations

from returns_platform.schema.spec import SCHEMA, TYPES, Col, Table

DIALECTS = ("sqlite", "postgres")


def _col_sql(c: Col, dialect: str) -> str:
    sql_type = TYPES[c.type][DIALECTS.index(dialect)]
    parts = [f"{c.name} {sql_type}"]
    if c.pk:
        parts.append("PRIMARY KEY")
    elif c.required:
        parts.append("NOT NULL")
    if c.unique and not c.pk:
        parts.append("UNIQUE")
    if c.fk:
        table, col = c.fk.split(".")
        parts.append(f"REFERENCES {table}({col})")
    checks = []
    if c.allowed:
        vals = ", ".join(f"'{v}'" for v in c.allowed)
        checks.append(f"{c.name} IN ({vals})")
    if c.min is not None:
        checks.append(f"{c.name} >= {c.min}")
    if c.max is not None:
        checks.append(f"{c.name} <= {c.max}")
    if checks:
        parts.append(f"CHECK ({' AND '.join(checks)})")
    return " ".join(parts)


def table_ddl(t: Table, dialect: str = "sqlite") -> str:
    lines = [_col_sql(c, dialect) for c in t.cols]
    for cols in t.unique_together:
        lines.append(f"CONSTRAINT uq_{t.name}_{'_'.join(cols)} UNIQUE ({', '.join(cols)})")
    for name, expr in t.checks:
        lines.append(f"CONSTRAINT {name} CHECK ({expr})")
    body = ",\n    ".join(lines)
    return f"CREATE TABLE {t.name} (\n    {body}\n);"


def index_ddl(t: Table) -> list[str]:
    out = []
    for c in t.cols:
        if c.fk and not c.unique:
            out.append(f"CREATE INDEX ix_{t.name}_{c.name} ON {t.name}({c.name});")
    return out


def schema_ddl(dialect: str = "sqlite") -> str:
    if dialect not in DIALECTS:
        raise ValueError(dialect)
    header = [
        "-- Returns platform schema. GENERATED from returns_platform/schema/spec.py — do not edit by hand.",
        f"-- Dialect: {dialect}. Money in paise (INR minor units); timestamps UTC.",
        "",
    ]
    blocks = []
    for t in SCHEMA:
        blocks.append(f"-- {t.entity}: {t.desc}\n" + table_ddl(t, dialect))
        blocks.extend(index_ddl(t))
    return "\n".join(header) + "\n\n".join(blocks) + "\n"


def drop_ddl() -> str:
    return "\n".join(f"DROP TABLE IF EXISTS {t.name};" for t in reversed(SCHEMA)) + "\n"
