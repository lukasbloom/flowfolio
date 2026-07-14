"""Tests for SECRET_KEY auto-generate-and-persist bootstrap.

A clean-volume first boot with no SECRET_KEY env must generate a strong
random key, persist it to <data>/secret_key at mode 0600, and reuse that
exact key on the next boot (idempotent). When SECRET_KEY is set via env,
the bootstrap must do nothing (env overrides).

The production default-secret guard (assert_production_safety) must stay
intact: it still refuses the literal default key and FLOWFOLIO_FIXED_NOW
in production, but tolerates a generated key with app_password=None
(first-run unclaimed state).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.main import assert_production_safety, ensure_secret_key


def _settings(tmp_path, **overrides):
    base = dict(
        secret_key="change-me-in-production",
        secret_key_path=str(tmp_path / "secret_key"),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clear_secret_key_env():
    """Ensure no SECRET_KEY leaks in from the ambient environment."""
    original = os.environ.pop("SECRET_KEY", None)
    yield
    if original is not None:
        os.environ["SECRET_KEY"] = original


def test_generates_and_persists_key_when_unset(tmp_path):
    """No SECRET_KEY env + default key → generate, persist, load."""
    settings = _settings(tmp_path)
    ensure_secret_key(settings)

    path = tmp_path / "secret_key"
    assert path.exists(), "key file must be created"
    assert settings.secret_key != "change-me-in-production"
    # The loaded key is exactly the file's contents.
    assert path.read_text().strip() == settings.secret_key
    # token_urlsafe(32) yields >= 40 chars; comfortably strong.
    assert len(settings.secret_key) >= 32


def test_persisted_key_has_mode_0600(tmp_path):
    """The generated key file must be owner-read/write only (0600)."""
    settings = _settings(tmp_path)
    ensure_secret_key(settings)
    path = tmp_path / "secret_key"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_idempotent_reads_existing_key(tmp_path):
    """A second call reads the existing file instead of regenerating."""
    settings1 = _settings(tmp_path)
    ensure_secret_key(settings1)
    first_key = settings1.secret_key

    settings2 = _settings(tmp_path)
    ensure_secret_key(settings2)
    assert settings2.secret_key == first_key, "must reuse the persisted key"


def test_generates_when_secret_key_is_empty_string(tmp_path):
    """SECRET_KEY= (empty string, what compose's ${SECRET_KEY:-} yields on a clean
    install) must still auto-generate, not be mistaken for a real key."""
    settings = _settings(tmp_path, secret_key="")
    ensure_secret_key(settings)

    path = tmp_path / "secret_key"
    assert path.exists(), "empty key must trigger generation"
    assert settings.secret_key not in ("", "change-me-in-production")
    assert path.read_text().strip() == settings.secret_key


def test_env_secret_key_overrides_and_writes_no_file(tmp_path, monkeypatch):
    """SECRET_KEY set via env → do nothing, write no file (env override)."""
    monkeypatch.setenv("SECRET_KEY", "env-provided-secret")
    settings = _settings(tmp_path, secret_key="env-provided-secret")
    ensure_secret_key(settings)

    assert settings.secret_key == "env-provided-secret"
    assert not (tmp_path / "secret_key").exists(), "env path must not write a file"


def _guard_settings(**overrides):
    base = dict(
        app_env="production",
        fixed_now=None,
        secret_key="a-strong-random-secret",
        app_password=None,
        demo_mode=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_guard_rejects_default_secret_key_in_production():
    with pytest.raises(RuntimeError, match="published defaults"):
        assert_production_safety(_guard_settings(secret_key="change-me-in-production"))


def test_guard_rejects_default_password_in_production():
    with pytest.raises(RuntimeError, match="published defaults"):
        assert_production_safety(_guard_settings(app_password="changeme"))


def test_guard_rejects_fixed_now_in_production():
    with pytest.raises(RuntimeError, match="FLOWFOLIO_FIXED_NOW"):
        assert_production_safety(_guard_settings(fixed_now="2026-04-30T12:00:00Z"))


def test_guard_allows_generated_key_with_no_password():
    """First-run unclaimed state (generated key, app_password=None) is acceptable."""
    assert assert_production_safety(_guard_settings()) is None


def test_guard_allows_fixed_now_in_production_when_demo(tmp_path):
    """The public demo is production-hardened AND frozen, demo_mode exempts
    the fixed_now guard so the demo can boot with a pinned clock."""
    assert (
        assert_production_safety(
            _guard_settings(
                fixed_now="2026-04-30T12:00:00Z",
                demo_mode=True,
                app_password="demo-throwaway",
            )
        )
        is None
    )


def test_guard_still_rejects_fixed_now_in_production_when_not_demo():
    """The fixed_now exemption is demo-scoped: with demo_mode False it still raises."""
    with pytest.raises(RuntimeError, match="FLOWFOLIO_FIXED_NOW"):
        assert_production_safety(
            _guard_settings(fixed_now="2026-04-30T12:00:00Z", demo_mode=False)
        )


def test_guard_still_rejects_default_secret_even_when_demo():
    """The exemption is narrow, it does not relax the default-secrets guard.
    A default SECRET_KEY under production STILL refuses to boot even in demo mode."""
    with pytest.raises(RuntimeError, match="published defaults"):
        assert_production_safety(
            _guard_settings(secret_key="change-me-in-production", demo_mode=True)
        )


def test_guard_rejects_sub_8_char_app_password_in_production():
    """A claimed instance with a weak APP_PASSWORD lingering in the env must
    also be refused, not just the unclaimed pre-seed path."""
    with pytest.raises(RuntimeError, match="shorter than 8 characters"):
        assert_production_safety(_guard_settings(app_password="short7x"))


def test_guard_allows_empty_app_password_in_production():
    """compose.yml's APP_PASSWORD=${APP_PASSWORD:-} arrives as an empty
    string, not None, on an unset host var. That is the unclaimed first-run
    state and must pass, not brick every default `docker compose up`."""
    assert assert_production_safety(_guard_settings(app_password="")) is None


def test_guard_allows_sub_8_char_app_password_in_development():
    """The floor is a production-only guard, dev trials keep working."""
    assert (
        assert_production_safety(
            _guard_settings(app_env="development", app_password="short7x")
        )
        is None
    )
