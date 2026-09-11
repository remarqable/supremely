FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

# Which build this image is. Passed by `make image`, which counts the
# commits since the release tag; empty for anyone building by hand, and the
# application then reports itself as running from source rather than
# claiming a build it does not have.
ARG APP_BUILD=""
# When this image was built, for the update check to compare against the
# registry. Beside APP_BUILD because it is the same kind of fact.
ARG APP_BUILT_AT=""

ENV PATH="/srv/app/.venv/bin:$PATH" \
    APP_BUILD=${APP_BUILD} \
    APP_BUILT_AT=${APP_BUILT_AT} \
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
