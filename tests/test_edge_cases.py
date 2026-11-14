"""Edge cases beyond the five the brief lists: money reconciliation across partial returns,
partial and empty receipts, expiry, lost parcels, mixed-eligibility RMAs, key reuse,
concurrency, and generator robustness across seeds."""
import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient

from returns_platform import db
from returns_platform.api.app import create_app
from returns_platform.cli import cmd_generate, cmd_validate
from returns_platform.config import load_policy

CLOCK = "2026-10-25T10:00:00"
OP = {"X-Role": "operator", "X-Actor": "OPR-009"}
POLICY = load_policy()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("RETURNS_CLOCK", CLOCK)
    root = tmp_path_factory.mktemp("edge")
    cmd_generate("starter", 20261022, root / "data", quiet=True)
    url = f"sqlite:///{root / 'edge.db'}"
    db.load_dataset(url, root / "data")
    app = create_app(url)
    yield TestClient(app), url, app
    mp.undo()


@pytest.fixture
def clock(monkeypatch):
    def set_(iso):
        monkeypatch.setenv("RETURNS_CLOCK", iso)
    yield set_
    monkeypatch.setenv("RETURNS_CLOCK", CLOCK)


_taken: set[str] = set()


def find_order(url, pred, min_days=0, max_days=10**6):
    """An order (unreturned, not used by another test) whose lines satisfy pred(lines)."""
    today = date.fromisoformat(CLOCK[:10])
    with db.make_engine(url).connect() as c:
        returned = {r["order_line_id"] for r in db.select(c, "return_line")}
        by_order = {}
        for l in db.select(c, "order_line"):
            by_order.setdefault(l["order_id"], []).append(l)
        for o in db.select(c, "sales_order", order_by="order_id DESC"):
            lines = by_order[o["order_id"]]
            days = (today - o["delivered_date"]).days
            if o["order_id"] in _taken or any(l["order_line_id"] in returned for l in lines):
                continue
            if min_days <= days <= max_days and pred(lines):
                _taken.add(o["order_id"])
                return o, lines
    pytest.skip("no order matches")


def create(client, order, items, rtype="REFUND", headers=None):
    body = {"order_id": order["order_id"], "request_type": rtype,
            "lines": [{"order_line_id": l["order_line_id"], "quantity": q, "reason_code": r} for l, q, r in items]}
    r = client.post("/api/returns", json=body, headers=headers or {"X-Role": "customer"})
    assert r.status_code == 201, r.text
    return r.json()


def to_received(client, rma):
    rts = rma["shipment"]["rts_id"]
    assert client.post(f"/api/shipments/{rts}/ship").status_code == 200
    r = client.post(f"/api/shipments/{rts}/receive", headers=OP)
    assert r.status_code == 200, r.text
    return r.json()


def inspect(client, line_id, qty, grade="A_NEW"):
    r = client.post(f"/api/return-lines/{line_id}/inspection", json={"received_qty": qty, "grade": grade}, headers=OP)
    assert r.status_code == 200, r.text
    return r.json()


def refund(client, line_id, key):
    return client.post(f"/api/return-lines/{line_id}/refunds", headers=OP | {"Idempotency-Key": key})


# --------------------------------------------------------------------------- money
def test_split_returns_across_rmas_reconcile_to_the_paisa(env, clock):
    """Return a 3-unit line as 1 + 2 on two RMAs (merchant fault, no fees): total refunded must equal
    exactly what was paid for the line — no paisa lost or gained to rounding."""
    client, url, _ = env
    order, lines = find_order(url, lambda ls: any(l["quantity"] >= 3 and l["category"] in ("APPAREL", "FOOTWEAR", "TOYS") for l in ls), max_days=14)
    line = next(l for l in lines if l["quantity"] >= 3 and l["category"] in ("APPAREL", "FOOTWEAR", "TOYS"))
    paid = 0
    for n, qty in enumerate((1, line["quantity"] - 1)):
        rma = to_received(client, create(client, order, [(line, qty, "DEFECTIVE")]))
        rl = rma["lines"][0]["return_line_id"]
        inspect(client, rl, qty, "E_DEFECTIVE")
        r = refund(client, rl, f"split-{n}")
        assert r.status_code == 201, r.text
        paid += r.json()["amount_paise"]
    assert paid == line["line_paid_paise"]


def test_shipping_fee_once_per_rma_and_never_on_merchant_fault(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: sum(l["category"] in ("APPAREL", "FOOTWEAR") for l in ls) >= 3, max_days=25)
    soft = [l for l in lines if l["category"] in ("APPAREL", "FOOTWEAR")][:3]
    rma = to_received(client, create(client, order, [(soft[0], 1, "DEFECTIVE"), (soft[1], 1, "SIZE_FIT"), (soft[2], 1, "CHANGED_MIND")]))
    fees = []
    for i, l in enumerate(rma["lines"]):
        inspect(client, l["return_line_id"], 1)
        fees.append(refund(client, l["return_line_id"], f"fee-{i}").json()["shipping_fee_paise"])
    assert fees[0] == 0                                   # merchant fault: never charged
    assert sorted(fees[1:]) == [0, POLICY.return_shipping_fee_paise]   # exactly one customer-fault line pays


