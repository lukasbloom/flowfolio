# Combined single-image build: FastAPI (uvicorn) + Next.js (node) + Caddy,
# supervised by s6-overlay as PID 1. The APScheduler jobs run INSIDE uvicorn
# (--workers 1), so there are only THREE supervised services, not four.
#
# Multi-arch (amd64 + arm64): the s6-overlay arch token is derived from buildx's
# TARGETARCH (amd64 -> x86_64, arm64 -> aarch64), so one `buildx --platform
# linux/amd64,linux/arm64` invocation produces correct per-arch images. An
# explicit S6_OVERLAY_ARCH build-arg still overrides for single-arch.

# ---- frontend build (mirrors frontend/Dockerfile builder) ----
FROM node:22-slim AS web-build
WORKDIR /app
COPY frontend/package.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

# ---- python deps (mirrors backend/Dockerfile; the stale upper bcrypt pin is
#      dropped per RESEARCH — pyproject floors at bcrypt>=4, passlib still listed) ----
FROM python:3.12-slim AS api-build
RUN pip install --no-cache-dir pip==24.* && \
    pip install --no-cache-dir "fastapi==0.136.*" "uvicorn[standard]==0.29.*" \
    "sqlalchemy[asyncio]==2.0.*" "aiosqlite==0.20.*" "alembic==1.13.*" \
    "pydantic-settings==2.*" "passlib[bcrypt]==1.7.*" "bcrypt>=4" "python-jose[cryptography]==3.*" \
    "httpx==0.27.*" "apscheduler==3.11.*" "selectolax==0.3.*"

# ---- final runtime ----
FROM python:3.12-slim AS runtime

