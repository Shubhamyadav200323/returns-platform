"""Data-tier access. Portable across SQLite (zero-setup default) and PostgreSQL.

Only this module talks SQL; services above it work with plain dicts typed by the schema spec.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

from returns_platform.export.io import TS_FMT, load_tables
from returns_platform.schema.ddl import drop_ddl, schema_ddl
from returns_platform.schema.spec import LOAD_ORDER, TABLES


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):                 # SQLite ignores FKs unless asked
            dbapi_conn.execute("PRAGMA foreign_keys = ON")
    return engine


def dialect(conn_or_engine) -> str:
    name = conn_or_engine.dialect.name
    return "postgres" if name.startswith("postgres") else "sqlite"


def create_schema(engine: Engine, drop: bool = True) -> None:
    ddl = (drop_ddl() if drop else "") + schema_ddl(dialect(engine))
    ddl = "\n".join(line for line in ddl.splitlines() if not line.lstrip().startswith("--"))
    with engine.begin() as conn:
        for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
            conn.exec_driver_sql(stmt)


# ---------------------------------------------------------------- type mapping
def to_db(table: str, row: dict) -> dict:
    out = {}
    for c in TABLES[table].cols:
        v = row.get(c.name)
        if isinstance(v, datetime):
            v = v.strftime(TS_FMT)
        elif isinstance(v, date):
            v = v.isoformat()
        out[c.name] = v
    return out


def from_db(table: str, row) -> dict:
    out = {}
    m = row._mapping if hasattr(row, "_mapping") else row
    for c in TABLES[table].cols:
        v = m[c.name]
        if v is not None:
            if c.type == "bool":
                v = bool(v)
            elif c.type == "date" and isinstance(v, str):
                v = date.fromisoformat(v)
            elif c.type == "ts" and isinstance(v, str):
                v = datetime.strptime(v[:19], TS_FMT)
        out[c.name] = v
    return out


# ---------------------------------------------------------------- generic repository
def insert(conn: Connection, table: str, row: dict) -> None:
    cols = [c.name for c in TABLES[table].cols]
    conn.execute(text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})"),
                 to_db(table, row))


def update(conn: Connection, table: str, pk_value: str, **fields) -> None:
    pk = TABLES[table].pk.name
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    params = to_db(table, {**fields}) | {"_pk": pk_value}
    params = {k: params[k] for k in fields} | {"_pk": pk_value}
    conn.execute(text(f"UPDATE {table} SET {sets} WHERE {pk} = :_pk"), params)


def get(conn: Connection, table: str, pk_value: str, for_update: bool = False) -> dict | None:
    pk = TABLES[table].pk.name
    lock = " FOR UPDATE" if for_update and dialect(conn) == "postgres" else ""
    row = conn.execute(text(f"SELECT * FROM {table} WHERE {pk} = :v{lock}"), {"v": pk_value}).first()
    return from_db(table, row) if row else None


def select(conn: Connection, table: str, where: str = "", params: dict | None = None,
           order_by: str | None = None, limit: int | None = None, offset: int = 0) -> list[dict]:
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order_by or TABLES[table].pk.name}"
    if limit is not None:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return [from_db(table, r) for r in conn.execute(text(sql), params or {})]


def next_counter(conn: Connection, table: str) -> int:
    pk = TABLES[table].pk.name
    top = conn.execute(text(f"SELECT MAX({pk}) FROM {table}")).scalar()
    return int(top.rsplit("-", 1)[1]) + 1 if top else 1


def load_all(conn: Connection) -> dict[str, list[dict]]:
    return {t: select(conn, t) for t in LOAD_ORDER}


def load_dataset(url: str, data_dir: Path) -> int:
    """Create a fresh schema and bulk-load an exported dataset (parents before children)."""
    engine = make_engine(url)
    create_schema(engine)
    tables, errors = load_tables(Path(data_dir))
    if errors:
        raise ValueError(f"dataset has parse errors: {errors[:3]}")
    total = 0
    with engine.begin() as conn:
        for name in LOAD_ORDER:
            rows = tables[name]
            if name == "refund":   # self-reference: originals before the duplicates that point at them
                rows = sorted(rows, key=lambda r: (r["duplicate_of_refund_id"] is not None, r["refund_id"]))
            if not rows:
                continue
            cols = [c.name for c in TABLES[name].cols]
            stmt = text(f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})")
            conn.execute(stmt, [to_db(name, r) for r in rows])
            total += len(rows)
    return total
