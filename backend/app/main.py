import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from alembic import command
from alembic.config import Config as AlembicConfig
from app.core.config import settings
from app.core.database import Base, engine
from app.core.http_guard import install_http_guard
from app.middleware.auth_middleware import AuthMiddleware
from app.routers import allocation, closed, concentration, contributions, realized, tags, update
from app.routers import settings as settings_router
from app.routers.accounts import router as accounts_router
from app.routers.apy_config import router as apy_config_router
from app.routers.auth import router as auth_router
from app.routers.config import router as config_router
from app.routers.fx import router as fx_router
from app.routers.instruments import router as instruments_router
from app.routers.keys import router as keys_router
from app.routers.networth import router as networth_router
from app.routers.perf import router as perf_router
from app.routers.prices import router as prices_router
from app.routers.reconciliation import router as reconciliation_router
from app.routers.setup import router as setup_router
from app.routers.trades import router as trades_router
from app.routers.transactions import router as transactions_router
from app.services.scheduler import shutdown_scheduler, start_scheduler


def ensure_secret_key(settings) -> None:  # type: ignore[no-untyped-def]
    """Auto-generate and persist a strong SECRET_KEY on a clean volume.

    When SECRET_KEY is provided via env, do nothing — env overrides. Otherwise,
    when settings.secret_key is still the published default, either read an
    already-persisted key from settings.secret_key_path (idempotent across
    restarts) or generate a new one with secrets.token_urlsafe(32), write it to
    that path at mode 0600, and assign it onto settings.secret_key.

    Runs BEFORE assert_production_safety so the guard sees the real key, not the
    default. Lets a stranger install with zero env vars.
    """
    # Env always wins. If SECRET_KEY is set, the loaded settings already
    # carry it and we must not touch the volume.
    if os.environ.get("SECRET_KEY"):
        return
    # An empty secret_key (compose's `${SECRET_KEY:-}` yields the empty string
    # SECRET_KEY= on a clean install, which pydantic loads over the default) is
    # NOT a real key — treat it like the published default and auto-generate,
    # matching compose.yml's documented "auto-gen if unset" contract. The
    # public demo relies on this to get a per-instance key after the
    # overlay blanks the inherited operator SECRET_KEY.
    if settings.secret_key and settings.secret_key != "change-me-in-production":
        return

    path = Path(settings.secret_key_path)
    if path.exists():
        settings.secret_key = path.read_text().strip()
        return

    key = secrets.token_urlsafe(32)
    # Constrain the parent dir to owner-only when the app owns it, so a shared
    # volume can't list/traverse to the key file.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        # Best-effort: a pre-existing dir we don't own may reject chmod. The
        # 0o600 file mode below is the real protection for the key itself.
        pass
    # Create the file atomically at mode 0600 (O_CREAT|O_EXCL) so the JWT signing
    # key is never momentarily world/group-readable under the process umask.
    # O_EXCL guards the create-then-chmod window; if a concurrent boot
    # already created the file we fall back to reading it.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        settings.secret_key = path.read_text().strip()
        return
    try:
        os.write(fd, key.encode())
    finally:
        os.close(fd)
    settings.secret_key = key


