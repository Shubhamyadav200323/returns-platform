import copy
import csv
from dataclasses import replace
from pathlib import Path

import pytest

from returns_platform.cli import cmd_generate, cmd_validate
from returns_platform.config import load_policy
from returns_platform.export.io import load_ground_truth, load_tables
from returns_platform.generator.pipeline import generate
from returns_platform.generator.settings import settings_for
from returns_platform.schema.spec import SCHEMA
from returns_platform.validation.checks import run_suite

POLICY = load_policy()


@pytest.fixture(scope="module")
def tiny_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("tiny")
    cmd_generate("tiny", 20261022, out, quiet=True)
    return out


def test_exact_volumes():
    s = settings_for("starter")
    ds = generate(s, POLICY)
    assert len(ds.tables["sales_order"]) == s.n_orders
    assert len(ds.tables["return_request"]) == s.n_requests
    assert len(ds.tables["return_line"]) == s.n_return_lines
    assert len(ds.anomalies) == 5 * s.anomalies_per_type


def test_generated_dataset_validates(tiny_dir):
    summary = cmd_validate(tiny_dir, quiet=True)
    assert summary["overall_status"] == "PASS", [c for c in summary["checks"] if c["status"] != "PASS"]
    ad = summary["anomaly_detection"]
    assert ad["precision"] == 1.0 and ad["recall"] == 1.0
    assert summary["decision_agreement_with_expected_outcomes"]["pct"] == 100.0


def test_same_seed_identical_outputs(tmp_path):
    a = cmd_generate("tiny", 7, tmp_path / "a", quiet=True)
    b = cmd_generate("tiny", 7, tmp_path / "b", quiet=True)
    assert [(f["path"], f["sha256"]) for f in a["files"]] == [(f["path"], f["sha256"]) for f in b["files"]]


def test_different_seed_differs():
    a = generate(settings_for("tiny", 1), POLICY)
    b = generate(settings_for("tiny", 2), POLICY)
    assert a.tables["sales_order"] != b.tables["sales_order"]


def test_baseline_independent_of_anomaly_volume():
    s = settings_for("tiny")
    clean = generate(s, POLICY, inject_anomalies=False)
    more = generate(replace(s, anomalies_per_type=1), POLICY)
    for t in ("customer", "product", "order_line", "inspection"):
        assert clean.tables[t] == more.tables[t]


def test_ground_truth_not_leaked_into_inputs(tiny_dir):
    gt_cols = {"issue_type", "anomaly_id", "ground_truth", "expected_decision", "is_anomaly"}
    for t in SCHEMA:
        with (tiny_dir / "csv" / f"{t.name}.csv").open() as fh:
            header = set(next(csv.reader(fh)))
        assert not header & gt_cols, t.name


def test_derived_values_not_independent(tiny_dir):
    """Order totals are sums of lines; RMA status is the last event — derived, never sampled."""
    T, _ = load_tables(tiny_dir)
    by_order = {}
    for l in T["order_line"]:
        by_order[l["order_id"]] = by_order.get(l["order_id"], 0) + l["line_paid_paise"]
    for o in T["sales_order"]:
        assert o["total_paid_paise"] == by_order[o["order_id"]] + o["shipping_fee_paise"]


# ----------------------------------------------------------- mutation tests: the suite must notice
def _suite(T, gt):
    return {r.name: r for r in run_suite(T, POLICY, gt).results}


def _first(rows, pred):
    return next(r for r in rows if pred(r))


@pytest.fixture(scope="module")
def loaded(tiny_dir):
    T, _ = load_tables(tiny_dir)
    gt, _ = load_ground_truth(tiny_dir)
    labelled = {a["record_id"] for a in gt} | {x for a in gt for x in a["related_ids"].split("|")}
    return T, gt, labelled


MUTATIONS = {
    "refund_amount_tampered": ("policy.refund_reexecution",
        lambda T, L: _first(T["refund"], lambda r: r["status"] == "SUCCEEDED" and r["refund_id"] not in L).update(
            amount_paise=0, requested_amount_paise=0, item_amount_paise=0, tax_amount_paise=0, restocking_fee_paise=0, shipping_fee_paise=0)),
    "disposition_ignores_grade": ("inventory_disposition.controls_inventory",
        lambda T, L: _first(T["inventory_disposition"], lambda d: d["disposition"] == "SCRAP").update(
            disposition="RESTOCK", to_location="SELLABLE", sellable_qty_delta=1)),
    "status_not_from_events": ("lifecycle.event_paths",
        lambda T, L: _first(T["return_request"], lambda r: r["status"] == "REJECTED").update(status="COMPLETED")),
    "duplicate_primary_key": ("customer.pk_unique",
        lambda T, L: T["customer"].append(dict(T["customer"][0]))),
    "bad_enum": ("return_line.required_enum_range",
        lambda T, L: T["return_line"][0].update(reason_code="BORED")),
    "refund_before_inspection": ("dates.ordering",
        lambda T, L: (lambda r: r.update(requested_at=r["requested_at"].replace(year=2025)))(
            _first(T["refund"], lambda r: r["refund_id"] not in L))),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation_is_detected(loaded, name):
    T, gt, labelled = loaded
    check, mutate = MUTATIONS[name]
    T2 = copy.deepcopy(T)
    mutate(T2, labelled)
    res = _suite(T2, gt)
    assert not res[check].passed, f"{check} missed mutation {name}"


def test_unmutated_suite_passes(loaded):
    T, gt, _ = loaded
    assert all(r.passed for r in _suite(copy.deepcopy(T), gt).values())


def test_outputs_are_byte_identical_across_operating_systems(tiny_dir):
    """Every generated text file uses LF only, so checksums match on Windows, macOS and Linux."""
    files = [p for p in tiny_dir.rglob("*") if p.is_file()]
    assert files
    for p in files:
        assert b"\r" not in p.read_bytes(), f"{p.name} contains CR line endings"
