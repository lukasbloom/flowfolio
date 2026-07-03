## Project

**Flowfolio**

A self-hosted personal portfolio tracker web app. Lets a single user record buy/sell transactions across stocks, ETFs/funds, crypto, and stablecoins held across multiple accounts, with live price fetching, multi-currency support (EUR/USD), and yield accrual for APY-bearing positions. Replaces a static spreadsheet that captures snapshots only, capturing the full transaction history so it can answer "which holding has performed best over time?"

**Core Value:** When I open the app, I can compare the performance (% return + time-weighted return) of every holding I own across multiple timeframes, so I know which investments are actually worth owning over time.

### Constraints

- **Privacy**: Self-hosted on user's own VPS — never a hosted SaaS. Financial data never leaves user-controlled infrastructure.
- **Single user**: No multi-tenant concerns; auth can be a single account with strong password / passkey.
- **Tech stack**: FastAPI (Python 3.12+) + SQLite (WAL mode) + Next.js (React) + Apache ECharts. Decimal arithmetic via Python `decimal` stdlib end-to-end. Schema migrations via Alembic. Background jobs via APScheduler in-process. Docker Compose: api + web + Caddy (3 containers; SQLite as a file in a named volume).
- **Pricing data**: Must work with free-tier sources for stocks and crypto. European mutual funds AND ETFs/ETCs (incl. the gold ETC) are priced for free by scraping FT.com tear-sheets (`app/services/pricing/ft_scraper.py`) — funds keyed by ISIN, ETFs/metals by exchange ticker (e.g. `VUSA:GER`, `EGLN:LSE`). Manual NAV override remains available as a fallback. (There is no free *official API* for EU UCITS by ISIN — paid only — but FT scraping is a working free *source*.)
- **Cost**: Hobby project — operating cost should sit comfortably alongside an existing VPS; no paid data feeds.
- **Form factor**: Responsive web app — must work well on mobile browsers without a PWA.
## Technology Stack