# --------------------------------------------------------------------------- receipts
def test_partial_receipt_refunds_only_received_units(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: any(l["quantity"] >= 2 and l["category"] in ("APPAREL", "TOYS", "HOME_KITCHEN") for l in ls), max_days=14)
    line = next(l for l in lines if l["quantity"] >= 2 and l["category"] in ("APPAREL", "TOYS", "HOME_KITCHEN"))
    rma = to_received(client, create(client, order, [(line, 2, "WRONG_ITEM")]))
    rl = rma["lines"][0]["return_line_id"]
    inspect(client, rl, 1)
    r = refund(client, rl, "partial-1").json()
    assert r["refund_qty"] == 1
    assert r["amount_paise"] <= line["line_paid_paise"] // line["quantity"] + 1


def test_empty_parcel_closes_without_refund_or_inventory_move(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: any(l["category"] == "APPAREL" for l in ls), max_days=25)
    line = next(l for l in lines if l["category"] == "APPAREL")
    rma = to_received(client, create(client, order, [(line, 1, "CHANGED_MIND")]))
    rl = rma["lines"][0]["return_line_id"]
    done = inspect(client, rl, 0)
    assert done["status"] == "COMPLETED" and done["lines"][0]["resolution"] == "NONE"
    assert refund(client, rl, "empty-1").json()["error"] == "NOTHING_RECEIVED"
    assert client.post(f"/api/return-lines/{rl}/disposition", headers=OP).json()["error"] == "NOTHING_RECEIVED"


# --------------------------------------------------------------------------- lifecycle
def test_shipping_after_ship_by_date_expires(env, clock):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: any(l["category"] == "APPAREL" for l in ls), max_days=25)
    line = next(l for l in lines if l["category"] == "APPAREL")
    rma = create(client, order, [(line, 1, "SIZE_FIT")])
    clock("2026-11-20T09:00:00")                      # > 14 days after authorization
    r = client.post(f"/api/shipments/{rma['shipment']['rts_id']}/ship").json()
    assert r["status"] == "EXPIRED" and r["shipment"]["status"] == "EXPIRED"
    assert r["lines"][0]["resolution"] == "NONE"


def test_lost_parcel_is_terminal(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: any(l["category"] == "FOOTWEAR" for l in ls), max_days=25)
    line = next(l for l in lines if l["category"] == "FOOTWEAR")
    rma = create(client, order, [(line, 1, "SIZE_FIT")])
    rts = rma["shipment"]["rts_id"]
    client.post(f"/api/shipments/{rts}/ship")
    assert client.post(f"/api/shipments/{rts}/lost", headers=OP).json()["status"] == "SHIPMENT_LOST"
    assert client.post(f"/api/shipments/{rts}/receive", headers=OP).json()["error"] == "ILLEGAL_TRANSITION"


def test_request_before_delivery_refused(env, clock):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: True, max_days=10)
    clock(f"{order['order_date'].isoformat()}T00:00:01")
    if order["delivered_date"] == order["order_date"]:
        pytest.skip("store order delivered same day")
    r = client.post("/api/returns", json={"order_id": order["order_id"], "lines": [
        {"order_line_id": lines[0]["order_line_id"], "quantity": 1, "reason_code": "DEFECTIVE"}]})
    assert r.json()["error"] == "NOT_DELIVERED"


def test_mixed_eligibility_rma_completes_on_approved_lines_only(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: any(l["category"] == "APPAREL" for l in ls)
                              and any(not POLICY.category(l["category"]).returnable for l in ls), max_days=25)
    ok = next(l for l in lines if l["category"] == "APPAREL")
    bad = next(l for l in lines if not POLICY.category(l["category"]).returnable)
    rma = to_received(client, create(client, order, [(ok, 1, "DEFECTIVE"), (bad, 1, "DEFECTIVE")]))
    assert rma["authorization"]["decision"] == "PARTIALLY_APPROVED"
    good = next(l for l in rma["lines"] if l["approved_qty"])
    assert client.post(f"/api/return-lines/{next(l for l in rma['lines'] if not l['approved_qty'])['return_line_id']}/inspection",
                       json={"received_qty": 1, "grade": "A_NEW"}, headers=OP).json()["error"] == "NOT_APPROVED"
    inspect(client, good["return_line_id"], 1, "E_DEFECTIVE")
    refund(client, good["return_line_id"], "mixed-1")
    final = client.post(f"/api/return-lines/{good['return_line_id']}/disposition", headers=OP).json()
    assert final["status"] == "COMPLETED"


