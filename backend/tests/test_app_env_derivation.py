"""APP_ENV derives from DOMAIN when not set explicitly.

A VPS install that sets DOMAIN (HTTPS via Caddy) must get production
behaviour (Swagger off, Secure session cookie) without remembering a second
variable. An explicit APP_ENV always wins so TLS-elsewhere setups (reverse
proxy in front of the container) and domain-with-Swagger debugging both
stay expressible.
"""

from app.core.config import Settings


def _settings(**kwargs) -> Settings:
    # Both fields passed explicitly so ambient env vars / .env cannot leak in.
    base = {"app_env": "", "domain": None}
    base.update(kwargs)
    return Settings(**base)


def test_no_domain_no_app_env_is_development():
    assert _settings().app_env == "development"


def test_domain_without_app_env_is_production():
    assert _settings(domain="flowfolio.example.com").app_env == "production"


def test_explicit_development_beats_domain():
    s = _settings(app_env="development", domain="flowfolio.example.com")
    assert s.app_env == "development"


def test_explicit_production_without_domain_sticks():
    assert _settings(app_env="production").app_env == "production"


def test_empty_string_domain_counts_as_unset():
    # compose.yml passes DOMAIN=${DOMAIN:-}, which is "" on a local trial.
    assert _settings(domain="").app_env == "development"
