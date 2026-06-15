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

RUN pip install --no-cache-dir -e . \
    && useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app \
    && mkdir -p /data && chown appuser:appuser /data

# Config (copied as root; chown so appuser can read).
COPY config/ ./config/
RUN chown -R appuser:appuser /app

# Steady state: run as non-root. /data ownership for volumes is handled by init in compose.
USER appuser

# ENTRYPOINT keeps `docker compose run bot --config … run-once` working without repeating ghdcbot.
ENTRYPOINT ["ghdcbot"]
# Default: run Discord bot. Override args for run-once, link, etc.
CMD ["--config", "/app/config/config.yaml", "bot"]
