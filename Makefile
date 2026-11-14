PY      ?= python3
SEED    ?= 20261022
SCALE   ?= full
DB_URL  ?= sqlite:///out/returns.db
CLOCK   ?= 2026-10-25T10:00:00

.PHONY: install test data starter validate reproduce db serve docs all clean

install:            ## install runtime + test dependencies
	$(PY) -m pip install -r requirements.txt

test:               ## unit, property, pipeline, mutation and API tests
	$(PY) -m pytest -q

starter:            ## small dataset (3k orders) — same generator, smaller scale
	$(PY) -m returns_platform generate --scale starter --seed $(SEED) --out out/starter

data:               ## full dataset (30k orders, 5k requests, 8k lines) + validation
	$(PY) -m returns_platform generate --scale $(SCALE) --seed $(SEED) --out out/$(SCALE)

validate:           ## re-run validation suite on an exported dataset
	$(PY) -m returns_platform validate --data out/$(SCALE)

reproduce:          ## generate twice, compare row counts + SHA-256
	$(PY) -m returns_platform reproduce --scale $(SCALE) --seed $(SEED) --report docs/reproducibility_$(SCALE).md

db: data            ## load dataset into the database (SQLite by default; set DB_URL for Postgres)
	$(PY) -m returns_platform load-db --data out/$(SCALE) --url $(DB_URL)

serve:              ## API + web UI on http://127.0.0.1:8000 (clock pinned just after the data snapshot)
	RETURNS_CLOCK=$(CLOCK) $(PY) -m returns_platform serve --url $(DB_URL)

docs:               ## regenerate data dictionary + DDL from the schema spec
	$(PY) -m returns_platform docs

all: test data reproduce db

clean:
	rm -rf out .pytest_cache .hypothesis
