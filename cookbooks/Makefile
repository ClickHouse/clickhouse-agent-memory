.PHONY: setup network start stop seed run logs clean status

COMPOSE := docker compose
NETWORK := enterprise_memory_net

# Create .env from template
setup:
	@if [ ! -f .env ]; then \
		cp .env.local.example .env; \
		echo "Created .env from template"; \
	else \
		echo ".env already exists -- skipping"; \
	fi
	@echo "Add your provider API keys to .env, then run: make start"

# Ensure the shared external network exists so LibreChat can attach later.
network:
	@docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)

# Start the full stack
start: network
	$(COMPOSE) up -d --build
	@echo ""
	@echo "Stack is starting -- run 'make logs' to follow"
	@echo "Once healthy, run: make seed && make run"

# Stop all services (keep data)
stop:
	$(COMPOSE) down

# Seed ClickHouse with demo data
seed:
	$(COMPOSE) exec demo-app python main.py seed

# Run all cookbook demos
run:
	$(COMPOSE) exec demo-app python main.py run-all

# Run a single cookbook demo (usage: make run-one COOKBOOK=observability)
run-one:
	$(COMPOSE) exec demo-app python main.py run $(COOKBOOK)

# Follow container logs
logs:
	$(COMPOSE) logs -f

# Show service status
status:
	$(COMPOSE) ps

# Stop and remove all data volumes
clean:
	$(COMPOSE) down -v
	@echo "All data volumes removed"
