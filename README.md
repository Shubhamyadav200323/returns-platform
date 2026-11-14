# Returns Management Platform

An end-to-end returns workflow (**eligibility → authorization → return shipment → inspection → refund → disposition**)
built as a three-tier application, with a seeded synthetic-data generator, labelled ground truth and a
validation suite scored against it.

| | |
|---|---|
| **Presentation** | `web/` — customer, operator and analyst views (served by the API, no build step) |
| **Application** | `returns_platform/` — FastAPI routes → transactional services → pure domain rule engines |
| **Data** | SQLite (zero setup) or PostgreSQL; CSV / JSON / SQL seed exports + ground truth |

Architecture, state machine and rule details: [`docs/architecture.md`](docs/architecture.md).
Every table and field: [`docs/data_dictionary.md`](docs/data_dictionary.md).

## Quick start

```bash
pip install -r requirements.txt
make test          # 63 tests: unit, property-based, pipeline, mutation, API end-to-end, edge cases
make data          # 30k orders / 5k requests / 8k lines + validation  (~15 s)
make reproduce     # generate twice, compare SHA-256 of every file
make db serve      # load into SQLite and open http://127.0.0.1:8000
```

PostgreSQL instead of SQLite: `make db serve DB_URL=postgresql+psycopg://user:pass@host/db`, or `docker compose up`.

Generator command (also recorded in `manifest.json`):

```bash
python -m returns_platform generate --scale full --seed 20261022 --out out/full
```

`--scale starter` (3k orders), `full` (30k) and `tiny` (400, for tests) run **the same generator**; only the counts differ.

## What the generator produces

Twelve tables, parents before children, every FK valid:

`customer` · `product` · `sales_order` · `order_line` · `return_request` · `return_line` · `return_authorization` ·
`return_shipment` · `inspection` · `refund` · `inventory_disposition` · `return_event`

IDs follow the brief and are immutable and zero-padded: `ORD-2026-000001`, `ORD-2026-000001-01` (line),
`RMA-2026-000001`, `RMAL-RMA-2026-000001-01`, `RTS-2026-000001`, `REF-2026-000001`, plus `AUTH-`, `INS-`, `DSP-`, `EVT-`.

**How outcomes are produced.** Randomness only drives *inputs*: what customers buy, which items they return, when,
how many and why, and what condition the items arrive in. Every *outcome* comes from the same rule engines the
API uses. That covers eligibility decisions, approved quantities, refund amounts, fees, dispositions and lifecycle
states. Order totals are sums of their lines, RMA status is the last event, and so on. No outcome is sampled.

**Coverage (full dataset):** 509 partial-quantity returns · 2,853 partial-order returns · 1,366 late requests
(446 approved with an attributed override) · 607 category exclusions · 273 over-requests clamped ·
100 follow-up RMAs on the same order · 1,316 exchanges · 668 damaged and 757 defective items · 94 lost and
147 never-shipped parcels · 197 short or empty receipts · 112 duplicate refund attempts and 25 over-payment
attempts blocked · 383 returns still in flight at the snapshot.

## Edge cases and ground truth

After the clean baseline is generated, the injector plants **40 of each** required issue (200 total). Each one touches
a single return line and is recorded in `ground_truth/anomalies.csv` with the original value, the injected value and
the detection rule:

| issue_type | what was planted | caught by |
|---|---|---|
| `RETURN_QTY_EXCEEDS_PURCHASED` | approved qty > purchased qty | Σ approved per order line > quantity |
| `REFUND_EXCEEDS_PAID` | payout > paid for the refunded units | amount > paid share for refund qty |
| `RESTOCK_BEFORE_INSPECTION` | RESTOCK timestamp before inspection | disposition.decided_at < inspected_at |
| `LATE_RETURN_WITHOUT_OVERRIDE` | approved after window, no override | days > window ∧ approved ∧ ¬override |
| `DUPLICATE_REFUND` | second successful payout on a line | > 1 SUCCEEDED refund per line |

`ground_truth/expected_outcomes.csv` holds the rule-derived expected decision, approved quantity, refund and
disposition for every return line. It's captured before injection. Ground truth lives only in `ground_truth/`, and a
test asserts that no label column appears in any input table.

The **live API blocks all five** of these (see the architecture doc for why they still exist in the dataset).

## Validation (`validation_summary.md`)

