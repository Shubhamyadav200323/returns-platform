from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from returns_platform import db
from returns_platform.api.app import create_app
from returns_platform.cli import cmd_generate
from returns_platform.config import load_policy
from returns_platform.export.io import load_ground_truth
from returns_platform.validation.checks import run_suite

CLOCK = "2026-10-25T10:00:00"
OP = {"X-Role": "operator", "X-Actor": "OPR-007"}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("RETURNS_CLOCK", CLOCK)
    root = tmp_path_factory.mktemp("api")
    data = root / "data"
    cmd_generate("tiny", 20261022, data, quiet=True)
    url = f"sqlite:///{root / 'api.db'}"
    db.load_dataset(url, data)
    client = TestClient(create_app(url))
    yield client, url, data
    mp.undo()


def find_line(url, category, max_days=None, min_days=None, min_qty=1):
    """An order line nobody has returned yet, in a category, delivered within a day range of CLOCK."""
    today = date.fromisoformat(CLOCK[:10])
    eng = db.make_engine(url)
    with eng.connect() as c:
        returned = {r["order_line_id"] for r in db.select(c, "return_line")}
        for l in db.select(c, "order_line", "category = :c AND quantity >= :q", {"c": category, "q": min_qty}):
            if l["order_line_id"] in returned:
                continue
            o = db.get(c, "sales_order", l["order_id"])
            days = (today - o["delivered_date"]).days
            if (max_days is None or days <= max_days) and (min_days is None or days >= min_days):
                return l, o
    pytest.skip(f"no suitable {category} line in tiny dataset")


def create(client, line, qty=1, reason="SIZE_FIT", rtype="REFUND", headers=None, override=None):
    body = {"order_id": line["order_id"], "request_type": rtype,
            "lines": [{"order_line_id": line["order_line_id"], "quantity": qty, "reason_code": reason}]}
    if override:
        body["override"] = override
    return client.post("/api/returns", json=body, headers=headers or {"X-Role": "customer"})


def test_full_refund_lifecycle_and_guards(env):
    client, url, _ = env
    line, order = find_line(url, "APPAREL", max_days=30)
    r = create(client, line, reason="CHANGED_MIND")
    assert r.status_code == 201, r.text
    rma = r.json()
    assert rma["status"] == "AUTHORIZED" and rma["lines"][0]["decision"] == "APPROVED"
    rts, rl = rma["shipment"]["rts_id"], rma["lines"][0]["return_line_id"]

    # guards before the parcel arrives
    assert client.post(f"/api/return-lines/{rl}/inspection", json={"received_qty": 1, "grade": "A_NEW"}, headers=OP).json()["error"] == "NOT_RECEIVED"
    assert client.post(f"/api/return-lines/{rl}/disposition", headers=OP).json()["error"] == "NOT_INSPECTED"
    assert client.post(f"/api/return-lines/{rl}/refunds", headers=OP | {"Idempotency-Key": "x"}).json()["error"] == "NOT_INSPECTED"
    assert client.post(f"/api/shipments/{rts}/receive", headers=OP).json()["error"] == "ILLEGAL_TRANSITION"
    assert client.post(f"/api/shipments/{rts}/receive", headers={"X-Role": "customer"}).status_code == 403

    assert client.post(f"/api/shipments/{rts}/ship", headers={"X-Role": "customer"}).json()["status"] == "IN_TRANSIT"
    assert client.post(f"/api/shipments/{rts}/receive", headers=OP).json()["status"] == "RECEIVED"
    r = client.post(f"/api/return-lines/{rl}/inspection", json={"received_qty": 1, "grade": "B_LIKE_NEW"}, headers=OP)
    assert r.json()["status"] == "INSPECTED"
    assert client.post(f"/api/return-lines/{rl}/inspection", json={"received_qty": 1, "grade": "A_NEW"}, headers=OP).status_code == 409

    # refund: policy-computed, idempotent, duplicates blocked and recorded
    assert client.post(f"/api/return-lines/{rl}/refunds", json={"amount_paise": 1}, headers=OP | {"Idempotency-Key": "k0"}).json()["error"] == "AMOUNT_MISMATCH"
    first = client.post(f"/api/return-lines/{rl}/refunds", headers=OP | {"Idempotency-Key": "k1"})
    assert first.status_code == 201 and first.json()["status"] == "SUCCEEDED"
    paid = first.json()
    assert paid["shipping_fee_paise"] == load_policy().return_shipping_fee_paise      # customer fault
    assert paid["amount_paise"] <= line["line_paid_paise"]
    replay = client.post(f"/api/return-lines/{rl}/refunds", headers=OP | {"Idempotency-Key": "k1"})
    assert replay.status_code == 200 and replay.json()["refund_id"] == paid["refund_id"]
    dup = client.post(f"/api/return-lines/{rl}/refunds", headers=OP | {"Idempotency-Key": "k2"})
    assert dup.status_code == 409 and dup.json()["status"] == "REJECTED_DUPLICATE" and dup.json()["amount_paise"] == 0

    done = client.post(f"/api/return-lines/{rl}/disposition", headers=OP).json()
    assert done["status"] == "COMPLETED"
    assert done["lines"][0]["disposition"]["disposition"] == "RESTOCK"
    assert done["totals"]["refunded_paise"] == paid["amount_paise"] and done["totals"]["blocked_attempts"] == 1
    states = [e["to_state"] for e in done["events"] if e["entity_type"] == "RETURN_REQUEST"]
    assert states == ["REQUESTED", "AUTHORIZED", "IN_TRANSIT", "RECEIVED", "INSPECTED", "COMPLETED"]


