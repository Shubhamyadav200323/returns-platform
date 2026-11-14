"""HTTP layer (FastAPI). Thin: validates input shape, checks role, delegates to services.

Auth is a deliberate stand-in: callers send `X-Role` (customer | operator | analyst | manager)
and `X-Actor` (their id). Swap `actor()` for real auth (OIDC/JWT) without touching services.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from returns_platform import db
from returns_platform.api.services import CLOCK, DomainError, ReturnsService
from returns_platform.config import ROOT, load_policy

WEB_DIR = ROOT / "web"
Role = Literal["customer", "operator", "analyst", "manager"]


class Actor(BaseModel):
    role: Role
    id: str


def actor(x_role: str = Header("customer"), x_actor: str | None = Header(None)) -> Actor:
    if x_role not in ("customer", "operator", "analyst", "manager"):
        raise DomainError("BAD_ROLE", "X-Role must be customer, operator, analyst or manager", 401)
    return Actor(role=x_role, id=x_actor or {"customer": "CUSTOMER", "operator": "OPR-001",
                                             "analyst": "ANL-001", "manager": "MGR-001"}[x_role])


def require(*roles: str):
    def dep(a: Actor = Depends(actor)) -> Actor:
        if a.role not in roles:
            raise DomainError("FORBIDDEN", f"role '{a.role}' cannot perform this action (needs {', '.join(roles)})", 403)
        return a
    return dep


class ReturnLineIn(BaseModel):
    order_line_id: str
    quantity: int = Field(ge=1, le=99)
    reason_code: str


class OverrideIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    by: str = Field(min_length=3, max_length=40)


class CreateReturnIn(BaseModel):
    order_id: str
    request_type: Literal["REFUND", "EXCHANGE"] = "REFUND"
    channel: Literal["WEB", "APP", "STORE"] = "WEB"
    lines: list[ReturnLineIn] = Field(min_length=1, max_length=20)
    override: OverrideIn | None = None


class InspectionIn(BaseModel):
    received_qty: int = Field(ge=0, le=99)
    grade: str
    damage_noted: bool = False
    notes: str | None = Field(None, max_length=500)


class RefundIn(BaseModel):
    amount_paise: int | None = Field(None, ge=0, description="Optional. Must equal the policy-computed amount.")


def create_app(db_url: str = "sqlite:///out/returns.db", clock_start: datetime | None = None) -> FastAPI:
    """clock_start: run the demo clock from this instant (floored at the latest stored event)."""
    policy = load_policy()
    svc = ReturnsService(db.make_engine(db_url), policy)
    if clock_start is not None:
        CLOCK.run_from(clock_start, floor=svc.latest_event_at())
    app = FastAPI(title="Returns Management API", version="1.0",
                  description="Eligibility → authorization → shipment → inspection → refund → disposition.")
    app.state.svc = svc

    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message, **exc.detail})

    def ok(payload, status=200):
        return JSONResponse(status_code=status, content=jsonable_encoder(payload))

    # ------------------------------------------------------------ reads
    @app.get("/api/health")
    def health():
        with svc.engine.connect() as c:
            n = c.exec_driver_sql("SELECT COUNT(*) FROM return_request").scalar()
        return {"status": "ok", "dialect": db.dialect(svc.engine), "return_requests": n, "policy": policy.version,
                "clock": {"mode": CLOCK.mode, "now": CLOCK.now().isoformat(sep=" ")}}

    @app.get("/api/policy")
    def get_policy():
        return {"version": policy.version, "categories": {k: vars(v) for k, v in policy.categories.items()},
                "reasons": policy.reason_fault, "grade_refund_bp": policy.grade_refund_bp,
                "return_shipping_fee_paise": policy.return_shipping_fee_paise, "ship_by_days": policy.ship_by_days}

    @app.get("/api/customers")
    def list_customers(limit: int = Query(20, le=100)):
        with svc.engine.connect() as c:
            rows = c.exec_driver_sql(
                "SELECT c.customer_id, c.full_name, c.segment, COUNT(o.order_id) AS orders FROM customer c "
                "JOIN sales_order o ON o.customer_id = c.customer_id GROUP BY c.customer_id, c.full_name, c.segment "
                f"ORDER BY COUNT(o.order_id) DESC, c.customer_id LIMIT {int(limit)}").mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/customers/{customer_id}/orders")
    def customer_orders(customer_id: str, limit: int = Query(20, le=100)):
        with svc.engine.connect() as c:
            return ok(db.select(c, "sales_order", "customer_id = :c", {"c": customer_id},
                                order_by="order_date DESC, order_id DESC", limit=limit))

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: str):
        return ok(svc.get_order(order_id))

    @app.get("/api/returns")
    def list_returns(status: str | None = None, customer_id: str | None = None,
                     limit: int = Query(50, le=200), offset: int = 0):
        return ok(svc.list_returns(status, customer_id, limit, offset))

    @app.get("/api/returns/{rma_id}")
    def get_return(rma_id: str):
        return ok(svc.get_return(rma_id))

    # ------------------------------------------------------------ customer
    @app.post("/api/returns", status_code=201)
    def create_return(body: CreateReturnIn, a: Actor = Depends(require("customer", "operator", "manager"))):
        if a.role == "customer" and a.id != "CUSTOMER":
            order = svc.get_order(body.order_id)
            if order["customer_id"] != a.id:
                raise DomainError("FORBIDDEN", "customers can only return their own orders", 403)
        if body.override and a.role == "customer":
            raise DomainError("FORBIDDEN", "only operators can override the return window", 403)
        rma = svc.create_return(order_id=body.order_id, request_type=body.request_type, channel=body.channel,
                                lines=[l.model_dump() for l in body.lines],
                                override=body.override.model_dump() if body.override else None, actor_id=a.id)
        return ok(rma, 201)

    # ------------------------------------------------------------ operator
    @app.post("/api/shipments/{rts_id}/ship")
    def ship(rts_id: str, a: Actor = Depends(require("customer", "operator", "manager"))):
        return ok(svc.ship(rts_id))

    @app.post("/api/shipments/{rts_id}/receive")
    def receive(rts_id: str, a: Actor = Depends(require("operator", "manager"))):
        return ok(svc.receive(rts_id, a.id))

    @app.post("/api/shipments/{rts_id}/lost")
    def lost(rts_id: str, a: Actor = Depends(require("operator", "manager"))):
        return ok(svc.mark_lost(rts_id, a.id))

    @app.post("/api/return-lines/{line_id}/inspection")
    def inspect(line_id: str, body: InspectionIn, a: Actor = Depends(require("operator", "manager"))):
        return ok(svc.inspect(line_id, received_qty=body.received_qty, grade=body.grade, inspector_id=a.id,
                              damage_noted=body.damage_noted, notes=body.notes))

    @app.post("/api/return-lines/{line_id}/disposition")
    def dispose(line_id: str, a: Actor = Depends(require("operator", "manager"))):
        return ok(svc.dispose(line_id, a.id))

    @app.post("/api/return-lines/{line_id}/refunds")
    def refund(line_id: str, body: RefundIn | None = None, idempotency_key: str = Header(...),
               a: Actor = Depends(require("operator", "manager"))):
        row, status = svc.refund(line_id, idempotency_key=idempotency_key, operator=a.id,
                                 amount_paise=body.amount_paise if body else None)
        return ok(row, status)

    # ------------------------------------------------------------ analyst / manager
    @app.get("/api/metrics")
    def metrics(a: Actor = Depends(require("analyst", "manager", "operator"))):
        return ok(svc.metrics())

    @app.get("/api/exceptions")
    def exceptions(a: Actor = Depends(require("analyst", "manager", "operator"))):
        return ok(svc.exceptions())

    @app.get("/api/integrity")
    def integrity(a: Actor = Depends(require("analyst", "manager"))):
        return ok(svc.integrity())

    # ------------------------------------------------------------ presentation tier
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(WEB_DIR / "index.html")

    return app
