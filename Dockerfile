# Gitcord - Offline-first Discord–GitHub automation engine
# Production Dockerfile: minimal layers, non-root user, reproducible build.
# Python 3.11 slim for smaller image and security updates.

FROM python:3.11-slim

# Prevent Python from writing bytecode and buffering stdout (cleaner logs in containers).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (better layer caching: only re-run when deps change).
# We copy only dependency manifests and source package, then install.
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -e . \
    && useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app \
    && mkdir -p /data && chown appuser:appuser /data

# Config and entrypoint (entrypoint runs as root to chown /data, then gosu to appuser).
COPY config/ ./config/
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Default: run Discord bot. Override with run-once or other commands.
# Example: docker compose run --rm bot --config /app/config/config.yaml run-once
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["--config", "/app/config/config.yaml", "bot"]
