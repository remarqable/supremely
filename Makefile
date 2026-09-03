TAILWIND_VERSION := v4.1.14
TAILWIND_BIN := bin/tailwindcss
DATA_DIR ?= data

# Local overrides (git-ignored): per-machine values such as PROD_SSH for
# pull-data. Absent on a fresh clone; that is fine.
-include Makefile.local

.DEFAULT_GOAL := help
.PHONY: help install css css-watch db run worker test lint migrate reset kill demo pull-data image deploy

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies (uv sync)
	uv sync

kill: ## Free the app port: kill whatever listens on PORT (default 8000)
	@PIDS=$$(lsof -ti tcp:$(or $(PORT),8000) -sTCP:LISTEN); \
	if [ -n "$$PIDS" ]; then \
	  kill $$PIDS && echo "Killed $$PIDS (port $(or $(PORT),8000))"; \
	else echo "Nothing listening on port $(or $(PORT),8000)"; fi

$(TAILWIND_BIN):
	@mkdir -p bin
	@case "$$(uname -s)-$$(uname -m)" in \
	  Darwin-arm64)  F=tailwindcss-macos-arm64 ;; \
	  Darwin-x86_64) F=tailwindcss-macos-x64 ;; \
	  Linux-aarch64) F=tailwindcss-linux-arm64 ;; \
	  Linux-x86_64)  F=tailwindcss-linux-x64 ;; \
	  *) echo "Unsupported platform"; exit 1 ;; \
	esac; \
	curl -sL -o $(TAILWIND_BIN) \
	  https://github.com/tailwindlabs/tailwindcss/releases/download/$(TAILWIND_VERSION)/$$F
	@chmod +x $(TAILWIND_BIN)

css: $(TAILWIND_BIN) ## Build Tailwind CSS (run after template changes; commit app.css)
	$(TAILWIND_BIN) -i app/static/css/input.css -o app/static/css/app.css --minify

css-watch: $(TAILWIND_BIN) ## Rebuild CSS continuously while editing templates
	$(TAILWIND_BIN) -i app/static/css/input.css -o app/static/css/app.css --watch

# Heavy-dev schema: build straight from the models (no migration authoring).
# Adds missing tables; run `make reset` for an incompatible model change.
db: ## Sync the dev schema straight from the models (no migrations)
	uv run flask dev sync-db

# The migration path — for prod/CI/smoke, and to verify the deploy story.
migrate: ## Apply Alembic migrations (the prod/CI path)
	uv run flask db upgrade

run: db ## Sync schema, then start the dev server on :8000
	uv run python run.py

worker: ## Run the background job worker (blocking)
	uv run flask jobs run

test: ## Run the test suite (pytest)
	uv run pytest

lint: ## Lint with ruff
	uv run ruff check .

demo: ## Dev only: wipe + seed a Demo Community (admin@demo.test / password)
	rm -rf $(DATA_DIR)
	uv run flask dev sync-db
	uv run flask seed demo

reset: ## Wipe data/ (DB, wizard config, uploads); setup wizard runs again
	rm -rf $(DATA_DIR)
	uv run flask dev sync-db
	@echo "Installation reset. Run 'make run' and open /setup."

# Mirror the production data locally for testing. Pull-only: nothing on the
# server is modified except a temporary snapshot file, removed afterwards.
# The database is snapshotted with SQLite's backup API (never a raw copy of
# a live file), uploads and installed themes are mirrored, and the server's
# config.env / secret_key are deliberately left alone — local settings and
# secrets stay local. The current local DB is backed up first, and sync-db
# then adds any columns the local models have that the server DB predates.
# Set PROD_SSH (and optionally PROD_DIR, PROD_CONTAINER) in Makefile.local.
PROD_DIR ?= /opt/supremely
PROD_CONTAINER ?= supremely
pull-data: ## Pull the server's DB + uploads for local testing (needs PROD_SSH)
	@test -n "$(PROD_SSH)" || { echo "Set PROD_SSH in Makefile.local (e.g. PROD_SSH = root@example.com)"; exit 1; }
	@if lsof -t $(DATA_DIR)/app.db >/dev/null 2>&1; then \
	  echo "The local app still has $(DATA_DIR)/app.db open — stop it first (make kill, and stop the worker)"; exit 1; fi
	ssh $(PROD_SSH) "docker exec $(PROD_CONTAINER) python -c \"import sqlite3; s = sqlite3.connect('/data/app.db'); d = sqlite3.connect('/data/.pull-snapshot.db'); s.backup(d); d.close(); s.close()\""
	@mkdir -p $(DATA_DIR)/backups
	@if [ -f $(DATA_DIR)/app.db ]; then \
	  sqlite3 $(DATA_DIR)/app.db ".backup '$(DATA_DIR)/backups/local-before-pull-$$(date +%Y%m%d-%H%M%S).db'"; \
	  echo "Local DB backed up to $(DATA_DIR)/backups/"; fi
	rm -f $(DATA_DIR)/app.db-wal $(DATA_DIR)/app.db-shm
	scp $(PROD_SSH):$(PROD_DIR)/data/.pull-snapshot.db $(DATA_DIR)/app.db
	ssh $(PROD_SSH) "rm -f $(PROD_DIR)/data/.pull-snapshot.db"
	rsync -a --delete $(PROD_SSH):$(PROD_DIR)/data/uploads/ $(DATA_DIR)/uploads/ 2>/dev/null || true
	rsync -a $(PROD_SSH):$(PROD_DIR)/data/themes/ $(DATA_DIR)/themes/ 2>/dev/null || true
	uv run flask dev sync-db
	@echo "Server data mirrored locally. Start the dev server to see it."

# Release image. Multi-platform builds need a BuildKit builder that is not the
# plain "docker" driver (Docker Desktop's default), so the target creates a
# docker-container builder once and always builds through it by name. Set
# IMAGE in Makefile.local to publish under a different repository.
IMAGE ?= remarqable/supremely
PLATFORMS ?= linux/amd64,linux/arm64
BUILDER ?= supremely
image: ## Build the multi-arch image and push it to the registry
	@docker buildx inspect $(BUILDER) >/dev/null 2>&1 || \
	  docker buildx create --name $(BUILDER) --driver docker-container --bootstrap
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) -t $(IMAGE):latest --push .

deploy: image ## Build + push the image, then update the server (needs PROD_SSH)
	@test -n "$(PROD_SSH)" || { echo "Set PROD_SSH in Makefile.local (e.g. PROD_SSH = root@example.com)"; exit 1; }
	ssh $(PROD_SSH) "bash $(PROD_DIR)/installer --update"
	@echo "Deployed $(IMAGE):latest to $(PROD_SSH)."
