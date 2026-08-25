FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

ENV PATH="/srv/app/.venv/bin:$PATH" \
    DATA_DIR=/data \
    APP_ENV=production \
    FLASK_APP=wsgi.py \
    PORT=8000

# Run as a non-root user; give it ownership of the data volume mount point.
RUN useradd --system --uid 10001 supremely \
    && mkdir -p /data && chown supremely:supremely /data
USER supremely

VOLUME /data
EXPOSE 8000

ENTRYPOINT ["./scripts/docker-entrypoint.sh"]
