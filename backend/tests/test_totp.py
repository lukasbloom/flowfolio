import pyotp
from app.services import totp


def test_generate_secret_is_base32_and_usable():
    secret = totp.generate_secret()
    assert isinstance(secret, str) and len(secret) >= 16
    # A code generated from the secret verifies.
    code = pyotp.TOTP(secret).now()
    assert totp.verify_code(secret, code) is True


def test_verify_rejects_wrong_code():
    secret = totp.generate_secret()
    assert totp.verify_code(secret, "000000") is False


def test_provisioning_uri_shape():
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", account="admin", issuer="Flowfolio")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=Flowfolio" in uri


def test_qr_svg_data_uri_is_inline_svg():
    uri = totp.qr_svg_data_uri("otpauth://totp/Flowfolio:admin?secret=JBSWY3DPEHPK3PXP&issuer=Flowfolio")
    assert uri.startswith("data:image/svg+xml")