def test_category_exclusion(env):
    client, url, _ = env
    line, _ = find_line(url, "BEAUTY")
    rma = create(client, line, reason="CHANGED_MIND").json()
    assert rma["status"] == "REJECTED" and rma["lines"][0]["rejection_reason"] == "CATEGORY_EXCLUDED"
    assert rma["shipment"] is None


def test_late_request_rejected_then_operator_override(env):
    client, url, _ = env
    line, _ = find_line(url, "ELECTRONICS", min_days=11)
    rma = create(client, line, reason="DEFECTIVE").json()
    assert rma["lines"][0]["rejection_reason"] == "WINDOW_EXPIRED"
    ovr = {"reason": "Courier delay verified", "by": "OPR-007"}
    assert create(client, line, reason="DEFECTIVE", override=ovr).status_code == 403    # customers can't override
    rma2 = create(client, line, reason="DEFECTIVE", override=ovr, headers=OP).json()
    assert rma2["status"] == "AUTHORIZED" and rma2["override_flag"] and rma2["authorization"]["decided_by"] == "OPR-007"


def test_over_request_clamped_and_cumulative(env):
    client, url, _ = env
    line, _ = find_line(url, "APPAREL", max_days=30)
    q = line["quantity"]
    rma = create(client, line, qty=q + 2, reason="SIZE_FIT").json()
    l = rma["lines"][0]
    assert (l["decision"], l["approved_qty"], l["rejection_reason"]) == ("PARTIALLY_APPROVED", q, "QTY_EXCEEDS_RETURNABLE")
    again = create(client, line, qty=1, reason="SIZE_FIT").json()
    assert again["lines"][0]["rejection_reason"] == "NOTHING_RETURNABLE"


def test_bad_input_rejected(env):
    client, url, _ = env
    line, _ = find_line(url, "TOYS")
    assert create(client, line, reason="BORED").json()["error"] == "BAD_REASON"
    assert client.post("/api/returns", json={"order_id": "ORD-2026-999999", "lines": [
        {"order_line_id": "x", "quantity": 1, "reason_code": "DEFECTIVE"}]}).status_code == 404
    assert client.get("/api/metrics", headers={"X-Role": "customer"}).status_code == 403


def test_api_writes_keep_dataset_valid(env):
    """After all the API activity above, the full validation suite still passes on the live DB:
    integrity checks clean and detectors flag only the originally labelled anomalies."""
    client, url, data = env
    eng = db.make_engine(url)
    with eng.connect() as c:
        T = db.load_all(c)
    gt, _ = load_ground_truth(data)
    bad = [(r.name, r.examples) for r in run_suite(T, load_policy(), gt).results if not r.passed]
    assert not bad, bad
    m = client.get("/api/metrics", headers={"X-Role": "analyst"}).json()
    assert m["volume"]["return_requests"] >= 80
    assert "blocked_refund_attempts" in client.get("/api/exceptions", headers={"X-Role": "manager"}).json()
