# Load .env for proxy and mirror settings (HTTP_PROXY, PIP_INDEX_URL, etc.)
ifneq (,$(wildcard .env))
include .env
export
endif

BUILD_ARCH := $(shell uname -m)
export BUILD_ARCH

.PHONY: help up down build rebuild logs restart ps pip-cache clean

help:
	@echo "  make build && make up   Build image + start at http://localhost:8501"
	@echo "  make rebuild            Full --no-cache rebuild + up"
	@echo "  make restart            down + up without rebuild"
	@echo "  make logs               Tail logs"
	@echo "  make ps                 Container status"
	@echo "  make pip-cache          Download wheels to pip-cache/<arch> (proxy via .env)"
	@echo "  make clean              Remove containers/volumes; prune images"
	@echo ""
	@echo "Inside Docker, reach host Ollama at: http://host.docker.internal:11434/v1"

up:
	docker compose up -d

down:
	docker compose down --remove-orphans

build:
	docker compose build

rebuild:
	docker compose build --no-cache
	docker compose up -d

restart:
	docker compose down --remove-orphans
	docker compose up -d

logs:
	@echo "  App running at: http://localhost:8501"
	docker compose logs -f

ps:
	docker compose ps

pip-cache:
	@chmod +x scripts/pip-cache-download.sh
	@./scripts/pip-cache-download.sh

clean:
	docker compose down --remove-orphans -v
	docker image prune -f