def assert_production_safety(settings) -> None:  # type: ignore[no-untyped-def]
    """Refuse to boot in production with dangerous defaults.

    Extracted as a standalone function so it can be unit-tested hermetically
    without standing up the full lifespan. Called from lifespan() at startup.
    """
    # Startup guard: refuse to boot if production accidentally has a pinned clock.
    # This combination is a copy/paste foot-gun from compose.test.yml — it would silently
    # serve the entire production app from "2026-04-30T12:00:00Z" until someone noticed.
    # The public demo is production-hardened AND frozen, so demo_mode is the
    # only exemption to this guard. The narrowing is scoped to the fixed_now check;
    # the default-secrets guard below is untouched — the demo still needs a real
    # auto-generated SECRET_KEY and a non-changeme APP_PASSWORD.
    if settings.app_env == "production" and settings.fixed_now and not settings.demo_mode:
        raise RuntimeError(
            "FLOWFOLIO_FIXED_NOW is set with APP_ENV=production. "
            "Refusing to boot — this combination is a copy/paste foot-gun from compose.test.yml. "
            "Unset FLOWFOLIO_FIXED_NOW in the production environment."
        )

    # Refuse to boot with the published default secrets. A default SECRET_KEY
    # lets anyone forge session tokens; a default APP_PASSWORD is a trivial login.
    # app_password is now str | None: None is the acceptable unclaimed
    # first-run state (the setup wizard claims it), so only the LITERAL "changeme"
    # default is refused here.
    if settings.app_env == "production" and (
        settings.secret_key == "change-me-in-production"
        or settings.app_password == "changeme"
    ):
        raise RuntimeError(
            "SECRET_KEY and/or APP_PASSWORD are still at their published defaults "
            "with APP_ENV=production. Refusing to boot — set strong values in .env. "
            "A default SECRET_KEY lets anyone forge session tokens."
        )

    # Refuse a sub-32-char SECRET_KEY in production. PyJWT raises
    # InsecureKeyLengthWarning below that floor because a short HS256 key
    # weakens every session token it signs, and the check above only refuses
    # the published default, not an operator-chosen five-character key.
    # Empty/unset stays governed by the default-value check above.
    # ensure_secret_key runs before this guard and always auto-generates a
    # 32+ byte key, so only a truthy, too-short, non-default value trips this
    # check. Mirrors the APP_PASSWORD floor below.
    if (
        settings.app_env == "production"
        and settings.secret_key
        and len(settings.secret_key) < 32
    ):
        raise RuntimeError(
            "SECRET_KEY is shorter than 32 characters. A short HS256 key "
            "weakens every session token. Generate a strong one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))" '
            "and set it in .env."
        )

    # Refuse a sub-8-char APP_PASSWORD in production. The interactive setup and
    # the pre-seed both enforce this floor; this catches the case where the DB
    # is already claimed but the operator keeps a weak APP_PASSWORD in the env
    # expecting it to be authoritative.
    # None or empty string means unclaimed, first-run wizard state, and must
    # pass this guard, mirroring pre_seed_admin_password_from_env's `if not
    # app_password: return` (compose.yml's `APP_PASSWORD=${APP_PASSWORD:-}`
    # arrives as an empty string, not None, on an unset host var).
    if (
        settings.app_env == "production"
        and settings.app_password
        and len(settings.app_password) < 8
    ):
        raise RuntimeError(
            "APP_PASSWORD is shorter than 8 characters. The interactive setup "
            "enforces this minimum; the env pre-seed does too. Set a longer "
            "APP_PASSWORD or unset it and claim the password via first-run setup."
        )


