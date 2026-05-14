.PHONY: help up down build rebuild logs restart ps \
        prod-up prod-down prod-logs pip-cache clean

help:
	@echo "Dev (port 8601):"
	@echo "  make build && make up   Build image + start dev container"
	@echo "  make rebuild            Full --no-cache rebuild + up"
	@echo "  make restart            down + up without rebuild"
	@echo "  make logs               Tail dev logs"
	@echo "  make ps                 Container status"
	@echo ""
	@echo "Prod (port 8600, explicit):"
	@echo "  make prod-up            Build + start prod container"
	@echo "  make prod-down          Stop prod container"
	@echo "  make prod-logs          Tail prod logs"
	@echo ""
	@echo "Maintenance:"
	@echo "  make pip-cache          Download wheels for offline builds"
	@echo "  make clean              Remove containers/volumes; prune images"
	@echo ""
	@echo "Inside Docker, reach host Ollama at: http://host.docker.internal:11434/v1"

up:
	docker compose --profile dev up -d

down:
	docker compose --profile dev down --remove-orphans

build:
	docker compose --profile dev build

rebuild:
	docker compose --profile dev build --no-cache
	docker compose --profile dev up -d

restart:
	docker compose --profile dev down --remove-orphans
	docker compose --profile dev up -d

logs:
	@echo "  App running at: http://localhost:8601"
	docker compose --profile dev logs -f

ps:
	docker compose ps

prod-up:
	docker compose --profile prod up -d --build

prod-down:
	docker compose --profile prod down --remove-orphans

prod-logs:
	docker compose --profile prod logs -f

pip-cache:
	@ARCH=$$(uname -m); \
	if [ "$$ARCH" = "arm64" ] || [ "$$ARCH" = "aarch64" ]; then \
	  PLAT="--platform manylinux_2_17_aarch64 --platform linux_aarch64"; \
	else \
	  PLAT="--platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform linux_x86_64"; \
	fi; \
	pip download $$PLAT \
	  --python-version 3.11 --implementation cp --abi cp311 \
	  --only-binary=:all: \
	  -r requirements.txt \
	  -d pip-cache/

clean:
	docker compose --profile dev down --remove-orphans -v
	docker compose --profile prod down --remove-orphans -v
	docker image prune -f