## Recommended Stack (Single Coherent Package)
| Layer | Choice | Version |
|-------|--------|---------|
| Backend framework | FastAPI (Python) | 0.136.x |
| Language runtime | Python | 3.12 |
| ORM | SQLAlchemy 2.0 async | 2.0.49 |
| Database | SQLite (WAL mode) + named Docker volume | 3.x (bundled) |
| Migrations | Alembic | 1.13.x |
| Scheduler | APScheduler 3.x | 3.11.x |
| External HTTP | httpx | 0.27.x |
| Decimal arithmetic | Python `decimal` stdlib | built-in |
| Frontend framework | Next.js (App Router) | 16.2.x |
| UI components | shadcn/ui + Tailwind CSS | latest |
| Charting | Apache ECharts via echarts-for-react | ECharts 5.5.x / wrapper 3.0.6 |
| Data fetching | TanStack Query v5 | 5.x |
| Reverse proxy / TLS | Caddy | 2.x |
| Auth | Single bcrypt-hashed password + HTTP-only session cookie | — |
| Containerisation | Docker Compose | — |
| Stock prices | Finnhub (primary) + Alpha Vantage (fallback) | free tiers |
| Crypto prices | CoinGecko Demo API | free tier |
| FX rates | Frankfurter API (ECB-sourced) | free, no key |
| European mutual funds / ETFs / gold ETC | FT.com tear-sheet scrape (free; `ft_scraper.py`), manual NAV fallback | — |
## Core Technologies
### Backend Framework — FastAPI (Python)
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| FastAPI | 0.136.x | REST API, background task hooks | Async-first, Pydantic v2 validation, minimal boilerplate, APScheduler integration is trivial, Python `decimal` for money is first-class, strongest free-tier API ecosystem in Python, excellent Docker story |
| Python | 3.12 | Runtime | 3.12 is the safe 2026 baseline; 3.13 exists but package support is still catching up; FastAPI 0.130+ dropped 3.9 |
| Uvicorn | 0.29.x | ASGI server | Standard FastAPI production server; use `--workers 1` for single-user (no multi-process scheduler conflicts) |
| Pydantic v2 | 2.x | Schema validation / serialisation | Ships with FastAPI 0.100+; up to 50x faster than v1 |
- **vs. NestJS/Node.js:** Python's `decimal.Decimal` is zero-effort, exact, and available in stdlib. In Node you must choose and integrate `decimal.js` or `dinero.js` and store values as strings or integers in the DB to avoid float leakage. Python removes this entire risk category from the codebase. APScheduler integrates as a startup lifespan hook without Redis. The financial calculation ecosystem (yield accrual formulas, XIRR, TWR) is richer in Python.
- **vs. Django:** Django's ORM is synchronous at its core; async support is bolted on. FastAPI + SQLAlchemy 2.0 async is native. Django carries admin/sessions/templates overhead that a pure API doesn't need.
- **vs. Go/Axum/Phoenix:** Faster cold performance, but the decimal handling story and scheduling ecosystem are more mature in Python. Over-engineered for a single-user personal tool running on a VPS.
- **vs. Hono:** Hono is a lightweight HTTP router — it has no scheduler, no DI, no ORM integration. "Stacking third-party libraries to compensate" negates the simplicity argument for a project this layered.
### Database — SQLite (WAL mode, Docker named volume)
- WAL mode allows one concurrent writer + multiple concurrent readers, eliminating the "whole DB locked" concern for a dashboard that reads while the scheduler writes a price snapshot.
- Zero ops cost: no Postgres container to manage, backup, tune. One file.
- Docker named volumes keep it persistent; `sqlite3 db.sqlite ".backup backup.sqlite"` is all the backup logic needed.
- Simon Willison's April 2026 research confirms WAL mode works correctly across containers sharing a named volume on the same Docker host (same kernel, same filesystem — locks function).
- At a single user's portfolio scale (tens of holdings), the entire dataset fits in SQLite's sweet spot by several orders of magnitude.
### ORM & Migrations — SQLAlchemy 2.0 + Alembic
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| SQLAlchemy | 2.0.49 | Async ORM, query builder | Native async, `AsyncSession`, works with both SQLite and Postgres; battle-tested |
| aiosqlite | 0.20.x | SQLite async driver | Required by SQLAlchemy's async engine for SQLite |
| Alembic | 1.13.x | Schema migrations | De-facto standard with SQLAlchemy; `--autogenerate` from models |
### Scheduler — APScheduler 3.11.x
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| APScheduler | 3.11.x | Daily yield accrual, daily price snapshot, FX fetch | Stable 3.x line (v4.0 is pre-release, not for production). Integrates via FastAPI lifespan hook. No Redis needed. |
### External HTTP — httpx
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| httpx | 0.27.x | Async HTTP calls to price APIs | FastAPI ecosystem standard; `AsyncClient` with connection pooling; timeout and retry configuration built-in |
### Decimal Arithmetic — Python `decimal` stdlib
### Frontend — Next.js 16 (App Router)
| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Next.js | 16.2.x | Full-stack React framework | Self-hosting is first-class via `output: "standalone"` Docker image; App Router + Server Components reduce client bundle; strong ecosystem for charting + forms; largest component library ecosystem (shadcn, Radix, etc.) |
| React | 19.x | UI runtime | Ships with Next.js 16 |
| TypeScript | 5.x | Type safety | Standard with Next.js |
- **vs. SvelteKit:** SvelteKit has better raw benchmark numbers and smaller bundles, but the React ecosystem's charting library support is significantly broader. Apache ECharts' React wrapper (echarts-for-react) is well-maintained; Svelte equivalents are thinner. shadcn/ui is React-only. For a dashboard-heavy app, React's ecosystem advantage wins.
- **vs. Vite + React SPA:** A pure SPA requires a separate API server with CORS management, more Docker containers, no SSR for initial load. Next.js collocates the frontend and can proxy API calls internally. For a single-user personal tool the added complexity of a SPA+API pattern is unnecessary.
- **vs. HTMX + server-rendered:** HTMX works excellently for form-heavy apps but charting with ECharts requires JavaScript anyway. Mixing HTMX and complex client-side chart state is awkward.
### UI Components — shadcn/ui + Tailwind CSS
| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| shadcn/ui | latest (copy-into-project) | Data tables, forms, modals, badges | Components are copied as local TypeScript files — no locked-in version; built on Radix UI for accessibility; the 2026 de-facto standard for Next.js admin dashboards |
| Tailwind CSS | 4.x | Utility styling | Ships with shadcn; excellent responsive support |
### Charting — Apache ECharts (via echarts-for-react)
| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| echarts | 5.5.x | Chart engine | Canvas + SVG rendering; WebGL option for large datasets; financial candlestick built-in; stacked area, multi-axis, time-series all first-class |
| echarts-for-react | 3.0.6 | React wrapper | Thin, stable wrapper; last published ~3 months ago |
- **vs. Recharts:** Recharts is SVG-only and shows performance degradation with large datasets. For net-worth time-series with hundreds of daily snapshots + transaction markers, ECharts canvas rendering is faster. ECharts also has better multi-axis support (needed for cost-basis vs. value charts).
- **vs. Lightweight Charts (TradingView):** Excellent for OHLCV candlestick specifically, but poor for pie/donut and stacked area. Narrow use case.
- **vs. D3:** D3 is a primitive — building all chart types from D3 primitives is a significant investment for a personal project.
- **vs. Highcharts:** Non-commercial use is free, but license explicitly forbids use in commercial SaaS. For a personal private tool it is technically permissible, but the ambiguity and future risk are not worth it when ECharts (Apache License 2.0) is equally capable.
- **vs. Chart.js:** Good enough for simple charts but weaker on multi-axis financial layouts.
- **vs. uPlot:** Very fast, minimal bundle, but lacks pie/donut charts entirely. Cannot cover all required chart types.
| Chart Type | ECharts Support |
|------------|-----------------|
| Line (net worth over time) | Native; `markPoint` for transaction annotations |
| Stacked area (cost basis vs value) | Native `stack: "total"` |
| Bar (contributions per month/year) | Native |
| Pie / Donut (allocation by type, risk, account) | Native |
| Time-series multi-axis | Native `yAxis` array |
### Data Fetching — TanStack Query v5
| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| @tanstack/react-query | 5.99.x | Server state management | Caching, background refetching, stale indicators; avoids manual `useEffect` fetch logic; pairs naturally with FastAPI JSON responses |
### Reverse Proxy / TLS — Caddy
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Caddy | 2.x | TLS termination, reverse proxy | Automatic HTTPS via Let's Encrypt with zero configuration; Caddyfile is 3 lines for a typical self-hosted setup; no certbot cron jobs; consensus 2026 choice for solo-developer VPS hosting |
### Authentication — Password + HTTP-only session cookie
## Price & FX Data Sources
### US Equities / ETFs — Finnhub (primary)
| Attribute | Value |
|-----------|-------|
| Free tier rate limit | 60 calls/minute |
| Historical endpoint | `/stock/candle` — daily OHLCV, ~1 year lookback on free tier |
| Authentication | Free API key (registration required) |
| Personal use | Allowed; redistribution of raw data is not |
| Reliability | HIGH — institutional-grade, actively maintained |
| Terms concern | None for a private self-hosted tool |
- Free tier: 25 calls/day, 5 calls/minute
- Use as fallback when Finnhub returns an error or for tickers Finnhub does not cover (small-cap international)
- 25 calls/day is sufficient for a secondary fallback that rarely triggers
| Avoid | Why |
|-------|-----|
| Yahoo Finance / yfinance | Official API shut down in 2017; unofficial endpoints break without warning; rate limiting became aggressive 2024+; IP bans reported; unreliable for scheduled production use |
| Stooq | Very low undocumented daily quota; "Exceeded daily hits limit" errors unpredictable; better for bulk offline downloads than scheduled API calls |
| Polygon.io free | Only 5 calls/minute, 15-minute delayed data; not meaningfully better than Finnhub for this use case |
| Alpha Vantage alone | 25 calls/day is too low to be primary; fine as fallback |
### Crypto — CoinGecko Demo API (primary) + Binance public (supplementary)
| Attribute | Value |
|-----------|-------|
| Rate limit | ~30 calls/minute (varies by traffic) |
| Monthly cap | 10,000 calls |
| Historical data | Up to 12 years; granularity auto-adjusts (daily beyond 90 days) |
| Attribution | Required: "Data provided by CoinGecko" with link |
| Coverage | BTC, ETH, XRP, SOL, TRX — all present |
| Authentication | Free Demo API key |
- `/api/v3/klines` endpoint: OHLCV candles, up to 1,000 per request, weight-based limit of 1,200/minute
- Use for initial historical backfill of Binance-listed pairs (BTC, ETH, XRP, SOL, TRX are all listed)
- No API key needed; freely available; reliable and well-documented
- Limitation: exchange-specific; prices are Binance spot prices (acceptable for a personal tracker)
### FX Rates — Frankfurter API
| Attribute | Value |
|-----------|-------|
| Source | ECB (European Central Bank) reference rates |
| Coverage | 30+ currencies including EUR/USD |
| Historical data | Back to January 4, 1999 |
| Rate limit | Not documented; free, no key required |
| Update frequency | Each working day ~16:00 CET |
| Reliability | Backed by ECB data; Frankfurter is open-source (self-hostable if needed) |
### European Mutual Funds / ETFs / ETCs — FT.com tear-sheet scrape (primary), manual NAV (fallback)

