TAILWIND_VERSION := v4.1.14
TAILWIND_BIN := bin/tailwindcss
DATA_DIR ?= data

.PHONY: install css css-watch run worker test migrate reset

install:
	uv sync

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

css: $(TAILWIND_BIN)
	$(TAILWIND_BIN) -i app/static/css/input.css -o app/static/css/app.css --minify

css-watch: $(TAILWIND_BIN)
	$(TAILWIND_BIN) -i app/static/css/input.css -o app/static/css/app.css --watch

# Heavy-dev schema: build straight from the models (no migration authoring).
# Adds missing tables; run `make reset` for an incompatible model change.
db:
	uv run flask dev sync-db

# The migration path — for prod/CI/smoke, and to verify the deploy story.
migrate:
	uv run flask db upgrade

run: db
	uv run python run.py

worker:
	uv run flask jobs run

test:
	uv run pytest

# Wipe the local installation (database, wizard config, uploads) so the
# setup wizard runs again. Stop the server first.
reset:
	rm -rf $(DATA_DIR)
	uv run flask dev sync-db
	@echo "Installation reset. Run 'make run' and open /setup."
