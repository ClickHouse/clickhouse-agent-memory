.PHONY: setup network start stop logs status clean restart

COMPOSE := docker compose
NETWORK := enterprise_memory_net
LIBRECHAT_PORT ?= 13800

# Create .env if missing
setup:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from template -- add GOOGLE_KEY (or other provider keys) and run 'make start'"; \
	else \
		echo ".env already exists -- skipping"; \
	fi

# Ensure the shared external network exists before bringing anything up.
network:
	@docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)

# Start LibreChat + MongoDB + Meilisearch + memory-mcp.
start: setup network
	$(COMPOSE) up -d --build
	@echo ""
	@echo "LibreChat will be available at http://localhost:$(LIBRECHAT_PORT)"
	@echo "First visit: register an account, then:"
	@echo "  1. Pick a preset from the dropdown (AI SRE / AI NetOps / AI SOC / Support Copilot), OR"
	@echo "  2. Open the Agents endpoint and create an agent from librechat/agents/*.md"

stop:
	$(COMPOSE) down

restart:
	$(COMPOSE) down
	$(COMPOSE) up -d --build

logs:
	$(COMPOSE) logs -f

status:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v
	@echo "LibreChat data volumes removed"