**Implemented and in use** (`app/services/pricing/ft_scraper.py`): scrapes the FT.com tear-sheet `mod-ui-data-list__value` span, currency-converted to EUR via the `:EUR` suffix. Funds resolve by ISIN (in `ticker_override`, else `symbol`); ETFs and the gold ETC resolve by exchange ticker in `ticker_override` (`VUSA:GER`, `SXR8:GER`, `SXRV:GER`, `EQQQ:GER`, `EGLN:LSE`). `allowed_sources_for` permits `ft` for `fund`, `etf`, and `metal`. Free, no key. This replaced the original "manual NAV only" assumption for funds.

**Live/history split (mirrors finnhub→twelve_data):** FT serves only the *current* NAV (no history endpoint), so `ft` instruments take their HISTORY from Yahoo via `app/services/pricing/yahoo.py` (`backfill.py` `ft` branch). ETFs/metal resolve to a Yahoo exchange symbol (`VUSA:GER`→`VUSA.DE`, `EGLN:LSE`→`EGLN.L`); funds resolve their ISIN via Yahoo search to a Morningstar NAV symbol (`0P……F`). Yahoo is HISTORY-ONLY and never wired into the daily scheduler (see the Yahoo row in "What NOT to Use" — the ban risk is repeated polling, not one-shot backfill). `fetch_yahoo_history` asserts the chart currency is EUR. TWRR needs ≥`INSUFFICIENT_HISTORY_DAYS` (7) distinct price-days, which the Yahoo backfill supplies (~760/instrument).

