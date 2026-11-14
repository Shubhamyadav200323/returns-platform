"""Command line entry point.

    python -m returns_platform generate --scale full --seed 20261022 --out out/full
    python -m returns_platform validate --data out/full
    python -m returns_platform reproduce --scale full --seed 20261022
    python -m returns_platform load-db --data out/full --url sqlite:///out/returns.db
    python -m returns_platform docs
    python -m returns_platform serve --url sqlite:///out/returns.db
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from returns_platform.config import ROOT, load_policy
from returns_platform.export.io import export_dataset, load_ground_truth, load_tables
from returns_platform.generator.pipeline import generate
from returns_platform.generator.settings import DEFAULT_SEED, SCALES, settings_for
from returns_platform.validation.checks import run_suite
from returns_platform.validation.report import build_manifest, build_summary, write_reports


def cmd_generate(scale: str, seed: int, out: Path, quiet=False) -> dict:
    t0 = time.time()
    s = settings_for(scale, seed)
    policy = load_policy()
    ds = generate(s, policy)
    if out.exists():
        shutil.rmtree(out)
    files = export_dataset(ds, out)
    command = f"python -m returns_platform generate --scale {scale} --seed {seed} --out {out.as_posix()}"
    manifest = build_manifest(out, files, s, command)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    summary = cmd_validate(out, quiet=True, manifest=manifest)
    if not quiet:
        ad = summary["anomaly_detection"]
        print(f"generated {scale} dataset (seed {seed}) in {time.time() - t0:.1f}s -> {out}")
        for f in manifest["files"]:
            if f["path"].startswith("csv/"):
                print(f"  {f['path']:<36}{f['rows']:>9,} rows")
        print(f"validation: {summary['overall_status']} ({summary['checks_passed']}/{summary['checks_total']} checks); "
              f"anomaly detection precision {ad['precision']} recall {ad['recall']}")
    return manifest


def cmd_validate(data: Path, quiet=False, manifest=None) -> dict:
    policy = load_policy()
    tables, errors = load_tables(data)
    anomalies, expected = load_ground_truth(data)
    suite = run_suite(tables, policy, anomalies)
    summary = build_summary(suite, tables, anomalies, expected, errors, policy)
    if manifest is None:
        manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    write_reports(data, summary, manifest)
    if not quiet:
        for r in summary["checks"]:
            mark = "PASS" if r["status"] == "PASS" else "FAIL"
            print(f"  [{mark}] {r['name']:<48} flagged={r['flagged']:<5} expected={r['expected']}")
            if mark == "FAIL":
                for ex in r["examples"]:
                    print(f"         e.g. {ex}")
        print(f"overall: {summary['overall_status']}")
    return summary


def cmd_reproduce(scale: str, seed: int, report_path: Path | None) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        runs = []
        for i in (1, 2):
            out = Path(tmp) / f"run{i}"
            m = cmd_generate(scale, seed, out, quiet=True)
            runs.append({f["path"]: (f["rows"], f["sha256"]) for f in m["files"]})
    same = runs[0] == runs[1]
    lines = [f"# Reproducibility check — scale `{scale}`, seed `{seed}`", "",
             f"Generated twice in clean directories. **Result: {'IDENTICAL' if same else 'DIFFERENT'}** "
             f"({len(runs[0])} files compared by row count and SHA-256).", "",
             "| File | Rows | SHA-256 (run 1) | Match |", "|---|---|---|---|"]
    for path in sorted(runs[0]):
        rows, h = runs[0][path]
        lines.append(f"| `{path}` | {'' if rows is None else f'{rows:,}'} | `{h[:16]}…` | {'✔' if runs[1].get(path) == (rows, h) else '✘'} |")
    text = "\n".join(lines) + "\n"
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text, encoding="utf-8")
    print(text if len(runs[0]) < 60 else f"reproducibility: {'IDENTICAL' if same else 'DIFFERENT'}")
    return same


def cmd_docs() -> None:
    from returns_platform.schema.ddl import schema_ddl
    from returns_platform.schema.dictionary import to_json, to_markdown
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "data_dictionary.md").write_text(to_markdown(), encoding="utf-8")
    (docs / "data_dictionary.json").write_text(json.dumps(to_json(), indent=2) + "\n", encoding="utf-8")
    for d in ("sqlite", "postgres"):
        (ROOT / "db").mkdir(exist_ok=True)
        (ROOT / "db" / f"schema_{d}.sql").write_text(schema_ddl(d), encoding="utf-8")
    print("wrote docs/data_dictionary.{md,json} and db/schema_{sqlite,postgres}.sql")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="returns_platform")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="generate, export and validate a dataset")
    g.add_argument("--scale", choices=sorted(SCALES), default="starter")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--out", type=Path)
    v = sub.add_parser("validate", help="validate an exported dataset against ground truth")
    v.add_argument("--data", type=Path, required=True)
    r = sub.add_parser("reproduce", help="generate twice and compare checksums")
    r.add_argument("--scale", choices=sorted(SCALES), default="starter")
    r.add_argument("--seed", type=int, default=DEFAULT_SEED)
    r.add_argument("--report", type=Path)
    l = sub.add_parser("load-db", help="create schema and load a dataset into a database")
    l.add_argument("--data", type=Path, required=True)
    l.add_argument("--url", default="sqlite:///out/returns.db")
    sv = sub.add_parser("serve", help="run the API + web UI")
    sv.add_argument("--url", default="sqlite:///out/returns.db")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sub.add_parser("docs", help="regenerate data dictionary and DDL files")
    a = ap.parse_args(argv)

    if a.cmd == "generate":
        cmd_generate(a.scale, a.seed, a.out or Path("out") / a.scale)
    elif a.cmd == "validate":
        return 0 if cmd_validate(a.data)["overall_status"] == "PASS" else 1
    elif a.cmd == "reproduce":
        return 0 if cmd_reproduce(a.scale, a.seed, a.report) else 1
    elif a.cmd == "load-db":
        from returns_platform.db import load_dataset
        n = load_dataset(a.url, a.data)
        print(f"loaded {n:,} rows into {a.url}")
    elif a.cmd == "serve":
        import uvicorn
        from returns_platform.api.app import create_app
        uvicorn.run(create_app(a.url), host=a.host, port=a.port)
    elif a.cmd == "docs":
        cmd_docs()
    return 0


if __name__ == "__main__":
    sys.exit(main())
