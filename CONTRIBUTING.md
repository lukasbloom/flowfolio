# Contributing to Flowfolio

Thanks for taking a look. A bit of honesty up front: Flowfolio is primarily a
personal project, built and maintained by a single developer to track their own
portfolio. It is open source (AGPL-3.0) because the self-hosted, privacy-first
ethos is worth sharing, not because it is chasing a large contributor base.

Pull requests are welcome, but expect a light touch. Small, focused fixes
(bugs, docs, a clean feature with tests) are the easiest to accept. For anything
larger, please open an issue first so we can agree on the shape before you spend
time on it. There is no SLA on reviews.

## Project layout

This is a monorepo with three top-level apps:

- `backend/` — FastAPI (Python 3.12) + SQLite (WAL) + SQLAlchemy 2.0 async + Alembic.
- `frontend/` — Next.js 16 (App Router) + React 19 + Tailwind + shadcn/ui + ECharts.
- `landing/` — the standalone static marketing site (built and deployed on its own).

See `CLAUDE.md` for the full architecture, the stack rationale, and the price/FX
data-source notes.

## Local development

The stack runs in Docker Compose. The compose layout has two faces:

- `compose.yml` is the single-image distribution artifact (FastAPI + Next.js +
  Caddy + backup in one supervised container). This is what a self-hoster runs.
- `compose.multi.yml` is the multi-service base (api + web + caddy + backup) that
  the dev and test overlays target.

For day-to-day work use the hot-reload dev overlay against the multi-service base:

```bash
docker compose -f compose.multi.yml -f compose.dev.yml up -d   # http://localhost:8080/
```

- Frontend runs `next dev` against the bind-mounted `./frontend` (HMR ~1s).
- API runs `uvicorn --reload` against the bind-mounted `./backend` (~3s reload).
- The first `up` runs `npm install` inside the web container (~30s on a cold
  `web_node_modules` volume). Later boots start in seconds.

Always pair the dev (or test) overlay with `compose.multi.yml`, never with the
single-image `compose.yml`. Overlaying a dev/test file onto `compose.yml`
silently drops the caddy and backup services.

To build and run the production single-image artifact (the pre-ship check):

```bash
docker compose up -d --build
```

Configuration lives in a `.env` file. Copy `.env.example` to `.env` and fill in
the values before the first run. The example file ships placeholder-only values,
so no real secret is ever committed.

## Running the tests

Backend test suite (runs natively against the local venv, no Docker needed, the
pytest fixtures build a fresh schema per test):

```bash
cd backend && uv run python -m pytest
```

Frontend lint and build (also native):

```bash
cd frontend && npm run lint
cd frontend && npm run build
cd frontend && npm run test:unit
```

Frontend end-to-end and visual-snapshot suites run against an isolated test
stack seeded from the deterministic golden fixture (frozen clock, zero real
data). Regenerate the fixture, then run the suite:

```bash
cd frontend && npm run test:e2e:regen-db   # rebuild tests/fixtures/golden.sqlite
cd frontend && npm run test:e2e            # full Playwright run
cd frontend && npm run test:e2e:snapshots  # visual snapshot project only
```

`scripts/seed-golden.py` seeds the deterministic **test** fixture only. It never
touches a real database.

## Regenerating the marketing screenshots

The landing-page and README screenshots are produced from the same golden seed
via a dedicated Playwright project, so they stay reproducible and never go stale
against a moving "now". With the isolated test stack running on `:8081`:

```bash
cd frontend && npm run test:e2e:screenshots   # marketing-chromium project
```

## PR expectations

Before opening a PR, please make sure:

- Backend adds no new Ruff or mypy errors. CI enforces a count ratchet against
  `backend/lint-baseline.json`. Run `uv run python scripts/lint_ratchet.py` in
  `backend/` to check locally. If your PR fixes existing errors, lower the
  baseline numbers in the same PR.
- Frontend passes ESLint (`npm run lint`) and builds (`npm run build`).
- The relevant test suites are green.
- Money math stays on `decimal.Decimal` (backend) and exact representations end
  to end. Never introduce binary floats into financial calculations.
- No real secrets, credentials, or personal financial data land in the diff.

Keep commits focused and the description clear about the "why". That is enough.