# System CLIs: curl (healthcheck), sqlite3/age/rclone/util-linux (backup job
# subprocess), ca-certificates + xz-utils (s6 tarball + ACME trust store).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl sqlite3 age rclone util-linux ca-certificates xz-utils && \
    rm -rf /var/lib/apt/lists/*

# Caddy binary from the official pinned image (glibc-compatible).
COPY --from=caddy:2 /usr/bin/caddy /usr/bin/caddy

# Docker CLI + compose plugin: pinned static binaries from the official
# images (glibc-compatible). Only the `updater` sidecar (scripts/updater.sh)
# ever invokes `docker`/`docker compose`; the app process never does. The
# compose plugin must live under cli-plugins/ to be discovered as `docker compose`.
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker/compose-bin:v2.32.4 /docker-compose /usr/local/lib/docker/cli-plugins/docker-compose

# Node runtime from the web-build stage (node:22-slim is Debian/glibc, so the
# binary runs on this python:3.12-slim base). The Next standalone server.js
# needs only the node binary at runtime, no node_modules tree.
COPY --from=web-build /usr/local/bin/node /usr/local/bin/node

# s6-overlay (official just-containers release, pinned). TARGETARCH is supplied
# automatically by buildx; S6_OVERLAY_ARCH (empty default) lets a caller force a
# token for a single-arch build.
ARG S6_OVERLAY_VERSION=3.2.3.0
ARG TARGETARCH
ARG S6_OVERLAY_ARCH=
RUN set -eu; \
    s6_arch="${S6_OVERLAY_ARCH}"; \
    if [ -z "${s6_arch}" ]; then \
      case "${TARGETARCH:-amd64}" in \
        amd64) s6_arch=x86_64 ;; \
        arm64) s6_arch=aarch64 ;; \
        *) echo "unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
      esac; \
    fi; \
    base="https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}"; \
    curl -fsSL "${base}/s6-overlay-noarch.tar.xz" -o /tmp/s6-overlay-noarch.tar.xz; \
    curl -fsSL "${base}/s6-overlay-${s6_arch}.tar.xz" -o /tmp/s6-overlay-arch.tar.xz; \
    tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz; \
    tar -C / -Jxpf /tmp/s6-overlay-arch.tar.xz; \
    rm /tmp/s6-overlay-noarch.tar.xz /tmp/s6-overlay-arch.tar.xz

# Python site-packages from the api-build stage + the backend app.
COPY --from=api-build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY backend/ /app/

# Next.js standalone output — all THREE artifacts (standalone omits static + public).
COPY --from=web-build /app/.next/standalone /app/web/
COPY --from=web-build /app/.next/static /app/web/.next/static
COPY --from=web-build /app/public /app/web/public

# Backup scripts the in-process job shells out to.
COPY scripts/ /app/scripts/

# Caddy config + the s6-overlay service definitions (see process-supervisor/README.md).
COPY Caddyfile /etc/caddy/Caddyfile
COPY process-supervisor/s6-overlay /etc/s6-overlay

WORKDIR /app

# PORT/HOSTNAME drive node server.js -> 127.0.0.1:3000.
# WEB_CONCURRENCY=1 enforces the scheduler single-worker invariant.
#
# S6_KEEP_ENV=1: s6-overlay v3 sanitises the environment for supervised services
# by default, so a `docker run -e APP_PASSWORD=… ` / compose `environment:` var
# never reaches uvicorn/node/caddy. With S6_KEEP_ENV=1 the full container env is
# preserved into every s6 service — required so DOMAIN, APP_PASSWORD, SECRET_KEY,
# DATABASE_URL, the FLOWFOLIO_* knobs, the pricing keys, and BACKUP_* all reach
# the app (without it the lifespan APP_PASSWORD pre-seed silently no-ops and the
# Caddy DOMAIN env-switch never sees DOMAIN).
# SETUP_STATUS_ORIGIN: the Next middleware runs server-side INSIDE this container,
# where the external published port is not bound. Point the first-run
# setup-status fetch straight at FastAPI on the loopback (127.0.0.1:8000) — this
# bypasses Caddy (whose `localhost:8080` site block does not match a 127.0.0.1
# Host header, returning empty) and so an unclaimed instance correctly routes to
# /setup regardless of the host port.
# APP_VERSION is stamped at build time by the release workflow, which
# passes --build-arg APP_VERSION=${git tag}. Untagged local builds fall back to
# "dev". Surfaced as a runtime ENV so it reaches uvicorn under S6_KEEP_ENV=1.
ARG APP_VERSION=dev

ENV PORT=3000 \
    HOSTNAME=127.0.0.1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEB_CONCURRENCY=1 \
    S6_KEEP_ENV=1 \
    APP_VERSION=${APP_VERSION} \
    SETUP_STATUS_ORIGIN=http://127.0.0.1:8000 \
    XDG_DATA_HOME=/data/caddy \
    FLOWFOLIO_DEMO_SEED_PATH=/app/demo-seed.sqlite

# Bake the pristine demo seed once, at build time, from the
# SAME scripts/fixtures/golden_portfolio.py fixture the e2e golden uses — one
# seed, two consumers, never drift. The reset engine (app/services/demo_reset.py)
# swaps this file over the live DB on boot and on the reset cron. The build runs
# network-free: seed-golden.py inserts the FX anchors first, so every
# get_or_fetch_fx_rate cache-hits and nothing reaches the wire. The seed
# is internally coherent only at FIXTURE_FROZEN_NOW=2026-04-30T12:00:00Z, which
# the demo pins via FLOWFOLIO_FIXED_NOW at runtime. FLOWFOLIO_DEMO_SEED_PATH is
# the single source of truth for the path — demo_reset.py reads the same env.
# The file is made read-only so the image's source-of-truth seed cannot be
# mutated in place. The path is inert in a normal install (consumed only in
# demo mode); baking it always keeps the image identical for private and demo.
RUN SEED_OUTPUT_PATH="${FLOWFOLIO_DEMO_SEED_PATH}" PYTHONPATH=/app \
      python /app/scripts/seed-golden.py && \
    chmod 0444 "${FLOWFOLIO_DEMO_SEED_PATH}"

ENTRYPOINT ["/init"]

# Single healthcheck through Caddy (covers the full proxy + uvicorn path). The
# 40s start-period covers Alembic migrations + scheduler boot.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -fsS http://localhost:8080/api/healthcheck || exit 1
