#!/bin/sh
set -e
mkdir -p "$DATA_DIR"

if [ "$1" = "worker" ]; then
    # The web container owns migrations (single migrator). The worker waits
    # until the schema is present rather than racing the same upgrade.
    until flask db current >/dev/null 2>&1; do
        echo "worker: waiting for database migrations..."
        sleep 2
    done
    exec flask jobs run
fi

# Web container: the one process that runs migrations, before Gunicorn starts.
flask db upgrade
# Access log to stdout, where the container collects it. Without it no
# layer of this stack records that a request happened at all, so an
# incident cannot be reconstructed and scanning cannot be noticed.
exec gunicorn wsgi:app -w "${WEB_CONCURRENCY:-4}" -b "0.0.0.0:${PORT:-8000}" \
    --access-logfile - --error-logfile -
