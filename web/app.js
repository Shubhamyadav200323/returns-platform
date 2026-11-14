"use strict";
// Presentation tier. No business rules here: every decision shown comes from the API.

const state = { role: "customer", customer: null, statusFilter: "RECEIVED", policy: null, rma: null };
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const money = (p) => (p == null ? "—" : inr.format(p / 100));
const fmtTs = (t) => (t ? String(t).replace("T", " ").slice(0, 16) : "—");
const nice = (s) => String(s ?? "").replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());

const ROLE_ACTOR = { customer: null, operator: "OPR-007", analyst: "ANL-001", manager: "MGR-001" };
const STATUS_TONE = { COMPLETED: "ok", REJECTED: "bad", SHIPMENT_LOST: "bad", EXPIRED: "warn", AUTHORIZED: "info",
  IN_TRANSIT: "info", RECEIVED: "warn", INSPECTED: "warn", REQUESTED: "", SUCCEEDED: "ok",
  REJECTED_DUPLICATE: "bad", REJECTED_EXCEEDS_PAID: "bad", APPROVED: "ok", PARTIALLY_APPROVED: "warn",
  RESTOCK: "ok", REFURBISH: "info", QUARANTINE: "warn", SCRAP: "bad" };
const badge = (s) => `<span class="badge ${STATUS_TONE[s] || ""}">${esc(nice(s))}</span>`;

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const h = { "X-Role": state.role, ...headers };
  const actor = state.role === "customer" ? state.customer : ROLE_ACTOR[state.role];
  if (actor) h["X-Actor"] = actor;
  if (body !== undefined) h["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers: h, body: body !== undefined ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !(res.status === 409 && data.refund_id) && !(res.status === 422 && data.refund_id)) {
    throw Object.assign(new Error(data.message || res.statusText), { code: data.error, status: res.status, data });
  }
  return { status: res.status, data };
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = ""), 3800);
}
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(`${e.code || "Error"}: ${e.message}`, true); } };

