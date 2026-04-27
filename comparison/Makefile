# Comparison exhibit: stitched stack vs ClickHouse.
#
# Targets:
#   make clickhouse   Run the ClickHouse agent against the cookbooks container.
#   make stitched     Print the up-front note, then run the stitched agent.
#   make compare      Print the structural comparison table.

HERE := $(shell pwd)
COOKBOOKS := $(HERE)/../cookbooks

PYTHON ?= python3
CLICKHOUSE_HOST ?= localhost
CLICKHOUSE_PORT ?= 8123

.PHONY: clickhouse stitched compare help

help:
	@echo "Targets:"
	@echo "  make clickhouse   -- run the ClickHouse agent (requires cookbooks stack up)"
	@echo "  make stitched     -- run the stitched agent (optional: docker compose up -d first)"
	@echo "  make compare      -- print the structural comparison"

clickhouse:
	@echo ">> Running ClickHouse agent against $(CLICKHOUSE_HOST):$(CLICKHOUSE_PORT)"
	@echo ">> If this fails, run 'cd $(COOKBOOKS) && make start seed' first."
	CLICKHOUSE_HOST=$(CLICKHOUSE_HOST) CLICKHOUSE_PORT=$(CLICKHOUSE_PORT) \
		$(PYTHON) $(HERE)/clickhouse/agent.py

stitched:
	@echo ">> Stitched stack requires four services."
	@echo ">> To run against real services first do:"
	@echo ">>   docker compose -f $(HERE)/stitched/docker-compose.yml up -d"
	@echo ">>   pip install -r $(HERE)/stitched/requirements.txt"
	@echo ">> Without them, agent.py falls back to in-memory doubles."
	$(PYTHON) $(HERE)/stitched/agent.py

compare:
	$(PYTHON) $(HERE)/compare.py
