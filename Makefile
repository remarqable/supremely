TAILWIND_VERSION := v4.1.14
TAILWIND_BIN := bin/tailwindcss
DATA_DIR ?= data

.DEFAULT_GOAL := help
.PHONY: help install css css-watch db run worker test lint migrate reset kill demo

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
