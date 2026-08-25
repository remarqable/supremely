#!/bin/sh
set -e
mkdir -p "$DATA_DIR"
flask db upgrade
if [ "$1" = "worker" ]; then
    exec flask jobs run
fi
exec gunicorn wsgi:app -w "${WEB_CONCURRENCY:-4}" -b "0.0.0.0:${PORT:-8000}"
