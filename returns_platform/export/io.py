"""Serialisation of datasets to CSV / JSON / SQL seed, and typed loading back from CSV."""
from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from returns_platform.schema.ddl import schema_ddl
from returns_platform.schema.spec import LOAD_ORDER, TABLES, Col

TS_FMT = "%Y-%m-%d %H:%M:%S"

ANOMALY_COLUMNS = ("anomaly_id", "issue_type", "table_name", "record_id", "field_name", "original_value",
                   "injected_value", "related_ids", "description", "detection_rule")
EXPECTED_COLUMNS = ("return_line_id", "rma_id", "expected_decision", "expected_approved_qty",
                    "expected_rejection_reason", "expected_resolution", "expected_refund_paise",
                    "expected_disposition")


def to_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, datetime):
        return v.strftime(TS_FMT)
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def to_json_value(v):
    if isinstance(v, (datetime, date)):
        return to_text(v)
    return v


def parse(col: Col, raw: str):
    if raw == "":
        return None
    t = col.type
    if t in ("int", "money", "bp"):
        return int(raw)
    if t == "bool":
        if raw not in ("true", "false"):
            raise ValueError(f"{col.name}: bad boolean {raw!r}")
        return raw == "true"
    if t == "date":
        return date.fromisoformat(raw)
    if t == "ts":
        return datetime.strptime(raw, TS_FMT)
    return raw


def sql_literal(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    return "'" + to_text(v).replace("'", "''") + "'"


# ---------------------------------------------------------------- writers
def write_csv(path: Path, columns, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([to_text(r.get(c)) for c in columns])


def write_json(path: Path, columns, rows) -> None:
    data = [{c: to_json_value(r.get(c)) for c in columns} for r in rows]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0, separators=(",", ":")) + "\n", encoding="utf-8")


def write_sql_seed(path: Path, tables: dict[str, list[dict]], dialect: str, batch: int = 500) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"-- Seed data ({dialect}). Generated — load after schema_{dialect}.sql.\n")
        fh.write("BEGIN;\n")
        for name in LOAD_ORDER:
            cols = [c.name for c in TABLES[name].cols]
            rows = tables.get(name, [])
            # self-referencing FK (refund.duplicate_of_refund_id): parents first
            if name == "refund":
                rows = sorted(rows, key=lambda r: (r.get("duplicate_of_refund_id") is not None, r["refund_id"]))
            for i in range(0, len(rows), batch):
                chunk = rows[i:i + batch]
                values = ",\n".join("(" + ", ".join(sql_literal(r.get(c)) for c in cols) + ")" for r in chunk)
                fh.write(f"INSERT INTO {name} ({', '.join(cols)}) VALUES\n{values};\n")
        fh.write("COMMIT;\n")


def export_dataset(ds, out_dir: Path) -> list[Path]:
    """Write all artefacts; returns the list of files written (for the manifest)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "csv").mkdir(exist_ok=True)
    (out_dir / "json").mkdir(exist_ok=True)
    (out_dir / "sql").mkdir(exist_ok=True)
    (out_dir / "ground_truth").mkdir(exist_ok=True)
    written = []
    for name in LOAD_ORDER:
        cols = [c.name for c in TABLES[name].cols]
        rows = ds.tables.get(name, [])
        for kind, fn in (("csv", write_csv), ("json", write_json)):
            p = out_dir / kind / f"{name}.{kind}"
            fn(p, cols, rows)
            written.append(p)
    for dialect in ("sqlite", "postgres"):
        p = out_dir / "sql" / f"schema_{dialect}.sql"
        p.write_text(schema_ddl(dialect), encoding="utf-8")
        written.append(p)
        p = out_dir / "sql" / f"seed_{dialect}.sql"
        write_sql_seed(p, ds.tables, dialect)
        written.append(p)
    gt = out_dir / "ground_truth"
    for stem, cols, rows in (("anomalies", ANOMALY_COLUMNS, ds.anomalies),
                             ("expected_outcomes", EXPECTED_COLUMNS, ds.expected)):
        write_csv(gt / f"{stem}.csv", cols, rows)
        write_json(gt / f"{stem}.json", cols, rows)
        written += [gt / f"{stem}.csv", gt / f"{stem}.json"]
    return written


# ---------------------------------------------------------------- loaders
def load_tables(data_dir: Path) -> tuple[dict[str, list[dict]], list[str]]:
    """Load CSVs back with types from the spec. Returns (tables, parse_errors)."""
    tables, errors = {}, []
    for name in LOAD_ORDER:
        spec = TABLES[name]
        cols = {c.name: c for c in spec.cols}
        path = data_dir / "csv" / f"{name}.csv"
        rows = []
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if list(reader.fieldnames or []) != list(cols):
                errors.append(f"{name}: header mismatch")
            for i, raw in enumerate(reader, 2):
                row = {}
                for k, v in raw.items():
                    try:
                        row[k] = parse(cols[k], v)
                    except (ValueError, KeyError) as exc:
                        errors.append(f"{name} line {i}: {exc}")
                        row[k] = None
                rows.append(row)
        tables[name] = rows
    return tables, errors


def load_ground_truth(data_dir: Path) -> tuple[list[dict], list[dict]]:
    def read(stem):
        with (data_dir / "ground_truth" / f"{stem}.csv").open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    return read("anomalies"), read("expected_outcomes")
