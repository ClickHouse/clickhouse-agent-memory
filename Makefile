.PHONY: help demo setup network cli-up cli-down cli-seed cli-run librechat-up librechat-down all-up all-down status clean test verify

NETWORK := enterprise_memory_net
# Published host ports. Override by exporting before make, e.g.
#   CH_HTTP_PORT=8123 LIBRECHAT_PORT=3080 make demo
CH_HTTP_PORT ?= 18123
MCP_PORT ?= 18765
LIBRECHAT_PORT ?= 13800

help:
	@echo "Enterprise Agent Memory -- orchestration targets"
	@echo ""
	@echo "  demo             One-command quickstart: setup + network + up + seed + LibreChat"
	@echo "  verify           Sanity-check a running demo (tables, tools, MCP, UI)"
	@echo "  test             Run pytest (unit always, integration when CH reachable)"
	@echo ""
	@echo "  setup            Create .env files for both cookbooks/ and librechat/"
	@echo "  network          Create the shared external docker network"
	@echo ""
	@echo "  cli-up           Start ClickHouse + demo-app"
	@echo "  cli-seed         Seed ClickHouse with synthetic data (17 tables + 3 conv memory)"
	@echo "  cli-run          Run all three CLI cookbook demos"
	@echo "  cli-down         Stop cookbook stack (keep data)"
	@echo ""
	@echo "  librechat-up     Start LibreChat + MongoDB + Meilisearch + memory-mcp"
	@echo "  librechat-down   Stop LibreChat stack (keep data)"
	@echo ""
	@echo "  all-up           Bring up cookbooks, seed, then LibreChat in one go"
	@echo "  all-down         Stop everything"
	@echo "  status           Show running containers on the shared network"
	@echo "  clean            Stop everything and drop all data volumes"
	@echo ""
	@echo "Published ports (override via env):"
	@echo "  ClickHouse HTTP:  $(CH_HTTP_PORT)  (CH_HTTP_PORT)"
	@echo "  memory-mcp:       $(MCP_PORT)  (MCP_PORT)"
	@echo "  LibreChat UI:     $(LIBRECHAT_PORT)  (LIBRECHAT_PORT)"

# One-command quickstart. Designed for under 90 seconds on a warm machine.
demo: setup network
	@echo ""
	@echo "=== Enterprise Agent Memory -- one command demo ==="
	@echo ""
	@echo "[1/5] Starting ClickHouse + demo-app ..."
	@$(MAKE) -C cookbooks start >/dev/null
	@echo "[2/5] Waiting for ClickHouse HTTP on :$(CH_HTTP_PORT) ..."
	@until curl -fsS -u default:clickhouse "http://localhost:$(CH_HTTP_PORT)/?query=SELECT+1" >/dev/null 2>&1; do sleep 1; done
	@echo "[3/5] Seeding 20 tables (3 domains + conversation memory) ..."
	@$(MAKE) -C cookbooks seed >/dev/null
	@echo "[4/5] Starting LibreChat + MongoDB + Meilisearch + memory-mcp ..."
	@$(MAKE) -C librechat start >/dev/null
	@echo "[5/5] Waiting for LibreChat on :$(LIBRECHAT_PORT) ..."
	@until curl -fsS "http://localhost:$(LIBRECHAT_PORT)/health" >/dev/null 2>&1 || curl -fsS "http://localhost:$(LIBRECHAT_PORT)/" >/dev/null 2>&1; do sleep 2; done
	@echo ""
	@echo "Ready."
	@echo "  LibreChat:  http://localhost:$(LIBRECHAT_PORT)"
	@echo "  memory-mcp: http://localhost:$(MCP_PORT)/mcp"
	@echo "  ClickHouse: http://localhost:$(CH_HTTP_PORT)   (user=default pass=clickhouse)"
	@echo ""
	@echo "Next:"
	@echo "  open http://localhost:$(LIBRECHAT_PORT)           # register, pick a preset"
	@echo "  make test                                         # full test suite"
	@echo "  make verify                                       # sanity check everything"

