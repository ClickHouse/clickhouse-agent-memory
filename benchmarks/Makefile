# Benchmarks for the enterprise agent memory stack.
#
# Two modes:
#   - Default: target the cookbook cluster at $(CH_HTTP) (port 18123, demo scale).
#   - Bench-scale: `make up && make seed` brings up an isolated cluster on
#     port 18124 with 100k events / 5k incidents. Then run `make bench
#     CH_HTTP=http://localhost:18124` against it.

CH_HTTP ?= http://localhost:18123
CH_USER ?= default
CH_PASS ?= clickhouse
CH_DB ?= enterprise_memory

# Bench-scale isolated cluster (separate from cookbook).
BENCH_CH_HTTP ?= http://localhost:18124
BENCH_CH_HOST ?= localhost
BENCH_CH_PORT ?= 18124
BENCH_CH_USER ?= default
BENCH_CH_PASS ?= clickhouse
BENCH_CH_DB   ?= enterprise_memory

PY ?= python3
ITERATIONS ?= 50
WARMUP ?= 5

.PHONY: help check-cluster bench report diff clean up seed down check seed-counts

help:
	@echo "Benchmark targets:"
	@echo ""
	@echo "Demo-scale (against cookbook cluster on :18123):"
	@echo "  make check-cluster  - verify the cluster is reachable and has seeded rows"
	@echo "  make bench          - run every tool query $(ITERATIONS) times, capture system.query_log"
	@echo "  make report         - render results/latest.json into results/latest.md"
	@echo "  make diff           - compare latest run against baseline/results.reference.json"
	@echo "  make clean          - remove results/*.json (keeps baseline)"
	@echo ""
	@echo "Bench-scale (isolated cluster on :18124, 100k events / 5k incidents):"
	@echo "  make up             - bring up the isolated bench ClickHouse"
	@echo "  make seed           - populate it with deterministic bench-scale data"
	@echo "  make check          - row counts and HNSW status on the bench cluster"
	@echo "  make down           - stop and remove the bench cluster (keeps volume)"
	@echo ""
	@echo "Then to run the harness against bench-scale:"
	@echo "  make bench CH_HTTP=\$$(BENCH_CH_HTTP)"

check-cluster:
	@echo ">>> Cluster reachability + seed counts"
	@curl -sS -u $(CH_USER):$(CH_PASS) "$(CH_HTTP)/?query=SELECT%20version()" || (echo "cluster unreachable at $(CH_HTTP)"; exit 1)
	@curl -sS -u $(CH_USER):$(CH_PASS) "$(CH_HTTP)/?database=$(CH_DB)&query=$$($(PY) -c 'import urllib.parse; print(urllib.parse.quote("SELECT name, total_rows FROM system.tables WHERE database = currentDatabase() AND engine IN (\\'MergeTree\\',\\'Memory\\') ORDER BY total_rows DESC FORMAT PrettyCompact"))')"

bench:
	@mkdir -p results
	@CH_HTTP="$(CH_HTTP)" CH_USER="$(CH_USER)" CH_PASS="$(CH_PASS)" CH_DB="$(CH_DB)" \
	 $(PY) harness/run_bench.py --iterations $(ITERATIONS) --warmup $(WARMUP) --out results/latest.json
	@echo ">>> run complete. render: make report"

report:
	@$(PY) harness/render_report.py --run results/latest.json --out results/latest.md
	@echo ">>> report: results/latest.md"
	@cat results/latest.md

diff:
	@$(PY) harness/render_report.py --run results/latest.json --baseline baseline/results.reference.json --out results/diff.md
	@cat results/diff.md

clean:
	@rm -f results/latest.json results/latest.md results/diff.md
	@echo ">>> cleaned results/"

up:
	@echo ">>> Bringing up isolated bench ClickHouse on $(BENCH_CH_HTTP)"
	@docker compose -f docker-compose.yml up -d --wait
	@echo ">>> Ready: $(BENCH_CH_HTTP)"

seed:
	@echo ">>> Seeding bench-scale dataset (100k events / 5k incidents / 40 services / 120 edges)"
	@CH_HOST=$(BENCH_CH_HOST) CH_PORT=$(BENCH_CH_PORT) CH_USER=$(BENCH_CH_USER) CH_PASS=$(BENCH_CH_PASS) CH_DB=$(BENCH_CH_DB) \
	 $(PY) seed/02_seed.py

check:
	@echo ">>> Row counts on bench cluster ($(BENCH_CH_HTTP))"
	@curl -sS -u $(BENCH_CH_USER):$(BENCH_CH_PASS) "$(BENCH_CH_HTTP)/?database=$(BENCH_CH_DB)" --data \
	 "SELECT name, total_rows, formatReadableSize(total_bytes) AS size FROM system.tables WHERE database = '$(BENCH_CH_DB)' AND engine IN ('MergeTree','Memory') ORDER BY total_rows DESC FORMAT PrettyCompact"
	@echo ""
	@echo ">>> Vector + skip indices on bench cluster"
	@curl -sS -u $(BENCH_CH_USER):$(BENCH_CH_PASS) "$(BENCH_CH_HTTP)/?database=$(BENCH_CH_DB)" --data \
	 "SELECT table, name, type FROM system.data_skipping_indices WHERE database = '$(BENCH_CH_DB)' ORDER BY table, name FORMAT PrettyCompact"

down:
	@docker compose -f docker-compose.yml down
	@echo ">>> bench cluster stopped (volume preserved)"
