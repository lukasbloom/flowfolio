"""TOTP helpers (authenticator-app 2FA). Thin wrapper over pyotp + a segno QR."""
from __future__ import annotations

import pyotp
import segno

_WINDOW = 1  # accept the adjacent 30s step each side (clock drift)


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str = "admin", issuer: str = "Flowfolio") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def verify_code(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=_WINDOW)


def qr_svg_data_uri(otpauth_uri: str) -> str:
    """Inline SVG data URI for the otpauth URI — rendered client-side, no external call."""
    return segno.make(otpauth_uri).svg_data_uri()