// ------------------------------------------------------------------ shell
document.querySelectorAll(".roles button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".roles button").forEach((x) => x.classList.toggle("active", x === b));
    state.role = b.dataset.role;
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${state.role}`));
    ({ customer: loadCustomer, operator: loadQueue, analyst: loadAnalytics, manager: loadManager })[state.role]();
  }));

async function boot() {
  const [{ data: h }, { data: p }] = await Promise.all([api("/api/health"), api("/api/policy")]);
  state.policy = p;
  $("#health").textContent = `${h.dialect} · ${h.return_requests.toLocaleString("en-IN")} returns`;
  $("#policy").textContent = `policy ${p.version}`;
  const { data: custs } = await api("/api/customers?limit=40");
  $("#cust-select").innerHTML = custs.map((c) => `<option value="${esc(c.customer_id)}">${esc(c.full_name)} · ${esc(c.customer_id)} · ${c.orders} orders</option>`).join("");
  $("#cust-select").addEventListener("change", guard(loadCustomer));
  loadCustomer();
}

// ------------------------------------------------------------------ customer
const loadCustomer = guard(async () => {
  state.customer = $("#cust-select").value;
  const [{ data: orders }, { data: rets }] = await Promise.all([
    api(`/api/customers/${state.customer}/orders?limit=30`),
    api(`/api/returns?customer_id=${state.customer}&limit=30`)]);
  $("#cust-orders").innerHTML = orders.map((o) => `<li data-id="${esc(o.order_id)}"><span>${esc(o.order_id)}<div class="sub">delivered ${esc(o.delivered_date)}</div></span><span class="mono">${money(o.total_paid_paise)}</span></li>`).join("") || `<li class="sub">No orders</li>`;
  $("#cust-returns").innerHTML = rets.items.map((r) => `<li data-rma="${esc(r.rma_id)}"><span>${esc(r.rma_id)}<div class="sub">${esc(r.order_id)}</div></span>${badge(r.status)}</li>`).join("") || `<li class="sub">No returns yet</li>`;
  $("#cust-orders").querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => openOrder(li.dataset.id)));
  $("#cust-returns").querySelectorAll("li[data-rma]").forEach((li) => li.addEventListener("click", () => showCustomerRma(li.dataset.rma)));
});

const openOrder = guard(async (orderId, opts = {}) => {
  const { data: o } = await api(`/api/orders/${orderId}`);
  const panel = opts.panel || $("#order-panel");
  const reasons = Object.keys(state.policy.reasons);
  panel.innerHTML = `
    <div class="head"><h2>${esc(o.order_id)}</h2><span class="muted">${esc(o.customer.full_name)} · ${esc(o.channel)} · paid ${money(o.total_paid_paise)}</span></div>
    <div class="facts"><span>Ordered <b>${esc(o.order_date)}</b></span><span>Delivered <b>${esc(o.delivered_date)}</b></span>
      <span>Payment <b>${esc(o.payment_method)}</b></span>${o.coupon_code ? `<span>Coupon <b>${esc(o.coupon_code)}</b></span>` : ""}
      ${o.returns.length ? `<span>Earlier returns <b>${o.returns.map(esc).join(", ")}</b></span>` : ""}</div>
    <table><thead><tr><th>Item</th><th>Window</th><th class="num">Paid</th><th class="num">Returnable</th><th>Return qty</th><th>Reason</th></tr></thead><tbody>
    ${o.lines.map((l) => `<tr>
      <td>${esc(l.product_name)}<div class="sub muted">${esc(nice(l.category))} · ${esc(l.order_line_id)}</div></td>
      <td>${!l.returnable_category ? badge("Not returnable")
        : l.returnable_qty === 0 ? `<span class="badge">Already returned</span>`
        : l.within_window ? `<span class="badge ok">${l.window_days - l.days_since_delivery} days left</span>`
        : `<span class="badge warn">closed ${l.days_since_delivery - l.window_days}d ago</span>`}</td>
      <td class="num">${money(l.line_paid_paise)}<div class="sub muted">${l.quantity} × ${money(l.unit_price_paise)}</div></td>
      <td class="num">${l.returnable_qty} / ${l.quantity}</td>
      <td><input type="number" min="0" max="99" value="0" data-line="${esc(l.order_line_id)}" aria-label="Quantity to return" ${l.returnable_qty === 0 ? "disabled" : ""}></td>
      <td><select data-reason="${esc(l.order_line_id)}">${reasons.map((r) => `<option>${r}</option>`).join("")}</select></td></tr>`).join("")}
    </tbody></table>
    <div class="actions">
      <select id="rtype" style="width:auto"><option value="REFUND">Refund</option><option value="EXCHANGE">Exchange</option></select>
      ${opts.override ? `<input id="ovr-reason" placeholder="Override reason (operator)" style="width:260px"><span class="muted">Override bypasses the window, never exclusions.</span>` : ""}
      <button class="primary" id="submit-return">Request return</button>
    </div>
    <p class="muted">Eligibility is decided by policy on submit — ask for any quantity; the system approves what the rules allow and explains the rest.</p>`;
  panel.querySelector("#submit-return").addEventListener("click", guard(async () => {
    const lines = [...panel.querySelectorAll("input[data-line]")].filter((i) => +i.value > 0).map((i) => ({
      order_line_id: i.dataset.line, quantity: +i.value,
      reason_code: panel.querySelector(`select[data-reason="${i.dataset.line}"]`).value }));
    if (!lines.length) return toast("Enter a quantity on at least one line", true);
    const body = { order_id: o.order_id, request_type: panel.querySelector("#rtype").value, channel: "WEB", lines };
    const ovr = panel.querySelector("#ovr-reason");
    if (ovr && ovr.value.trim()) body.override = { reason: ovr.value.trim(), by: ROLE_ACTOR.operator };
    const { data: rma } = await api("/api/returns", { method: "POST", body });
    toast(`${rma.rma_id}: ${nice(rma.authorization.decision)}`);
    if (opts.onCreated) return opts.onCreated(rma);
    showCustomerRma(rma.rma_id);
    loadCustomer();
  }));
});

const showCustomerRma = guard(async (rmaId) => {
  const { data: r } = await api(`/api/returns/${rmaId}`);
  const s = r.shipment;
  $("#order-panel").innerHTML = `
    <div class="head"><h2>${esc(r.rma_id)}</h2>${badge(r.status)}</div>
    <div class="facts"><span>Order <b>${esc(r.order_id)}</b></span><span>Type <b>${esc(nice(r.request_type))}</b></span>
      ${r.authorization.ship_by_date ? `<span>Ship by <b>${esc(r.authorization.ship_by_date)}</b></span>` : ""}
      ${s ? `<span>Label <b>${esc(s.carrier)} ${esc(s.tracking_number)}</b></span>` : ""}
      <span>Refunded <b>${money(r.totals.refunded_paise)}</b></span></div>
    ${lineDecisionTable(r)}
    ${s && s.status === "LABEL_CREATED" ? `<div class="actions"><button class="primary" id="handover">I've handed the parcel to the courier</button></div>` : ""}
    <h3>History</h3>${timeline(r.events)}`;
  const b = $("#handover");
  if (b) b.addEventListener("click", guard(async () => { await api(`/api/shipments/${s.rts_id}/ship`, { method: "POST" }); showCustomerRma(rmaId); loadCustomer(); }));
});

function lineDecisionTable(r) {
  return `<table><thead><tr><th>Line</th><th>Decision</th><th class="num">Requested</th><th class="num">Approved</th><th>Why</th></tr></thead><tbody>
    ${r.lines.map((l) => `<tr><td>${esc(l.product_name)}<div class="sub muted">${esc(nice(l.reason_code))}</div></td>
      <td>${badge(l.decision)}</td><td class="num">${l.requested_qty}</td><td class="num">${l.approved_qty}</td>
      <td>${l.rejection_reason ? esc(nice(l.rejection_reason)) + (l.rejection_reason === "WINDOW_EXPIRED" ? ` (day ${l.days_since_delivery} of ${l.window_days})` : "") : "Within policy"}</td></tr>`).join("")}
    </tbody></table>`;
}

function timeline(events) {
  return `<ul class="timeline">${events.map((e) => `<li class="${/REJECT|LOST|EXPIRED|DUPLICATE|EXCEEDS/.test(e.to_state) ? "bad" : ""}">
    <time>${String(e.occurred_at).replace("T", " ").slice(0, 19)}</time><b>${esc(nice(e.entity_type))}</b> → ${esc(nice(e.to_state))}
    <span class="muted">· ${esc(e.actor_type.toLowerCase())} ${esc(e.actor_id)}</span></li>`).join("")}</ul>`;
}

// ------------------------------------------------------------------ operator
const QUEUE_STATES = ["AUTHORIZED", "IN_TRANSIT", "RECEIVED", "INSPECTED", "COMPLETED", "SHIPMENT_LOST", "REJECTED"];

const loadQueue = guard(async () => {
  $("#status-chips").innerHTML = QUEUE_STATES.map((s) => `<button class="${s === state.statusFilter ? "on" : ""}" data-s="${s}">${esc(nice(s))}</button>`).join("");
  $("#status-chips").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { state.statusFilter = b.dataset.s; loadQueue(); }));
  const { data } = await api(`/api/returns?status=${state.statusFilter}&limit=60`);
  $("#queue").innerHTML = data.items.map((r) => `<li data-rma="${esc(r.rma_id)}" class="${state.rma === r.rma_id ? "sel" : ""}">
      <span>${esc(r.rma_id)}<div class="sub">${esc(nice(r.request_type))} · ${fmtTs(r.requested_at)}</div></span>${r.override_flag ? badge("override") : ""}</li>`).join("") || `<li class="sub">Nothing in this state</li>`;
  $("#queue").querySelectorAll("li[data-rma]").forEach((li) => li.addEventListener("click", () => openRma(li.dataset.rma)));
});

$("#lookup-go").addEventListener("click", guard(async () => {
  const v = $("#lookup").value.trim().toUpperCase();
  if (v.startsWith("RMA-")) return openRma(v);
  if (v.startsWith("ORD-")) return openOrder(v, { panel: $("#rma-panel"), override: true, onCreated: (r) => { openRma(r.rma_id); loadQueue(); } });
  toast("Enter an RMA-… or ORD-… id", true);
}));

const openRma = guard(async (rmaId) => {
  state.rma = rmaId;
  const { data: r } = await api(`/api/returns/${rmaId}`);
  const s = r.shipment;
  const panel = $("#rma-panel");
  const grades = ["A_NEW", "B_LIKE_NEW", "C_USED", "D_DAMAGED", "E_DEFECTIVE"];
  // Only the step that applies now is offered; finished steps show their outcome instead.
  const shipStep = !s ? "" :
    s.status === "LABEL_CREATED" ? `<div class="next"><span class="step">Next</span>Waiting for the courier to collect the parcel.
        <button data-act="ship">Record courier pick-up</button></div>` :
    s.status === "IN_TRANSIT" ? `<div class="next"><span class="step">Next</span>Parcel is with ${esc(s.carrier)} (${esc(s.tracking_number)}).
        <button class="primary" data-act="receive">Receive at warehouse</button><button class="danger" data-act="lost">Mark lost</button></div>` : "";
  const lineCard = (l) => {
    const ins = l.inspection, d = l.disposition;
    const ok = l.refunds.find((x) => x.status === "SUCCEEDED");
    const steps = [];
    if (l.approved_qty > 0) {
      if (ins) steps.push(`<div class="done">✓ Inspected: <b>${ins.received_qty}</b> of ${l.approved_qty} received · ${badge(ins.grade)}${ins.notes ? ` <span class="muted">${esc(ins.notes)}</span>` : ""}</div>`);
      else if (r.status === "RECEIVED") steps.push(`<div class="next"><span class="step">Next</span>Inspect:
          <input type="number" min="0" max="${l.approved_qty}" value="${l.approved_qty}" data-recv="${esc(l.return_line_id)}" aria-label="Units received">
          <select data-grade="${esc(l.return_line_id)}" aria-label="Condition grade">${grades.map((g) => `<option value="${g}">${esc(nice(g))}</option>`).join("")}</select>
          <button class="primary" data-act="inspect" data-line="${esc(l.return_line_id)}">Record inspection</button></div>`);
      if (ins && ins.received_qty > 0) {
        if (d) steps.push(`<div class="done">✓ Disposition ${badge(d.disposition)} → ${esc(nice(d.to_location))}${d.sellable_qty_delta ? `, +${d.sellable_qty_delta} sellable` : ""}</div>`);
        else steps.push(`<div class="next"><span class="step">Next</span>Decide where the units go (from the grade).
            <button data-act="dispose" data-line="${esc(l.return_line_id)}">Decide disposition</button></div>`);
        if (r.request_type === "REFUND") {
          if (ok) steps.push(`<div class="done">✓ Refunded <b>${money(ok.amount_paise)}</b>
              <details class="demo"><summary>Test duplicate protection</summary>
                <button data-act="retry" data-line="${esc(l.return_line_id)}" title="Same Idempotency-Key: returns the stored result">Retry with same key</button>
                <button data-act="double" data-line="${esc(l.return_line_id)}" title="New key after a payout: blocked and recorded">Simulate double-submit</button>
              </details></div>`);
          else steps.push(`<div class="next"><span class="step">Next</span>Amount is computed by policy.
              <button class="primary" data-act="refund" data-line="${esc(l.return_line_id)}">Issue refund</button></div>`);
        } else if (d) steps.push(`<div class="done">✓ Exchanged for ${esc(l.exchange_product_id || "same item")}</div>`);
      } else if (ins) steps.push(`<div class="done warn-text">Nothing arrived for this line: no refund, no stock movement.</div>`);
    }
    return `<div class="subcard">
      <div class="head"><div><b>${esc(l.product_name)}</b> <span class="muted mono">${esc(l.return_line_id)}</span></div>
        <div>${badge(l.decision)} <span class="badge">${esc(nice(l.resolution))}</span></div></div>
      <div class="facts"><span>${esc(nice(l.order_line.category))}</span><span>Reason <b>${esc(nice(l.reason_code))}</b></span>
        <span>Approved <b>${l.approved_qty}</b> of ${l.requested_qty} requested</span><span>Line paid <b>${money(l.order_line.line_paid_paise)}</b></span>
        ${l.rejection_reason ? `<span>${esc(nice(l.rejection_reason))}</span>` : ""}</div>
      ${steps.join("")}
      ${l.refunds.length ? `<div class="scroll"><table><thead><tr><th>Refund attempt</th><th>Status</th><th class="num">Item</th><th class="num">Tax</th><th class="num">Fees</th><th class="num">Paid</th></tr></thead><tbody>
        ${l.refunds.map((x) => `<tr><td class="mono">${esc(x.refund_id)}<div class="sub muted">attempt ${x.attempt_no}</div></td><td>${badge(x.status)}</td>
          <td class="num">${money(x.item_amount_paise)}<div class="sub muted">grade ${x.grade_refund_bp / 100}%</div></td><td class="num">${money(x.tax_amount_paise)}</td>
          <td class="num">${money(x.restocking_fee_paise + x.shipping_fee_paise)}</td><td class="num"><b>${money(x.amount_paise)}</b></td></tr>`).join("")}</tbody></table></div>` : ""}
    </div>`;
  };
  panel.innerHTML = `
    <div class="head"><h2>${esc(r.rma_id)}</h2><div>${badge(r.status)} ${r.override_flag ? badge("override") : ""}</div></div>
    <div class="facts"><span>Order <b>${esc(r.order_id)}</b></span><span>Customer <b>${esc(r.customer_id)}</b></span>
      <span>Type <b>${esc(nice(r.request_type))}</b></span><span>Decided by <b>${esc(r.authorization.decided_by)}</b></span>
      ${r.override_flag ? `<span>Override <b>${esc(r.override_reason)}</b></span>` : ""}
      ${s ? `<span>Shipment <b>${esc(s.rts_id)}</b> ${badge(s.status)}</span>` : ""}
      <span>Refunded <b>${money(r.totals.refunded_paise)}</b></span>
      ${r.totals.blocked_attempts ? `<span>Blocked attempts <b>${r.totals.blocked_attempts}</b></span>` : ""}</div>
    ${shipStep}
    ${r.lines.map(lineCard).join("")}
    <h3>Audit trail</h3>${timeline(r.events)}`;

  panel.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", guard(async () => {
    const line = b.dataset.line, act = b.dataset.act;
    b.disabled = true;
    if (act === "ship") await api(`/api/shipments/${s.rts_id}/ship`, { method: "POST" });
    if (act === "receive") await api(`/api/shipments/${s.rts_id}/receive`, { method: "POST" });
    if (act === "lost") await api(`/api/shipments/${s.rts_id}/lost`, { method: "POST" });
    if (act === "inspect") await api(`/api/return-lines/${line}/inspection`, { method: "POST", body: {
      received_qty: +panel.querySelector(`[data-recv="${line}"]`).value, grade: panel.querySelector(`[data-grade="${line}"]`).value } });
    if (act === "dispose") await api(`/api/return-lines/${line}/disposition`, { method: "POST" });
    if (act === "refund" || act === "double") {
      const key = crypto.randomUUID();
      sessionStorage.setItem(`key:${line}`, key);
      const { status, data } = await api(`/api/return-lines/${line}/refunds`, { method: "POST", headers: { "Idempotency-Key": key } });
      toast(status === 201 ? `Refund ${data.refund_id}: ${money(data.amount_paise)}` : `Blocked: ${nice(data.status)} (recorded as ${data.refund_id})`, status !== 201);
    }
    if (act === "retry") {
      const key = sessionStorage.getItem(`key:${line}`) || "unknown";
      const { status, data } = await api(`/api/return-lines/${line}/refunds`, { method: "POST", headers: { "Idempotency-Key": key } });
      toast(status === 200 ? `Replay: returned stored ${data.refund_id}, nothing paid twice` : `${nice(data.status)}`);
    }
    await openRma(rmaId);
    loadQueue();
  })));
});

// ------------------------------------------------------------------ charts / KPIs
function bars(entries, { fmt = (v) => v, max, cls, label = nice } = {}) {
  const m = max ?? Math.max(1, ...entries.map((e) => e[1]));
  return `<div class="bars">${entries.map(([k, v, extra]) => `<span>${esc(label(k))}</span>
    <div class="track"><div class="fill ${cls ? cls(k) : ""}" style="width:${(100 * v) / m}%"></div></div><span class="val">${fmt(v, extra)}</span>`).join("")}</div>`;
}
const kpi = (v, l, tone = "") => `<div class="kpi ${tone}"><div class="v">${v}</div><div class="l">${l}</div></div>`;
const rupees = (p) => money(p).replace(/\.\d+$/, "");

// ------------------------------------------------------------------ analyst (merchandising / e-commerce)
const loadAnalytics = guard(async () => {
  const { data: m } = await api("/api/metrics");
  const e = m.eligibility, rf = m.refunds, lc = m.lifecycle, inv = m.inventory;
  $("#kpis").innerHTML = [
    kpi(`${m.return_rate.units_requested_pct_of_sold}%`, "of units sold requested back"),
    kpi(`${((100 * e.units_approved) / e.units_requested).toFixed(1)}%`, "of requested units approved"),
    kpi(rupees(rf.total_refunded_paise), `refunded over ${rf.payouts.toLocaleString("en-IN")} payouts`),
    kpi(`${rf.refund_pct_of_paid_on_refunded_lines}%`, "of paid value returned on refunded lines"),
    kpi(`${m.reasons.merchant_fault_pct}%`, "of returned lines were our fault"),
    kpi(inv.sellable_units_recovered.toLocaleString("en-IN"), `units back to sellable stock (${rupees(inv.recovered_list_value_paise)})`),
  ].join("");
  const cats = Object.entries(m.return_rate.by_category).sort((a, b) => b[1].request_rate_pct - a[1].request_rate_pct);
  const nonReturnable = new Set(Object.entries(state.policy.categories).filter(([, c]) => !c.returnable).map(([k]) => k));
  $("#chart-cat").innerHTML = bars(cats.map(([k, v]) => [k, v.request_rate_pct, v]), { fmt: (v) => `${v}%`, cls: (k) => (nonReturnable.has(k) ? "excluded" : "") }) +
    `<p class="muted">Grey = excluded categories (requests still arrive and are rejected by policy).</p>`;
  const merchant = new Set(Object.entries(state.policy.reasons).filter(([, f]) => f === "merchant").map(([k]) => k));
  $("#chart-reason").innerHTML = bars(Object.entries(m.reasons.by_reason).sort((a, b) => b[1] - a[1]), { cls: (k) => (merchant.has(k) ? "alert" : "") }) +
    `<p class="muted">Red = our fault (defective, wrong item, not as described, damaged in transit): no fees charged.</p>`;
  $("#chart-disp").innerHTML = bars(Object.entries(inv.units_by_disposition).sort((a, b) => b[1] - a[1]), { fmt: (v) => `${v.toLocaleString("en-IN")} u` });
  $("#chart-rej").innerHTML = bars(Object.entries(e.rejection_reasons).sort((a, b) => b[1] - a[1]));
});

// ------------------------------------------------------------------ operations manager
const EXC_ACTION = {
  lost_shipments: "Raise carrier claim; contact customer",
  awaiting_inspection_past_sla: "Inspect today",
  awaiting_refund_past_sla: "Issue refund",
  blocked_refund_attempts: "Review — no money moved",
  empty_or_missing_parcels: "Investigate / fraud review",
  window_overrides: "Audit override reason",
};
const EXC_LABEL = {
  lost_shipments: "Lost shipments",
  awaiting_inspection_past_sla: "Awaiting inspection (past SLA)",
  awaiting_refund_past_sla: "Awaiting refund (past SLA)",
  blocked_refund_attempts: "Blocked refund attempts",
  empty_or_missing_parcels: "Empty or short parcels",
  window_overrides: "Window overrides",
};
const titleCase = (s) => String(s).split("_").map((w) => w[0] + w.slice(1).toLowerCase()).join(" ");
const exampleId = (x) => (x ? x.rma_id || x.refund_id || x.inspection_id || x.return_line_id || "" : "");

const loadManager = guard(async () => {
  const [{ data: m }, { data: ex }] = await Promise.all([api("/api/metrics"), api("/api/exceptions")]);
  const op = m.operations, lc = m.lifecycle, rf = m.refunds;
  const blocked = Object.values(rf.blocked_attempts).reduce((a, b) => a + b, 0);
  const stale = Object.values(op.backlog).reduce((a, b) => a + b.over_7_days, 0);
  const shipped = Object.values(op.carriers).reduce((a, c) => a + c.shipped, 0);
  const lost = Object.values(op.carriers).reduce((a, c) => a + c.lost, 0);
  $("#mgr-kpis").innerHTML = [
    kpi(op.open_total.toLocaleString("en-IN"), "returns open right now"),
    kpi(stale.toLocaleString("en-IN"), "stuck > 7 days in one stage", stale ? "warn" : ""),
    kpi(`${lc.inspection_sla_breach_pct}%`, "inspections past 2-day SLA", lc.inspection_sla_breach_pct > 10 ? "warn" : ""),
    kpi(`${lc.median_request_to_refund_days} d`, "median request → refund"),
    kpi(`${((100 * lost) / Math.max(1, shipped)).toFixed(2)}%`, `parcels lost in transit (${lost})`),
    kpi(blocked.toLocaleString("en-IN"), `bad payouts blocked (${rupees(rf.blocked_value_paise)})`),
  ].join("");
  $("#mgr-backlog").innerHTML = `<div class="scroll"><table><thead><tr><th>Stage</th><th class="num">Open</th><th class="num">> 7 days</th><th class="num">Oldest</th></tr></thead><tbody>
    ${Object.entries(op.backlog).filter(([, b]) => b.count).map(([k, b]) => `<tr><td>${badge(k)}</td><td class="num">${b.count}</td>
      <td class="num">${b.over_7_days ? `<span class="badge warn">${b.over_7_days}</span>` : "0"}</td><td class="num">${b.oldest_days} d</td></tr>`).join("")}
    </tbody></table></div><p class="muted">Time in current stage, measured at the latest recorded activity (${esc(op.as_of)}).</p>`;
  const worst = Math.max(...Object.values(op.carriers).map((c) => c.lost_pct));
  $("#mgr-carriers").innerHTML = `<div class="scroll"><table><thead><tr><th>Carrier</th><th class="num">Shipped</th><th class="num">Lost</th><th class="num">Loss rate</th><th class="num">Median transit</th></tr></thead><tbody>
    ${Object.entries(op.carriers).sort((a, b) => b[1].lost_pct - a[1].lost_pct).map(([k, c]) => `<tr><td>${esc(titleCase(k))}</td><td class="num">${c.shipped.toLocaleString("en-IN")}</td>
      <td class="num">${c.lost}</td><td class="num">${c.lost_pct === worst ? `<span class="badge bad">${c.lost_pct}%</span>` : `${c.lost_pct}%`}</td><td class="num">${c.median_transit_days} d</td></tr>`).join("")}
    </tbody></table></div>`;
  $("#mgr-throughput").innerHTML = bars(Object.entries(op.completed_per_week), { fmt: (v) => `${v}` }).replaceAll(/>(\d{4})-w(\d+)</gi, ">Week $2<");
  $("#mgr-overrides").innerHTML = bars(Object.entries(op.overrides_by_operator), { label: (k) => k }) +
    `<p class="muted">${op.overrides_total} window overrides in total. Each records who approved it and why.</p>`;
  $("#exceptions").innerHTML = `<div class="scroll"><table><thead><tr><th>Queue</th><th class="num">Items</th><th>Example</th><th>Next action</th></tr></thead><tbody>
    ${Object.entries(ex.counts).map(([k, n]) => `<tr><td>${esc(EXC_LABEL[k] || nice(k))}</td><td class="num">${n ? `<b>${n}</b>` : "0"}</td>
      <td class="mono">${esc(exampleId(ex[k][0]))}</td><td>${n ? esc(EXC_ACTION[k] || "") : `<span class="muted">Nothing to do</span>`}</td></tr>`).join("")}
    </tbody></table></div>`;
});

$("#run-integrity").addEventListener("click", guard(async () => {
  $("#integrity").innerHTML = `<p class="muted">Running…</p>`;
  const { data } = await api("/api/integrity");
  $("#integrity").innerHTML = `<div class="scroll"><table><thead><tr><th>Check</th><th class="num">Flagged</th><th>Example</th></tr></thead><tbody>${data.checks.map((c) =>
    `<tr><td>${esc(c.name)}<div class="sub muted">${esc(c.description)}</div></td><td class="num">${c.flagged}</td><td class="mono">${esc(c.examples[0] || "")}</td></tr>`).join("")}</tbody></table></div>
    <p class="muted">Flags here are the labelled anomalies planted in the seed data (see ground_truth/anomalies.csv); new API writes add none.</p>`;
}));

boot().catch((e) => { $("#health").textContent = "API unreachable"; toast(e.message, true); });
