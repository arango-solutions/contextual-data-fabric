# Contextual Data Fabric — one-command demo + gates (WP-P1.7 / CC-8).
#
#   make install  create .venv and install the engine + live-leg libraries
#   make up       bring up both stacks (ArangoDB; Postgres + Ontop)
#   make seed     load the corpus (structured -> Postgres, unstructured ->
#                 ArangoDB) + emit the mappings
#   make gate     the pre-demo golden gate — run before ANY demo (PJ's rule)
#   make demo     up + seed + gate, then serve the browser demo at :8099
#   make test     lint + types + unit suite (what CI runs)
#   make down     stop the stacks (volumes preserved)
#
# With Docker, Python, and Snowflake .env configured: make install && make demo

# Host ports — overridable; defaults match this machine's running stacks.
export CDF_ARANGO_PORT   ?= 8530
export CDF_POSTGRES_PORT ?= 5433
# 8090 is not free on this machine: dalio-bff binds 127.0.0.1:8090, and an
# explicit loopback bind wins over Docker's wildcard, so the stack comes up
# healthy and every SPARQL call 404s against the wrong server. 18090 is what
# tools/run_workbench.sh already assumed.
export CDF_ONTOP_PORT    ?= 18090
export CDF_UI_PORT       ?= 8099
export CDF_CLICKHOUSE_HTTP_PORT ?= 8123

# The owned SPARQL->AQL library defaults to the reviewed CC-9 pin. Developers
# may opt into an editable sibling checkout with CDF_USE_LOCAL_SIBLINGS=1.
CDF_SIBLINGS ?= $(HOME)/code
CDF_USE_LOCAL_SIBLINGS ?= 0
ARANGO_SPARQL_PIN ?= deploy/pins/arango-sparql-py.txt

# Engine environment (CC-7: credentials stay here, in the engine's env).
DEMO_ENV = ARANGO_URL=http://127.0.0.1:$(CDF_ARANGO_PORT) ARANGO_DB=cmf \
           ARANGO_USER=root ARANGO_PASSWORD=cdf \
           ONTOP_SPARQL_ENDPOINT=http://127.0.0.1:$(CDF_ONTOP_PORT)/sparql \
           ONTOP_REFORMULATE_ENDPOINT=http://127.0.0.1:$(CDF_ONTOP_PORT)/ontop/reformulate \
           CLICKHOUSE_DSN=clickhouse://cdf:cdf@127.0.0.1:$(CDF_CLICKHOUSE_HTTP_PORT)/analytics \
           CDF_CSI_DIR=deploy/csi CDF_R2RML_DIR=deploy/r2rml \
           CDF_PREPARED_QUESTIONS=deploy/questions.json CDF_STRICT_STARTUP=true

# Snowflake creds live in the gitignored .env (CC-7). Recipes that touch the
# Snowflake leg source it so SNOWFLAKE_* reach gate.py / load_corpus.py, which
# read them from the environment (server.py loads .env itself).
# `.` is a POSIX special builtin: when it fails, a non-interactive shell exits
# — and `|| true` does NOT rescue it (bash in POSIX mode, which is /bin/sh on
# macOS, honours that). So the obvious `. ./.env 2>/dev/null || true` made every
# recipe using it exit 1 having run nothing at all, silently, whenever .env was
# absent. `make gate` on a fresh clone printed no output and failed. Test for
# the file instead of catching the failure.
LOAD_ENV = if [ -f ./.env ]; then set -a; . ./.env; set +a; fi;

PY = .venv/bin/python
CK25_EVIDENCE ?= docs/evidence/ck25-gpt-4o-mini-3x.json

.PHONY: install up seed gate demo test optimizer-oracle performance-baseline ck25-live sota-baseline sota-baseline-live catalog-integrity authorization-golden down jdbc free-ui

install:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	# clickhouse-connect is lazy-imported by the ClickHouse adapter, but g7 is a
	# gate case — without it that leg silently contributes nothing and the live
	# test skips rather than fails, so `make gate` cannot pass from a fresh clone.
	$(PY) -m pip install -e ".[test,service,mcp,auth,dev]" "psycopg[binary]" python-arango snowflake-connector-python clickhouse-connect
	# Owned library: reviewed pin by default; editable sibling only by opt-in.
	# The [nl] extra pulls the NL engine (arango-query-core + openai/anthropic) so
	# the free-form NL front-end is wired — without it default_client() degrades to
	# prepared-questions-only. arango-sparql-py main is self-consistent with its
	# pinned arango-query-core: the grounding seams 6/7 import the newer symbols
	# (LabelIndex/PredicateIndex) only under TYPE_CHECKING, so [nl] installs and
	# runs on the pinned version — no query-core override needed (retired 2026-08-04
	# once the grounding branch landed on main, arango-sparql-py@623aa24).
	@if [ "$(CDF_USE_LOCAL_SIBLINGS)" = "1" ]; then \
	  $(PY) -m pip install -e "$(CDF_SIBLINGS)/arango-sparql-py[nl,analyzer]"; \
	else \
	  $(PY) -m pip install -r "$(ARANGO_SPARQL_PIN)"; \
	fi
	@echo "OK — now: make demo   (Docker must be running)"

jdbc: deploy/ontop/jdbc/postgresql.jar
deploy/ontop/jdbc/postgresql.jar:
	curl -sL -o $@ https://jdbc.postgresql.org/download/postgresql-42.7.4.jar

up: jdbc
	docker compose -p cdf-arango -f deploy/arango/docker-compose.yml up -d --wait
	docker compose -p cdf-ontop  -f deploy/ontop/docker-compose.yml  up -d --wait
	# ClickHouse self-seeds via docker-entrypoint-initdb.d/seed.sql on first
	# create (fresh container each up-after-down), so no `seed` step is needed.
	docker compose -p cdf-clickhouse -f deploy/clickhouse/docker-compose.yml up -d --wait

seed:
	PG_DSN=postgresql://cdf:cdf@127.0.0.1:$(CDF_POSTGRES_PORT)/crm $(PY) deploy/ontop/load_corpus.py
	$(LOAD_ENV) $(PY) deploy/snowflake/load_corpus.py   # telemetry -> Snowflake USAGE_METRICS (46 rows)
	docker compose -p cdf-ontop -f deploy/ontop/docker-compose.yml restart ontop
	$(DEMO_ENV) $(PY) deploy/arango/seed.py           # tickets (kept as a small typed collection)
	$(DEMO_ENV) $(PY) deploy/arango/load_corpus.py    # documents + chunks (account_id stamp)
	$(DEMO_ENV) $(PY) deploy/arango/export_csi.py     # reverse CSI over the live graph
	@sleep 12  # Ontop reloads the R2RML mapping on start

gate:
	$(LOAD_ENV) $(DEMO_ENV) $(PY) deploy/demo/gate.py

free-ui:
	@pids=$$(lsof -ti tcp:$(CDF_UI_PORT) 2>/dev/null); \
	if [ -n "$$pids" ]; then echo "freeing port $(CDF_UI_PORT) (was: $$pids)"; kill $$pids 2>/dev/null; sleep 1; fi

demo: up seed gate free-ui
	$(DEMO_ENV) $(PY) deploy/demo/server.py

catalog-integrity:
	@tmp=$$(mktemp); trap 'rm -f "$$tmp"' EXIT; \
	  $(PY) -m cdf.catalog.cli build --root . --output "$$tmp" >/dev/null; \
	  diff -u deploy/catalog/manifest.json "$$tmp"
	$(PY) -m cdf.catalog.cli validate --root . deploy/catalog/manifest.json

authorization-golden:
	$(PY) -m pytest tests/test_governance.py -q

test: catalog-integrity authorization-golden
	.venv/bin/ruff check src tests deploy
	.venv/bin/mypy src
	$(PY) -m pytest tests -q

optimizer-oracle:
	@$(PY) -m cdf.eval.optimizer_oracle

performance-baseline:
	@$(PY) -m cdf.eval.performance_baseline

ck25-live:
	@$(PY) -m cdf.eval.ck25_eval --repetitions 3 --resume --output "$(CK25_EVIDENCE)"

sota-baseline:
	@$(PY) -m cdf.eval.sota_scorecard

sota-baseline-live:
	@$(PY) -m cdf.eval.sota_scorecard --live

down:
	docker compose -p cdf-arango -f deploy/arango/docker-compose.yml down
	docker compose -p cdf-ontop  -f deploy/ontop/docker-compose.yml  down
	docker compose -p cdf-clickhouse -f deploy/clickhouse/docker-compose.yml down
