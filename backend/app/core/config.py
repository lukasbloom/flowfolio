from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:////data/flowfolio.db"
    secret_key: str = "change-me-in-production"
    # Where the auto-generated SECRET_KEY is persisted on a clean volume.
    # main.ensure_secret_key writes a strong random key here (mode 0600) when
    # SECRET_KEY is unset, so a stranger can install with zero required env vars.
    secret_key_path: str = "/data/secret_key"
    session_expire_seconds: int = 86400 * 7  # 7 days
    log_level: str = "WARNING"

    # Single-user auth. The admin password lives in the DB
    # (user_setting.admin_password_hash); APP_PASSWORD is an OPTIONAL boot-time
    # pre-seed for automated/headless deploys. When unset
    # the first-run setup wizard claims the password. None is the acceptable
    # unclaimed first-run state; the literal "changeme" default is still refused
    # in production by assert_production_safety.
    app_password: str | None = None

    # The public hostname Caddy serves HTTPS for (same DOMAIN env the compose
    # stack hands to Caddy). Only read here to derive app_env below.
    domain: str | None = None

    # APP_ENV gates production-only behaviour:
    # - "production": Swagger /api/docs and /api/openapi.json are disabled,
    #                 cookies are sent with `secure=True` (HTTPS-only via Caddy)
    # - "development": Swagger enabled, cookies allow plain HTTP
    # Unset derives from DOMAIN: a domain means HTTPS is live, so production
    # hardening switches on without a second variable. An explicit value always
    # wins (e.g. APP_ENV=production behind an external TLS proxy with no DOMAIN,
    # or APP_ENV=development to debug Swagger on a domain install).
    app_env: str = ""

    @model_validator(mode="after")
    def _derive_app_env(self) -> "Settings":
        if not self.app_env:
            self.app_env = "production" if self.domain else "development"
        return self

    # Clock pin for snapshot test runs. In production both stay
    # at their default; compose.test.yml sets these for the hermetic suite.
    # See backend/app/core/clock.py and backend/app/main.py for consumers.
    fixed_now: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FLOWFOLIO_FIXED_NOW", "fixed_now"),
    )  # FLOWFOLIO_FIXED_NOW (ISO-8601, e.g. "2026-04-30T12:00:00Z")
    scheduler_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("FLOWFOLIO_SCHEDULER_ENABLED", "scheduler_enabled"),
    )  # FLOWFOLIO_SCHEDULER_ENABLED — false in compose.test.yml

    # Keys guardrail. When DEMO_MODE=true the keys surface is
    # hidden + write-locked (the public demo ships pre-configured keys nobody
    # may overwrite). Default False in a normal self-hosted install.
    demo_mode: bool = False  # DEMO_MODE

    # Hermetic outbound network on the test stack.
    # When True, httpx calls to FORBIDDEN_HOSTS (Finnhub/CoinGecko/Frankfurter/
    # Binance/Alpha Vantage/FT) raise rather than reach the wire. Set via
    # FLOWFOLIO_NETWORK_HERMETIC=true in compose.test.yml. Default False in
    # production — pricing fetches must reach upstream.
    network_hermetic: bool = Field(
        default=False,
        validation_alias=AliasChoices("FLOWFOLIO_NETWORK_HERMETIC", "network_hermetic"),
    )  # FLOWFOLIO_NETWORK_HERMETIC — true in compose.test.yml only

    # Use NullPool (no connection pooling) on the test
    # stack so that every request opens a fresh SQLite connection and always
    # sees the current inode after `test_db_reset.sh` atomically swaps the
    # database file. Pool connections hold references to the OLD inode even
    # after `mv -f`, causing transient "0 buckets" responses when the test
    # suite issues rapid consecutive resets. NullPool eliminates the race.
    # Set via FLOWFOLIO_NULL_POOL=true in compose.test.yml. Default False in
    # production — pooling is desirable under normal concurrent load.
    null_pool: bool = Field(
        default=False,
        validation_alias=AliasChoices("FLOWFOLIO_NULL_POOL", "null_pool"),
    )  # FLOWFOLIO_NULL_POOL — true in compose.test.yml only

    # How often the demo reset cron swaps the pristine seed back
    # over the live DB. Inert unless DEMO_MODE is on — the scheduler reads this
    # only in the demo branch. Default 6h balances "stays tidy"
    # against "don't yank the rug from an exploring visitor".
    demo_reset_interval_hours: int = Field(
        default=6,
        validation_alias=AliasChoices("FLOWFOLIO_DEMO_RESET_HOURS", "demo_reset_interval_hours"),
    )  # FLOWFOLIO_DEMO_RESET_HOURS — demo-only reset cadence

    # The pricing/GitHub API keys no longer live here. They moved
    # to the DB-backed key store (app.services.key_store) and are resolved at call
    # time via get_api_key, so there is no env path to inject a key (DB-only).
    pricing_timeout_seconds: float = 10.0

    # In-scheduler backup settings. Optional with
    # None defaults, mirroring the pricing-key convention above. Env names are
    # uppercase (BACKUP_ENCRYPTION_KEY etc.) and match field names case-insensitively.
    backup_encryption_key: str | None = None
    backup_dest: str | None = None
    backup_retain_days: int = 30

    # Self-update. APP_VERSION is baked at build time via the release
    # workflow's --build-arg; falls back to "dev" for local/untagged builds.
    app_version: str = "dev"
    github_repo: str = "lukasbloom/flowfolio"  # source of truth for the releases check
    # GITHUB_TOKEN moved to the DB key store; resolved via get_api_key("github").

    # The shared named-volume mount where the app drops
    # request.json and reads the updater's status.json. Matches the
    # UPDATE_CHANNEL_DIR mount in compose.yml. The app writes a file
    # here and NEVER touches the Docker socket — the updater sidecar acts on it.
    update_channel_dir: str = "/update"

    @field_validator("app_version")
    @classmethod
    def _blank_version_is_dev(cls, v: str) -> str:
        # An empty/blank APP_VERSION (e.g. unset build-arg) means an untagged
        # build, fall back to "dev" rather than reporting "".
        return v.strip() or "dev"


settings = Settings()
