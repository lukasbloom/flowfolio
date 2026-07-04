"""APP_ENV defaults to production (fail-safe), overridable to development.

Production hardening (Swagger off, Secure cookie, boot guards) is the default so
a self-hosted install is locked down without an extra variable. A plain-HTTP
local trial opts out with an explicit APP_ENV=development. compose.yml passes
`APP_ENV=${APP_ENV:-}` (empty when unset), which must still resolve to production.

These tests drive APP_ENV through the environment (via monkeypatch) rather than
constructor kwargs, exercising the real env-read + blank-normalisation path.
conftest.py forces APP_ENV=development for the rest of the suite, so each test
here sets or clears the var itself.
"""

from app.core.config import Settings


def test_unset_defaults_to_production(monkeypatch):
    # Field absent entirely (e.g. `docker run` with no APP_ENV).
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings().app_env == "production"


def test_empty_string_resolves_to_production(monkeypatch):
    # compose.yml's `APP_ENV=${APP_ENV:-}` reaches the container as "".
    monkeypatch.setenv("APP_ENV", "")
    assert Settings().app_env == "production"


def test_whitespace_resolves_to_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "   ")
    assert Settings().app_env == "production"


def test_explicit_development_wins(monkeypatch):
    # The opt-out for a plain-HTTP local trial / the dev + test overlays.
    monkeypatch.setenv("APP_ENV", "development")
    assert Settings().app_env == "development"


def test_explicit_production_sticks(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert Settings().app_env == "production"
