## Project

**Flowfolio**

A self-hosted personal portfolio tracker web app. Lets a single user record buy/sell transactions across stocks, ETFs/funds, crypto, and stablecoins held across multiple accounts, with live price fetching, multi-currency support (EUR/USD), and yield accrual for APY-bearing positions. Replaces a static spreadsheet that captures snapshots only, capturing the full transaction history so it can answer "which holding has performed best over time?"

**Core Value:** When I open the app, I can compare the performance (% return + time-weighted return) of every holding I own across multiple timeframes, so I know which investments are actually worth owning over time.

### Constraints

- **Privacy**: Self-hosted on the user's own infrastructure, never a hosted SaaS. Financial data never leaves it.
- **Single user**: No multi-tenant concerns; auth is a single account with a strong password.
- **Tech stack**: FastAPI (Python 3.12+) + SQLite (WAL mode) + Next.js (React) + Apache ECharts. Decimal arithmetic via Python `decimal` stdlib end-to-end. Schema migrations via Alembic. Background jobs via APScheduler in-process. Ships as one s6-supervised container (`compose.yml`); dev and test run the 4-service split (`compose.multi.yml`). SQLite lives as a file in the `db_data` named volume either way.
- **Pricing data**: Free-tier sources only. European mutual funds AND ETFs/ETCs (incl. the gold ETC) are priced by scraping FT.com tear-sheets (`app/services/pricing/ft_scraper.py`), funds keyed by ISIN, ETFs/metals by exchange ticker (e.g. `VUSA:GER`, `EGLN:LSE`). Manual NAV override remains the fallback.
- **Cost**: Hobby project. Operating cost should sit comfortably alongside an existing VPS; no paid data feeds.
- **Form factor**: Responsive web app, must work well on mobile browsers without a PWA.

## Stack

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
| Auth | Single bcrypt-hashed password + HTTP-only session cookie | |
| Tooling | Ruff (lint + format), mypy, ESLint + Prettier | |

## Price & FX Data Sources

| Asset class | Live source | History source |
|-------------|-------------|----------------|
| Stocks / US ETFs | Finnhub (60 calls/min free) | Finnhub, Twelve Data fallback |
| Crypto | CoinGecko Demo (~30 calls/min, 10k/month) | Binance `/api/v3/klines`, no key |
| FX | Frankfurter (ECB rates, no key, updates each working day ~16:00 CET) | Frankfurter, back to 1999 |
| EU funds / ETFs / gold ETC | FT.com tear-sheet scrape | Yahoo, backfill only |

CoinGecko's free tier requires the attribution "Data provided by CoinGecko" with a link.

### FT scrape (funds, ETFs, ETC)

`app/services/pricing/ft_scraper.py` scrapes the FT.com tear-sheet `mod-ui-data-list__value` span, currency-converted to EUR via the `:EUR` suffix. Funds resolve by ISIN (in `ticker_override`, else `symbol`); ETFs and the gold ETC resolve by exchange ticker in `ticker_override` (`VUSA:GER`, `SXR8:GER`, `SXRV:GER`, `EQQQ:GER`, `EGLN:LSE`). `allowed_sources_for` permits `ft` for `fund`, `etf`, and `metal`. There is no free official API covering EU UCITS by ISIN, which is why this is a scrape.

**Live/history split (mirrors finnhub→twelve_data):** FT serves only the *current* NAV (no history endpoint), so `ft` instruments take their HISTORY from Yahoo via `app/services/pricing/yahoo.py` (`backfill.py` `ft` branch). ETFs/metal resolve to a Yahoo exchange symbol (`VUSA:GER`→`VUSA.DE`, `EGLN:LSE`→`EGLN.L`); funds resolve their ISIN via Yahoo search to a Morningstar NAV symbol (`0P……F`). Yahoo is HISTORY-ONLY and never wired into the daily scheduler. `fetch_yahoo_history` asserts the chart currency is EUR. TWRR needs ≥`INSUFFICIENT_HISTORY_DAYS` (7) distinct price-days, which the Yahoo backfill supplies (~760/instrument).

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

## Hard rules

- **Never use `float` or JavaScript `number` for money.** Python `decimal.Decimal` end to end; `Numeric(18, 8)` in SQLAlchemy.
- **Yahoo is history-only.** Never wire it into the scheduler or any live path. The ban risk is repeated polling, and a burst of test calls already got the dev IP throttled once. The backfill spaces calls and backs off.
- **APScheduler stays on 3.x.** 4.0 is a pre-release marked "do not use in production".
