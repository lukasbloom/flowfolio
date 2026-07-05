from app.core import auth


def test_token_carries_epoch_and_validates_against_matching_epoch():
    t = auth.create_session_token(token_epoch=3)
    assert auth.session_token_epoch(t) == 3
    assert auth.validate_session_token(t, current_epoch=3) is True
    assert auth.validate_session_token(t, current_epoch=4) is False


def test_legacy_token_without_epoch_claim_validates_against_zero():
    # A token minted the old way (no epoch claim) must survive at epoch 0.
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.core.config import settings
    legacy = jwt.encode(
        {"sub": "user", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        settings.secret_key, algorithm=auth.ALGORITHM,
    )
    assert auth.validate_session_token(legacy, current_epoch=0) is True


def test_pre_auth_token_roundtrip():
    t = auth.create_pre_auth_token()
    assert auth.validate_pre_auth_token(t) is True
    # A normal session token is NOT a valid pre-auth token (stage claim differs).
    assert auth.validate_pre_auth_token(auth.create_session_token(0)) is False
