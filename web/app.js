"use strict";
// Presentation tier. No business rules here: every decision shown comes from the API.

const state = { role: "customer", customer: null, statusFilter: "RECEIVED", policy: null, rma: null };
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
const money = (p) => (p == null ? "—" : inr.format(p / 100));
const fmtTs = (t) => (t ? String(t).replace("T", " ").slice(0, 16) : "—");
const nice = (s) => String(s ?? "").replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());

const ROLE_ACTOR = { customer: null, operator: "OPR-007", analyst: "ANL-001" };
const STATUS_TONE = { COMPLETED: "ok", REJECTED: "bad", SHIPMENT_LOST: "bad", EXPIRED: "warn", AUTHORIZED: "info",
  IN_TRANSIT: "info", RECEIVED: "warn", INSPECTED: "warn", REQUESTED: "", SUCCEEDED: "ok",
  REJECTED_DUPLICATE: "bad", REJECTED_EXCEEDS_PAID: "bad", APPROVED: "ok", PARTIALLY_APPROVED: "warn",
  RESTOCK: "ok", REFURBISH: "info", QUARANTINE: "warn", SCRAP: "bad" };
const badge = (s) => `<span class="badge ${STATUS_TONE[s] || ""}">${esc(nice(s))}</span>`;

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const h = { "X-Role": state.role === "analyst" ? "analyst" : state.role, ...headers };
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
    ({ customer: loadCustomer, operator: loadQueue, analyst: loadAnalytics })[state.role]();
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
      <td>${!l.returnable_category ? badge("Not returnable") : l.within_window ? `<span class="badge ok">${l.window_days - l.days_since_delivery} days left</span>` : `<span class="badge warn">closed ${l.days_since_delivery - l.window_days}d ago</span>`}</td>
      <td class="num">${money(l.line_paid_paise)}<div class="sub muted">${l.quantity} × ${money(l.unit_price_paise)}</div></td>
      <td class="num">${l.returnable_qty} / ${l.quantity}</td>
      <td><input type="number" min="0" max="99" value="0" data-line="${esc(l.order_line_id)}"></td>
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
    <time>${fmtTs(e.occurred_at)}</time><b>${esc(nice(e.entity_type))}</b> → ${esc(nice(e.to_state))}
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
  panel.innerHTML = `
    <div class="head"><h2>${esc(r.rma_id)}</h2><div>${badge(r.status)} ${r.override_flag ? badge("override") : ""}</div></div>
    <div class="facts"><span>Order <b>${esc(r.order_id)}</b></span><span>Customer <b>${esc(r.customer_id)}</b></span>
      <span>Type <b>${esc(nice(r.request_type))}</b></span><span>Decided by <b>${esc(r.authorization.decided_by)}</b></span>
      ${r.override_flag ? `<span>Override <b>${esc(r.override_reason)}</b></span>` : ""}
      ${s ? `<span>Shipment <b>${esc(s.rts_id)}</b> ${badge(s.status)}</span>` : ""}
      <span>Refunded <b>${money(r.totals.refunded_paise)}</b></span>
      ${r.totals.blocked_attempts ? `<span>Blocked attempts <b>${r.totals.blocked_attempts}</b></span>` : ""}</div>
    ${s ? `<div class="actions">
        <button data-act="ship" ${s.status !== "LABEL_CREATED" ? "disabled" : ""}>Courier pick-up scan</button>
        <button data-act="receive" ${s.status !== "IN_TRANSIT" ? "disabled" : ""}>Receive at warehouse</button>
        <button data-act="lost" class="danger" ${s.status !== "IN_TRANSIT" ? "disabled" : ""}>Mark lost</button></div>` : ""}
    ${r.lines.map((l) => {
      const ins = l.inspection, d = l.disposition;
      const ok = l.refunds.find((x) => x.status === "SUCCEEDED");
      return `<div class="subcard">
        <div class="head"><div><b>${esc(l.product_name)}</b> <span class="muted mono">${esc(l.return_line_id)}</span></div>
          <div>${badge(l.decision)} <span class="badge">${esc(nice(l.resolution))}</span></div></div>
        <div class="facts"><span>${esc(nice(l.order_line.category))}</span><span>Reason <b>${esc(nice(l.reason_code))}</b></span>
          <span>Approved <b>${l.approved_qty}</b> of ${l.requested_qty} requested</span><span>Line paid <b>${money(l.order_line.line_paid_paise)}</b></span>
          ${l.rejection_reason ? `<span>${esc(nice(l.rejection_reason))}</span>` : ""}</div>
        ${l.approved_qty > 0 ? `
          <div class="row">${ins ? `Inspected: <b>${ins.received_qty}</b> received · ${badge(ins.grade)} ${ins.notes ? `<span class="muted">${esc(ins.notes)}</span>` : ""}` :
            `<span>Inspect:</span><input type="number" min="0" max="${l.approved_qty}" value="${l.approved_qty}" data-recv="${esc(l.return_line_id)}">
             <select data-grade="${esc(l.return_line_id)}">${grades.map((g) => `<option>${g}</option>`).join("")}</select>
             <button data-act="inspect" data-line="${esc(l.return_line_id)}" ${r.status !== "RECEIVED" ? "disabled" : ""}>Record inspection</button>`}</div>
          <div class="actions">
            ${d ? `<span>Disposition ${badge(d.disposition)} → ${esc(nice(d.to_location))}, sellable +${d.sellable_qty_delta}</span>` :
              `<button data-act="dispose" data-line="${esc(l.return_line_id)}" ${!ins || !ins.received_qty ? "disabled" : ""}>Decide disposition</button>`}
            ${r.request_type === "REFUND" ? `
              <button class="primary" data-act="refund" data-line="${esc(l.return_line_id)}" ${!ins || !ins.received_qty || ok ? "disabled" : ""}>Issue refund</button>
              <button data-act="retry" data-line="${esc(l.return_line_id)}" ${!ok ? "disabled" : ""} title="Same Idempotency-Key: returns the stored result">Retry (same key)</button>
              <button data-act="double" data-line="${esc(l.return_line_id)}" ${!ok ? "disabled" : ""} title="New key after a payout: blocked and recorded">Simulate double-submit</button>` : ""}
          </div>
          ${l.refunds.length ? `<table><thead><tr><th>Refund</th><th>Status</th><th class="num">Item</th><th class="num">Tax</th><th class="num">Fees</th><th class="num">Paid</th></tr></thead><tbody>
            ${l.refunds.map((x) => `<tr><td class="mono">${esc(x.refund_id)}<div class="sub muted">attempt ${x.attempt_no}</div></td><td>${badge(x.status)}</td>
              <td class="num">${money(x.item_amount_paise)}<div class="sub muted">grade ${x.grade_refund_bp / 100}%</div></td><td class="num">${money(x.tax_amount_paise)}</td>
              <td class="num">${money(x.restocking_fee_paise + x.shipping_fee_paise)}</td><td class="num"><b>${money(x.amount_paise)}</b></td></tr>`).join("")}</tbody></table>` : ""}
        ` : ""}
      </div>`;
    }).join("")}
    <h3>Audit trail</h3>${timeline(r.events)}`;

  panel.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", guard(async () => {
    const line = b.dataset.line, act = b.dataset.act;
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
      toast(status === 200 ? `Replay: returned stored ${data.refund_id}, nothing paid twice` : `${data.status}`);
    }
    await openRma(rmaId);
    loadQueue();
  })));
});