def _alembic_upgrade_head() -> None:
    # Run Alembic migrations to head on startup. Idempotent.
    # Synchronous because Alembic does not expose an async API; engine
    # connections inside Alembic env.py use the sync driver.
    alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = AlembicConfig(str(alembic_ini))
    cfg.set_main_option("script_location", str(alembic_ini.parent / "alembic"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Auto-generate + persist SECRET_KEY on a clean volume BEFORE the
    # production guard, so assert_production_safety sees the generated key
    # rather than the published default.
    ensure_secret_key(settings)

    # Refuse to boot in production with dangerous defaults / pinned clock.
    assert_production_safety(settings)

    # Hermetic-network guard install.
    # When FLOWFOLIO_NETWORK_HERMETIC=true (compose.test.yml only), monkey-patch
    # httpx.AsyncClient to raise HermeticNetworkViolation for any call to an
    # external pricing/FX host. No-op in production (network_hermetic defaults False).
    if settings.network_hermetic:
        install_http_guard()

    # Boot-seed and scheduled reset are the same swap. In demo mode, swap
    # the pristine seed in BEFORE migrations so every boot starts from a clean,
    # secret-free synthetic demo. Sequencing rationale: the swap is a sequential
    # awaited file op, so alembic then runs against the freshly-swapped seed (a
    # no-op when the baked seed is already at head, safe if it lags), create_all
    # is a no-op, and the existing pre_seed_admin_password_from_env block below
    # re-claims the unclaimed seed using settings.app_password so the shared demo
    # session stays valid. No second re-claim is added, and the swap MUST stay
    # before that block (a later swap would wipe the claim). Startup is
    # single-threaded, so nothing races the swap.
    if settings.demo_mode:
        from app.services.demo_reset import swap_demo_seed

        await swap_demo_seed()

    # Apply migrations first so any new columns/tables exist before
    # create_all's no-op pass and before any router serves requests.
    # alembic env.py uses asyncio.run(), which cannot nest inside the
    # lifespan loop — run in a worker thread so it gets its own loop.
    await asyncio.to_thread(_alembic_upgrade_head)
    # Safety net: create_all picks up tables that exist in models but were
    # never added to a migration (defensive only — Alembic is authoritative).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Pre-seed the admin password from APP_PASSWORD if set, so an
    # automated/headless deploy is claimed before
    # serving requests and never shows the setup wizard. No-op when
    # APP_PASSWORD is unset or the instance is already claimed.
    from app.core.database import async_session_factory
    from app.services.key_store import load_key_cache
    from app.services.setup_state import get_token_epoch, pre_seed_admin_password_from_env

    async with async_session_factory() as session:
        await pre_seed_admin_password_from_env(session, settings.app_password)
        await session.commit()
        # Boot-load half: populate the in-process key resolver cache from
        # the persisted user_setting rows (read-only). Runs after migrations so
        # user_setting exists; the write-invalidate half lives in set_key/clear_key.
        await load_key_cache(session)
        # Cache the token epoch on app.state so AuthMiddleware never needs a
        # per-request DB read to validate a session. Whatever bumps the
        # stored epoch (e.g. a password change) must also update
        # app.state.token_epoch in-process to take effect immediately.
        app.state.token_epoch = await get_token_epoch(session)

    # Suppress APScheduler on the test stack. Cron triggers use the asyncio
    # event-loop clock (real time), so jobs would otherwise fire mid-suite and
    # corrupt the golden state. Production default stays True.
    if settings.scheduler_enabled:
        start_scheduler(app)
    try:
        yield
    finally:
        if settings.scheduler_enabled:
            shutdown_scheduler(app)
        await engine.dispose()


# Disable Swagger and the OpenAPI schema endpoint in production so endpoint
# signatures are not publicly exposed. In development they remain enabled.
_is_production = settings.app_env == "production"
_docs_url = None if _is_production else "/api/docs"
_openapi_url = None if _is_production else "/api/openapi.json"


app = FastAPI(
    title="Flowfolio API",
    version="0.1.0",
    docs_url=_docs_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
)

# Safe default so AuthMiddleware's getattr(request.app.state, "token_epoch", 0)
# always has a value even in tests that hit the app via ASGITransport without
# running the lifespan (httpx's ASGITransport does not send lifespan events).
# The real lifespan above overwrites this with the DB-backed epoch at boot.
app.state.token_epoch = 0

# IMPORTANT: middleware order matters in Starlette — last added is the
# outermost wrapper. AuthMiddleware must wrap the routers but sit inside
# CORSMiddleware so preflight OPTIONS requests are not blocked. Ordering only
# matters when CORS is registered at all, which is dev-only (see below).
app.add_middleware(AuthMiddleware)

# Dev-only CORS: a bare `next dev` on the host (port 3000) hitting the API
# directly is the only cross-origin consumer. Production is same-origin
# behind Caddy and must not ship a credentialed CORS grant.
if settings.app_env != "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Auth router (login/logout) is exempted inside AuthMiddleware.
app.include_router(auth_router)
# Boot-flags config: unauthenticated {demo, app_version} the
# frontend reads before any session — exempted inside AuthMiddleware.
app.include_router(config_router)
# First-run setup router (status/claim) — exemption handled in AuthMiddleware.
app.include_router(setup_router)
# Data routers — gated by AuthMiddleware (require valid session cookie).
app.include_router(accounts_router)
app.include_router(instruments_router)
app.include_router(transactions_router)
app.include_router(fx_router)
app.include_router(apy_config_router)
app.include_router(prices_router)
app.include_router(perf_router)
app.include_router(networth_router)
app.include_router(trades_router)
app.include_router(allocation.router)
app.include_router(concentration.router)
app.include_router(closed.router)
app.include_router(contributions.router)
app.include_router(realized.router)
app.include_router(reconciliation_router)
app.include_router(settings_router.router)
# API-key configuration surface: GET status + test-then-persist PUT +
# standalone test + clear. Session-gated like the other data routers.
app.include_router(keys_router)
app.include_router(tags.tags_router)
app.include_router(tags.holding_tags_router)
# Update check: /api/version (build-stamped) + the /api/update surface
# (status, check, dismiss). Both are AuthMiddleware-gated like the other data routers.
app.include_router(update.version_router)
app.include_router(update.router)


@app.get("/api/healthcheck")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}