def test_exchange_cannot_be_refunded(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: any(l["category"] == "APPAREL" for l in ls), max_days=25)
    line = next(l for l in lines if l["category"] == "APPAREL")
    rma = to_received(client, create(client, order, [(line, 1, "SIZE_FIT")], rtype="EXCHANGE"))
    rl = rma["lines"][0]["return_line_id"]
    inspect(client, rl, 1)
    assert refund(client, rl, "exch-1").json()["error"] == "NOT_A_REFUND"
    final = client.post(f"/api/return-lines/{rl}/disposition", headers=OP).json()
    assert final["status"] == "COMPLETED" and final["lines"][0]["resolution"] == "EXCHANGE"


# --------------------------------------------------------------------------- input abuse
def test_idempotency_key_cannot_be_reused_on_another_line(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: sum(l["category"] == "APPAREL" for l in ls) >= 2, max_days=25)
    a, b = [l for l in lines if l["category"] == "APPAREL"][:2]
    rma = to_received(client, create(client, order, [(a, 1, "DEFECTIVE"), (b, 1, "DEFECTIVE")]))
    l1, l2 = (l["return_line_id"] for l in rma["lines"])
    inspect(client, l1, 1)
    inspect(client, l2, 1)
    assert refund(client, l1, "shared-key").status_code == 201
    assert refund(client, l2, "shared-key").json()["error"] == "KEY_REUSED"


def test_request_validation(env):
    client, url, _ = env
    order, lines = find_order(url, lambda ls: True, max_days=25)
    ol = lines[0]["order_line_id"]
    post = lambda body, h=None: client.post("/api/returns", json={"order_id": order["order_id"], **body}, headers=h or {})
    assert post({"lines": [{"order_line_id": ol, "quantity": 0, "reason_code": "DEFECTIVE"}]}).status_code == 422
    assert post({"lines": [{"order_line_id": ol, "quantity": 1, "reason_code": "DEFECTIVE"}] * 2}).json()["error"] == "DUPLICATE_LINE"
    assert post({"lines": []}).status_code == 422
    other = find_order(url, lambda ls: True)[1][0]["order_line_id"]
    assert post({"lines": [{"order_line_id": other, "quantity": 1, "reason_code": "DEFECTIVE"}]}).json()["error"] == "LINE_NOT_ON_ORDER"
    assert post({"lines": [{"order_line_id": ol, "quantity": 1, "reason_code": "DEFECTIVE"}],
                 "override": {"reason": "x", "by": "OPR-1"}}, OP).status_code == 422          # reason too short
    assert post({"lines": [{"order_line_id": ol, "quantity": 1, "reason_code": "DEFECTIVE"}]},
                {"X-Role": "customer", "X-Actor": "CUST-999999"}).json()["error"] == "FORBIDDEN"
    assert client.get("/api/returns/RMA-2026-999999").status_code == 404


# --------------------------------------------------------------------------- concurrency
def test_concurrent_refunds_pay_out_once(env):
    """8 simultaneous refund calls with different keys on one line: exactly one payout, no 5xx."""
    client, url, app = env
    order, lines = find_order(url, lambda ls: any(l["category"] == "APPAREL" for l in ls), max_days=25)
    line = next(l for l in lines if l["category"] == "APPAREL")
    rma = to_received(client, create(client, order, [(line, 1, "DEFECTIVE")]))
    rl = rma["lines"][0]["return_line_id"]
    inspect(client, rl, 1)
    statuses, barrier = [], threading.Barrier(8)

    def go(i):
        c = TestClient(app, raise_server_exceptions=False)   # a crash must show up as a 5xx, not vanish in a thread
        barrier.wait()
        statuses.append(c.post(f"/api/return-lines/{rl}/refunds", headers=OP | {"Idempotency-Key": f"race-{i}"}).status_code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with db.make_engine(url).connect() as c:
        ok = db.select(c, "refund", "return_line_id = :l AND status = 'SUCCEEDED'", {"l": rl})
    assert len(statuses) == 8, statuses
    assert len(ok) == 1, f"{len(ok)} payouts; statuses {statuses}"
    assert sorted(statuses) == [201] + [409] * 7, statuses        # one payout, seven recorded duplicates
    assert all(s < 500 for s in statuses), statuses


# --------------------------------------------------------------------------- generator robustness
@pytest.mark.parametrize("seed", [1, 7, 42, 2026, 99991])
def test_generator_valid_for_any_seed(tmp_path, seed):
    cmd_generate("tiny", seed, tmp_path / "d", quiet=True)
    s = cmd_validate(tmp_path / "d", quiet=True)
    assert s["overall_status"] == "PASS", [c["name"] for c in s["checks"] if c["status"] != "PASS"]