// ------------------------------------------------------------------ analyst
function bars(entries, { fmt = (v) => v, max, cls } = {}) {
  const m = max ?? Math.max(1, ...entries.map((e) => e[1]));
  return `<div class="bars">${entries.map(([k, v, extra]) => `<span>${esc(nice(k))}</span>
    <div class="track"><div class="fill ${cls ? cls(k) : ""}" style="width:${(100 * v) / m}%"></div></div><span class="val">${fmt(v, extra)}</span>`).join("")}</div>`;
}

const loadAnalytics = guard(async () => {
  const [{ data: m }, { data: ex }] = await Promise.all([api("/api/metrics"), api("/api/exceptions")]);
  const e = m.eligibility, rf = m.refunds, lc = m.lifecycle, inv = m.inventory;
  const blocked = Object.values(rf.blocked_attempts).reduce((a, b) => a + b, 0);
  const kpi = (v, l) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`;
  $("#kpis").innerHTML = [
    kpi(`${m.return_rate.units_requested_pct_of_sold}%`, "of units sold requested back"),
    kpi(`${((100 * e.units_approved) / e.units_requested).toFixed(1)}%`, "of requested units approved"),
    kpi(money(rf.total_refunded_paise).replace(/\.\d+$/, ""), `refunded over ${rf.payouts.toLocaleString("en-IN")} payouts`),
    kpi(blocked.toLocaleString("en-IN"), `bad payouts blocked (${money(rf.blocked_value_paise).replace(/\.\d+$/, "")})`),
    kpi(`${lc.median_request_to_refund_days} d`, "median request → refund"),
    kpi(inv.sellable_units_recovered.toLocaleString("en-IN"), "units back to sellable stock"),
  ].join("");
  const cats = Object.entries(m.return_rate.by_category).sort((a, b) => b[1].request_rate_pct - a[1].request_rate_pct);
  const nonReturnable = new Set(Object.entries(state.policy.categories).filter(([, c]) => !c.returnable).map(([k]) => k));
  $("#chart-cat").innerHTML = bars(cats.map(([k, v]) => [k, v.request_rate_pct, v]), { fmt: (v) => `${v}%`, cls: (k) => (nonReturnable.has(k) ? "excluded" : "") }) +
    `<p class="muted">Grey = excluded categories (requests still arrive and are rejected by policy).</p>`;
  $("#chart-disp").innerHTML = bars(Object.entries(inv.units_by_disposition).sort((a, b) => b[1] - a[1]), { fmt: (v) => `${v.toLocaleString("en-IN")} u` });
  $("#chart-rej").innerHTML = bars(Object.entries(e.rejection_reasons).sort((a, b) => b[1] - a[1]));
  $("#chart-status").innerHTML = bars(Object.entries(lc.rma_status).sort((a, b) => b[1] - a[1]));
  const rows = Object.entries(ex.counts).map(([k, n]) => `<tr><td>${esc(nice(k))}</td><td class="num">${n}</td><td class="muted">${
    esc((ex[k][0] && (ex[k][0].action || ex[k][0].rma_id || ex[k][0].refund_id || ex[k][0].inspection_id)) || "")}</td></tr>`).join("");
  $("#exceptions").innerHTML = `<table><thead><tr><th>Queue</th><th class="num">Items</th><th>Latest / action</th></tr></thead><tbody>${rows}</tbody></table>`;
});

$("#run-integrity").addEventListener("click", guard(async () => {
  $("#integrity").innerHTML = `<p class="muted">Running…</p>`;
  const { data } = await api("/api/integrity", { headers: { "X-Role": "manager" } });
  $("#integrity").innerHTML = `<table><thead><tr><th>Check</th><th class="num">Flagged</th><th>Example</th></tr></thead><tbody>${data.checks.map((c) =>
    `<tr><td>${esc(c.name)}<div class="sub muted">${esc(c.description)}</div></td><td class="num">${c.flagged}</td><td class="mono">${esc(c.examples[0] || "")}</td></tr>`).join("")}</tbody></table>
    <p class="muted">Flags here are the labelled anomalies planted in the seed data (see ground_truth/anomalies.csv); new API writes add none.</p>`;
}));

boot().catch((e) => { $("#health").textContent = "API unreachable"; toast(e.message, true); });
