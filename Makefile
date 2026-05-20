# Optional .env for LLM URLs etc. (honored if present; not required for pip-cache build)
ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: help up down build rebuild logs restart ps pip-cache pip-cache-check clean

help:
	@echo "  make pip-cache          Download wheels to pip-cache/<arch> (online, once per machine)"
	@echo "  make build && make up   Offline docker build + start at http://localhost:8600"
	@echo "  make rebuild            Full --no-cache rebuild + up"
	@echo "  make restart            down + up without rebuild"
	@echo "  make logs               Tail logs"
	@echo "  make ps                 Container status"
	@echo "  make clean              Remove containers/volumes; prune images"
	@echo ""
	@echo "Inside Docker, reach host Ollama at: http://host.docker.internal:11434/v1"

up:
	docker compose up -d

down:
	docker compose down --remove-orphans

build: pip-cache-check
	docker compose build

rebuild: pip-cache-check
	docker compose build --no-cache
	docker compose up -d

restart:
	docker compose down --remove-orphans
	docker compose up -d

logs:
	@echo "  App running at: http://localhost:8600"
	docker compose logs -f

ps:
	docker compose ps

pip-cache:
	@chmod +x scripts/pip-cache-download.sh scripts/pip-cache-check.sh
	@./scripts/pip-cache-download.sh

pip-cache-check:
	@chmod +x scripts/pip-cache-check.sh
	@./scripts/pip-cache-check.sh

clean:
	docker compose down --remove-orphans -v
	docker image prune -f