There are 79 checks: PK uniqueness, ID format, required fields, enums and ranges, FK integrity, unique keys, the price
waterfall, order total reconciliation, cross-entity links, date ordering, lifecycle event paths, state ↔ artefact
consistency, disposition-controls-inventory, refund row arithmetic, five anomaly detectors, and full **re-execution of
the eligibility and refund engines** on the stored records.

A check passes only when it flags **exactly** the records ground truth says it should. Integrity checks must flag
nothing, and detectors must flag every labelled anomaly of their type and nothing else. Results on the full dataset:

- **79 / 79 checks pass**
- anomaly detection: **320 true positives, 0 false positives, 0 false negatives** (precision 1.0, recall 1.0)
- eligibility decisions on non-anomalous lines agree **100%** with the expected outcomes
- mutation tests show the suite also catches tampering it was *not* told about: 1-paisa changes, a broken FK,
  status not matching events, disposition ignoring grade, refund before inspection, duplicate PKs

## Edge cases tested beyond the brief

`tests/test_edge_cases.py` probes cases nobody listed:

- a line returned in pieces across two RMAs reconciles to the exact paisa paid
- the return-shipping fee is charged once per RMA and never on merchant-fault lines
- partial receipts refund only what arrived; an empty parcel closes with no refund and no stock movement
- shipping after the ship-by date expires the RMA; a lost parcel can't later be received
- a request before delivery is refused; a mixed RMA (one excluded line) completes on its approved lines only
- an exchange can't be refunded; an idempotency key can't be reused on another line; malformed input is rejected
- **8 simultaneous refund calls produce exactly one payout and seven recorded duplicates** (SQLite and PostgreSQL)
- the generator produces a fully valid dataset for five arbitrary seeds, not just the default

## Reproducibility

Each generation stage has its own SHA-256-derived RNG stream from the single seed `20261022`, so changing the
anomaly volume doesn't move the baseline (that's tested too). `make reproduce` generates twice in clean
directories and compares the row count and SHA-256 of all 32 output files. The result is identical at both scales:
see [`docs/reproducibility_full.md`](docs/reproducibility_full.md). `manifest.json` records the command, seed,
settings, policy file hash, Python version and a checksum for every file.

## Provenance and lawful use

All data is synthetic. No external dataset was used. Names are random combinations of generic first and last names,
and emails use the reserved `example.com` domain. See [`docs/SOURCE_MANIFEST.md`](docs/SOURCE_MANIFEST.md).

## Repository layout

```
config/return_policy.yaml        all business values (windows, exclusions, fees, grade factors, disposition map)
returns_platform/
  domain/                        pure rule engines: money, pricing, eligibility, refund, disposition, lifecycle
  schema/                        spec.py (single source of truth) → ddl.py, dictionary.py
  generator/                     settings (scales, seed streams), baseline, anomalies, pipeline
  validation/                    checks (scored vs ground truth), report + manifest
  export/io.py                   CSV / JSON / SQL writers and typed loader
  analytics/metrics.py           KPIs + scenario coverage
  db.py                          data-tier access (SQLite/Postgres), bulk loader
  api/services.py, api/app.py    application services + HTTP routes
  cli.py                         generate · validate · reproduce · load-db · serve · docs
web/                             presentation tier
db/                              generated DDL (sqlite, postgres)
docs/                            architecture, data dictionary, reproducibility reports, source manifest
tests/                           domain, pipeline + mutation, API end-to-end
```

## API

| Method | Path | Role |
|---|---|---|
| GET | `/api/orders/{id}` (with returnable qty and window per line), `/api/returns`, `/api/returns/{rma}` | any |
| POST | `/api/returns` (operators may add an `override`) | customer, operator |
| POST | `/api/shipments/{rts}/ship` · `/receive` · `/lost` | customer (ship), operator |
| POST | `/api/return-lines/{id}/inspection` · `/disposition` | operator |
| POST | `/api/return-lines/{id}/refunds` + `Idempotency-Key` header | operator |
| GET | `/api/metrics`, `/api/exceptions`, `/api/integrity` | analyst, manager |

Interactive docs at `/docs`. Errors use one shape: `{"error": "NOT_INSPECTED", "message": "..."}`.

## Known limitations

- Authentication is a header stand-in (`X-Role`, `X-Actor`), isolated in one function so real auth can replace it.
- Approved quantity on an expired or lost RMA isn't released for a new return; the customer goes through support.
- The Docker files are provided but weren't run in the build environment. SQLite and PostgreSQL were both tested directly.
- The demo server pins `RETURNS_CLOCK` to just after the data snapshot (2026-10-22), so return windows look live.