# End-to-end sanity check on a running demo.
verify:
	@echo "Verifying running demo ..."
	@echo -n "  ClickHouse HTTP (:$(CH_HTTP_PORT)): "
	@curl -fsS -u default:clickhouse "http://localhost:$(CH_HTTP_PORT)/?query=SELECT+count()+FROM+enterprise_memory.obs_events_stream" | awk '{print "ok (" $$1 " hot events)"}'
	@echo -n "  Schema tables: "
	@curl -fsS -u default:clickhouse "http://localhost:$(CH_HTTP_PORT)/?query=SELECT+count()+FROM+system.tables+WHERE+database%3D%27enterprise_memory%27" | awk '{print "ok (" $$1 " tables)"}'
	@echo -n "  Conversation memory seeded: "
	@curl -fsS -u default:clickhouse "http://localhost:$(CH_HTTP_PORT)/?query=SELECT+count()+FROM+enterprise_memory.agent_memory_long" | awk '{print "ok (" $$1 " WARM rows)"}'
	@echo -n "  memory-mcp (:$(MCP_PORT)): "
	@if curl -fsS -m 3 "http://localhost:$(MCP_PORT)/mcp" >/dev/null 2>&1 || curl -fsS -m 3 -X POST "http://localhost:$(MCP_PORT)/mcp" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"verify","version":"0"}},"id":1}' >/dev/null 2>&1; then echo "ok"; else echo "not reachable"; fi
	@echo -n "  LibreChat (:$(LIBRECHAT_PORT)): "
	@if curl -fsS -m 3 "http://localhost:$(LIBRECHAT_PORT)/" >/dev/null 2>&1; then echo "ok"; else echo "not reachable (may still be booting)"; fi
	@echo ""
	@echo "Running integration test suite against the live stack ..."
	@CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=$(CH_HTTP_PORT) CLICKHOUSE_PASSWORD=clickhouse python3 -m pytest tests/integration/ -q 2>&1 | tail -12

test:
	@echo "== Unit tests (no DB) =="
	@python3 -m pytest tests/unit/ -q 2>&1 | tail -3
	@echo ""
	@echo "== Integration tests (needs make demo first) =="
	@CLICKHOUSE_HOST=localhost CLICKHOUSE_PORT=$(CH_HTTP_PORT) CLICKHOUSE_PASSWORD=clickhouse python3 -m pytest tests/integration/ -q 2>&1 | tail -5

setup:
	$(MAKE) -C cookbooks setup
	$(MAKE) -C librechat setup

network:
	@docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)

# -- CLI cookbook stack ------------------------------------------------------

cli-up: network
	$(MAKE) -C cookbooks start

cli-seed:
	$(MAKE) -C cookbooks seed

cli-run:
	$(MAKE) -C cookbooks run

cli-down:
	$(MAKE) -C cookbooks stop

# -- LibreChat stack ---------------------------------------------------------

librechat-up: network
	$(MAKE) -C librechat start

librechat-down:
	$(MAKE) -C librechat stop

# -- Combined ---------------------------------------------------------------

all-up: network
	$(MAKE) -C cookbooks start
	@echo "Waiting 8s for ClickHouse to become ready ..."
	@sleep 8
	$(MAKE) -C cookbooks seed
	$(MAKE) -C librechat start
	@echo ""
	@echo "Everything is up."
	@echo "  CLI demos:    docker compose -f cookbooks/docker-compose.yml exec demo-app python main.py run-all"
	@echo "  LibreChat UI: http://localhost:3080"

all-down:
	$(MAKE) -C librechat stop
	$(MAKE) -C cookbooks stop

status:
	@docker ps --filter network=$(NETWORK) --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"

clean:
	$(MAKE) -C librechat clean
	$(MAKE) -C cookbooks clean
	@docker network rm $(NETWORK) 2>/dev/null || true
	@echo "All volumes and the shared network removed"
