# Benchmarks for the enterprise agent memory stack.
# Targets the ClickHouse cluster at $(CH_HTTP) (default: project cluster on 18123).

CH_HTTP ?= http://localhost:18123
CH_USER ?= default
CH_PASS ?= clickhouse
CH_DB ?= enterprise_memory

PY ?= python3
ITERATIONS ?= 50
WARMUP ?= 5

.PHONY: help check-cluster bench report diff clean

help:
	@echo "Benchmark targets:"
	@echo "  make check-cluster  - verify the cluster is reachable and has seeded rows"
	@echo "  make bench          - run every tool query $(ITERATIONS) times, capture system.query_log"
	@echo "  make report         - render results/latest.json into results/latest.md"
	@echo "  make diff           - compare latest run against baseline/results.reference.json"
	@echo "  make clean          - remove results/*.json (keeps baseline)"

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