Why scrape rather than an API — no free *official* API covers EU UCITS by ISIN:
| Source | Assessment |
|--------|------------|
| JustETF | Scraper territory; no public API; ETFs only (not open-end funds) |
| Morningstar | Data present but behind auth; no stable free API endpoint |
| Investing.com | Scrapers exist but violate ToS and break unpredictably |
| Twelve Data / EODHD / FMP | Have free tiers, but EU/ISIN fund+ETF coverage is largely paid |
## Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `passlib[bcrypt]` | 1.7.x | Password hashing | Auth implementation |
| `python-jose[cryptography]` | 3.x | JWT / signed session tokens | Auth implementation |
| `aiosqlite` | 0.20.x | Async SQLite driver for SQLAlchemy | Always (SQLite backend) |
| `alembic` | 1.13.x | DB migrations | Every schema change |
| `pydantic-settings` | 2.x | Config from env vars | App configuration (API keys, DB path) |
| `pytest-asyncio` | 0.23.x | Async test runner | Test suite |
| `@tanstack/react-query` | 5.99.x | Frontend server-state | All API data fetching |
| `date-fns` | 3.x | Date arithmetic in JS | Timeframe selectors, chart axis labels |
| `zod` | 3.x | Frontend schema validation | Form validation matching Pydantic schemas |
## Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| Docker Compose | Orchestrate the stack | `compose.yml` = single-image dist artifact; `compose.multi.yml` = 4-service base for dev/test overlays |
| Ruff | Python linter + formatter | Replaces flake8 + black + isort in one tool; fast |
| mypy | Python static typing | Run in CI; FastAPI + Pydantic are mypy-friendly |
| ESLint + Prettier | TypeScript linting + formatting | Next.js ships with ESLint config |
| Alembic | DB migrations | `alembic revision --autogenerate` + `alembic upgrade head` on container start |
## Installation
# Python backend (inside /backend)
# Frontend (inside /frontend)

