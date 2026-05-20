# Load .env for proxy and mirror settings (HTTP_PROXY, PIP_INDEX_URL, etc.)
ifneq (,$(wildcard .env))
include .env
export
endif

PIP_TRUSTED := \
	--trusted-host pypi.org \
	--trusted-host pypi.python.org \
	--trusted-host files.pythonhosted.org

.PHONY: help up down build rebuild logs restart ps pip-cache clean

help:
	@echo "  make build && make up   Build image + start at http://localhost:8501"
	@echo "  make rebuild            Full --no-cache rebuild + up"
	@echo "  make restart            down + up without rebuild"
	@echo "  make logs               Tail logs"
	@echo "  make ps                 Container status"
	@echo "  make pip-cache          Download wheels (uses HTTP_PROXY / PIP_INDEX_URL from .env)"
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
	@ARCH=$$(uname -m); \
	if [ "$$ARCH" = "arm64" ] || [ "$$ARCH" = "aarch64" ]; then \
	  PLAT="--platform manylinux_2_17_aarch64 --platform linux_aarch64"; \
	else \
	  PLAT="--platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform linux_x86_64"; \
	fi; \
	INDEX_ARG=""; \
	if [ -n "$${PIP_INDEX_URL:-}" ]; then INDEX_ARG="-i $$PIP_INDEX_URL"; fi; \
	pip download $$PLAT $$INDEX_ARG \
	  $(PIP_TRUSTED) \
	  --python-version 3.11 --implementation cp --abi cp311 \
	  --only-binary=:all: \
	  -r requirements.txt \
	  -d pip-cache/

clean:
	docker compose down --remove-orphans -v
	docker image prune -f