## Local Development

Two-mode workflow. **Default to dev mode for any code-iteration task; only fall back to a prod rebuild when explicitly testing what will ship.**

**Compose file layout:** the top-level `compose.yml` is now the **single-image dist artifact** — one `flowfolio` service that runs FastAPI + Next.js + Caddy + the backup job in one s6-supervised container (`docker compose up` is what the VPS runs). The former 4-service base (api + web + caddy + backup) moved to **`compose.multi.yml`**, which is the base the dev and test overlays target. Every dev/test invocation uses `-f compose.multi.yml -f <overlay>` — using the old `-f compose.yml -f compose.dev.yml` would overlay the dev file onto the single-image service and silently lose caddy/backup.

### Daily iteration — hot reload (use this 95% of the time)

```bash
docker compose -f compose.multi.yml -f compose.dev.yml up -d
```

- Frontend = `next dev` against bind-mounted `./frontend` (HMR ~1s, no container restart on edit)
- API = `uvicorn --reload --reload-dir /app/app` against bind-mounted `./backend` (~3s reload on edit)
- Caddy, SQLite (`db_data`), backup service inherit from `compose.multi.yml` unchanged
- URL stays `http://localhost:8080/`; existing session cookies survive the swap from prod mode

The first `up` runs `npm install` inside the web container (~30s on a cold `web_node_modules` volume); subsequent boots start in seconds.

### Pre-release testing with the production image

```bash
docker compose down
docker compose up -d --build        # builds the single combined image (api+web+caddy+backup)
```

This is what your VPS will run — the top-level `compose.yml` single-image dist artifact. Reach for this when:
- Running a final manual test pass before a release
- Investigating a bug that smells like a build-time concern (minification, tree-shaking, image optimization, `next build` static generation)
- Testing APScheduler cron/accrual jobs that need to survive past a single reload

### Tradeoff (read before assuming dev == prod)

- Dev mode disables minification, tree-shaking, image optimization; bundle sizes and timings are NOT representative of prod
- `uvicorn --reload` spawns a fresh worker on every save → APScheduler scheduler state, in-memory caches, and accrual job runs are wiped each reload
- The Next.js dev server uses Turbopack on this project; some prod-only edge cases (RSC streaming, suspense boundaries) behave subtly differently

If a bug reproduces in dev but not prod (or vice versa), rebuild prod and check both before classifying.

### Hot-reload verification (sanity-check the overlay is actually doing its job)

```bash
# Backend reload — touching a file should respawn the uvicorn worker
docker logs flowfolio-api-1 --tail 2     # baseline
touch backend/app/main.py
sleep 4
docker logs flowfolio-api-1 --tail 5     # should show "Started server process [N+1]"

# Frontend HMR — sed-edit a visible string and confirm it appears at the URL
sed -i.bak 's/Flowfolio</Flowfolio (test)</' frontend/app/login/page.tsx
sleep 2
curl -s http://localhost:8080/login | grep -oE "Flowfolio[^<]*</h1>"   # → "Flowfolio (test)</h1>"
mv frontend/app/login/page.tsx.bak frontend/app/login/page.tsx
```

If either fails, see `compose.dev.yml` — the overlay relies on `!override` (not `!reset`) for the `volumes:` lists; using the wrong tag silently strips the new mounts.

### Compose-overlay gotchas (learned the hard way)

These cost a real debugging cycle each — don't re-discover them in a future session:

- **`.env` mount conflict.** `compose.multi.yml` mounts `./.env:/app/.env:ro` for the api service. The dev overlay bind-mounts `./backend → /app`, which shadows that path and makes Docker try to create the `.env` mountpoint *inside* the bind-mounted host dir (creates an empty `backend/.env` then errors). The overlay fixes this by replacing the volume list and using `env_file: ./.env` instead. If you ever add a new file-mount to `compose.multi.yml`, mirror the override in `compose.dev.yml`. (The single-image `compose.yml` dist artifact drops the `.env`-as-file mount entirely — it is pure env vars.)
- **`!reset` vs `!override`.** Compose's `!reset` tag returns a list to its empty/default state and **silently discards any inline replacement values** — using it on `volumes:` with a new list under it gives you NO volumes, not the new volumes. The correct tag for "replace this inherited list with my new one" is `!override`. Use `!reset null` or `!reset []` only when you want to genuinely clear an inherited key (e.g., `web.build`, `web.depends_on`).
- **`web_node_modules` is a named volume on purpose.** It prevents the macOS host's bind-mount from clobbering the alpine-native `node_modules` the container installs. Don't replace it with `./frontend/node_modules` — install will try to use mac-native binaries on alpine and fail.

### When NOT to use the dev overlay

- Running the existing backend test suite — `cd backend && uv run python -m pytest` runs natively against the local `.venv`, no Docker needed
- Frontend lint or build verification — `cd frontend && npm run lint` / `npm run build` run natively too
- Quick one-shot SQLite inspection — `docker exec flowfolio-api-1 python -c "..."` works against either dev or prod stack since `db_data` is a named volume shared between them

## Alternatives Considered
| Recommended | Alternative | When Alternative is Better |
|-------------|-------------|---------------------------|
| FastAPI (Python) | NestJS (TypeScript) | If the team is TypeScript-only and strongly prefers JS everywhere; adds decimal library dependency |
| FastAPI (Python) | Django | If you need Django admin, auth system, or ORM migrations out of the box for a larger team project |
| SQLite + WAL | PostgreSQL | When concurrent writers exist, multi-user, or when you need PostGIS / full-text search / JSONB operators |
| APScheduler 3.x | BullMQ + Redis | When jobs must survive process restarts reliably, when jobs need retry queuing, or when running multiple workers |
| Next.js 16 | SvelteKit | Smaller bundle, better raw performance; choose if charting ecosystem limitations are acceptable or if you prefer Svelte's reactivity model |
| Next.js 16 | Vite + React SPA | When the API is a completely separate service or team; adds CORS management overhead |
| Apache ECharts | Recharts | For simpler charts with fewer data points and cleaner React component API |
| Apache ECharts | Lightweight Charts | If the app were only candlestick OHLCV charts (too narrow) |
| Caddy | Traefik | When Docker label-based dynamic routing across many services is needed; over-engineered for 2-service setup |
| Caddy | Nginx | When you already know Nginx and prefer manual cert management; no advantage over Caddy here |
| Frankfurter (ECB) | OpenExchangeRates | OpenExchangeRates free tier requires an API key and limits base currency to USD only; Frankfurter is EUR-based and matches this app's primary currency |
| FT.com scrape for funds/ETFs | Twelve Data / EODHD paid | At ~$12-29/month for a hobby project; out of scope per cost constraint. FT scraping covers the same instruments for free; manual NAV remains the fallback. |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `float` / JavaScript `number` for money | Binary floating-point: `0.1 + 0.2 = 0.30000000000000004`; unacceptable for financial data | Python `decimal.Decimal`; store as `NUMERIC` / `TEXT` in DB |
| Yahoo Finance / yfinance — *for SCHEDULED LIVE polling* | Official API dead since 2017; unofficial scrapers actively blocked since 2024; IP bans reported; zero reliability for scheduled jobs | Finnhub (primary), Alpha Vantage (fallback). **Exception:** Yahoo IS used as a HISTORY-ONLY backfill source for `ft` instruments (EU funds/ETFs/metal) — see note below; the ban risk is about repeated daily polling, not a one-shot backfill. Confirmed empirically: a burst of test calls got the dev IP throttled for ~minutes, so the backfill spaces calls + backs off, and the live path stays on FT. |
| Highcharts | Open-source license only for non-commercial — ambiguous for a "personal financial tool"; vendor-lock-in risk | Apache ECharts (Apache License 2.0, unambiguous) |
| APScheduler 4.0 (pre-release) | Explicitly marked "do not use in production"; ground-up redesign with breaking changes still in flux | APScheduler 3.11.x (stable) |
| Redis / BullMQ | Over-engineered for 3 nightly cron jobs; adds a third container with no proportionate benefit | APScheduler 3.x in-process |
| Visx / D3 from scratch | High-effort primitives; requires building every chart type manually; 10x implementation time vs. ECharts | Apache ECharts |
| CoinMarketCap free | 333 calls/day only; CMC's attribution requirements are stricter; CoinGecko covers the same coins with better free limits | CoinGecko Demo |
| Polygon.io free | Only 5 calls/minute, 15-min delayed data, 2 years history — strictly inferior to Finnhub for this use case | Finnhub |
| Stooq API (for scheduled calls) | Low undocumented daily quota; designed for bulk CSV downloads, not real-time API calls | Finnhub |
## Stack Patterns by Variant
- Add `py_webauthn` to backend dependencies
- Requires HTTPS to be live before testing (Caddy handles this)
- Store passkey credentials in a `webauthn_credentials` table
- Registration flow: one-time setup endpoint, store credential; authentication flow: challenge-response
- Add ~2-3 days implementation vs password auth
- Change SQLAlchemy connection string from `sqlite+aiosqlite://` to `postgresql+asyncpg://`
- Add `asyncpg` to dependencies
- Run `alembic upgrade head` — schema is portable
- Add Postgres container to `compose.yml`
- No application code changes required if money columns are `Numeric(precision=18, scale=8)` type in SQLAlchemy (maps to `NUMERIC` in both SQLite and Postgres)
- 800 calls/day free (8/minute) with 4-hour delayed data
- Adequate for nightly snapshots; insufficient for live price badges during market hours
- No advantage over Finnhub for this use case; mention for completeness
## Version Compatibility
| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| FastAPI 0.136.x | Pydantic 2.x | Requires Pydantic v2; Pydantic v1 no longer supported |
| FastAPI 0.136.x | Python 3.10+ | Python 3.9 support dropped in FastAPI 0.130.0 (Feb 2026) |
| SQLAlchemy 2.0.49 | aiosqlite 0.20.x | aiosqlite required for async SQLite dialect |
| APScheduler 3.11.x | asyncio (Python) | Use `AsyncIOScheduler` variant; v4.0 has different API |
| echarts-for-react 3.0.6 | echarts 5.x | Peer dependency; install both explicitly |
| Next.js 16.x | React 19.x | Ships bundled; no manual React version management needed |
| shadcn/ui (latest) | Next.js 16, Tailwind 4.x | shadcn components are copied into project; version drift is not an issue |
## Sources
- Finnhub rate limit: [https://finnhub.io/docs/api/rate-limit](https://finnhub.io/docs/api/rate-limit) — confirmed 60 calls/minute free tier (MEDIUM confidence — page returned partial content; corroborated by multiple community sources)
- CoinGecko pricing + rate limits: [https://www.coingecko.com/en/api/pricing](https://www.coingecko.com/en/api/pricing) and [https://docs.coingecko.com/docs/common-errors-rate-limit](https://docs.coingecko.com/docs/common-errors-rate-limit) — confirmed ~30 calls/minute, 10k/month (HIGH confidence — official docs)
- Alpha Vantage free tier: multiple 2026 review articles — 25 calls/day, 5/minute (HIGH confidence — consistent across sources)
- Frankfurter API: [https://frankfurter.dev/](https://frankfurter.dev/) — ECB-sourced, free, no key, history from 1999 (HIGH confidence — official site)
- Binance public API: [https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) — weight-based limits, public endpoints no auth (HIGH confidence — official docs)
- SQLite WAL + Docker: [https://simonwillison.net/2026/Apr/7/sqlite-wal-docker-containers/](https://simonwillison.net/2026/Apr/7/sqlite-wal-docker-containers/) — same-host WAL confirmed working (HIGH confidence — current official research, April 2026)
- FastAPI 0.136.1: [https://pypi.org/project/fastapi/](https://pypi.org/project/fastapi/) and releasebot sources (HIGH confidence)
- SQLAlchemy 2.0.49: confirmed via search results citing April 3, 2026 release (HIGH confidence)
- APScheduler 4.0 pre-release warning: [https://pypi.org/project/APScheduler/4.0.0a1/](https://pypi.org/project/APScheduler/4.0.0a1/) — explicitly "do not use in production" (HIGH confidence — official PyPI)
- Next.js 16.2.x: [https://nextjs.org/blog/next-16-2](https://nextjs.org/blog/next-16-2) (HIGH confidence)
- echarts-for-react 3.0.6: [https://www.npmjs.com/package/echarts-for-react](https://www.npmjs.com/package/echarts-for-react) (HIGH confidence)
- TanStack Query 5.99.x: [https://www.npmjs.com/package/@tanstack/react-query](https://www.npmjs.com/package/@tanstack/react-query) (HIGH confidence)
- Ghostfolio stack reference: [https://github.com/ghostfolio/ghostfolio](https://github.com/ghostfolio/ghostfolio) — NestJS + PostgreSQL + Prisma (informs the comparison; their choice of Postgres is for multi-user SaaS, not applicable here)
- Yahoo Finance reliability: multiple 2024-2026 sources including [https://medium.com/@trading.dude/why-yfinance-keeps-getting-blocked-and-what-to-use-instead-92d84bb2cc01](https://medium.com/@trading.dude/why-yfinance-keeps-getting-blocked-and-what-to-use-instead-92d84bb2cc01) (HIGH confidence — consistent reports of IP bans and breakage)
- CoinGecko attribution requirements: [https://www.coingecko.com/en/api_terms](https://www.coingecko.com/en/api_terms) (HIGH confidence — official ToS)
- European mutual fund API gap: investigated justetf, Morningstar community forum, Twelve Data, Investing.com; no viable free ISIN-based NAV API found (MEDIUM confidence — absence of evidence is not conclusive, but consistent across all sources)
- ECharts vs Recharts vs Chart.js: [https://blog.logrocket.com/best-react-chart-libraries-2025/](https://blog.logrocket.com/best-react-chart-libraries-2025/) and npm-compare (MEDIUM confidence — benchmark claims without reproducible methodology)
- Caddy 2026 recommendation: [https://ossalt.com/guides/traefik-vs-caddy-vs-nginx-reverse-proxy-self-hosting-2026](https://ossalt.com/guides/traefik-vs-caddy-vs-nginx-reverse-proxy-self-hosting-2026) (HIGH confidence — consensus across multiple 2026 guides)
