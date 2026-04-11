"""Unit tests for src/dashboard/auth.py."""

import asyncio
from unittest.mock import MagicMock

import pytest
from itsdangerous import SignatureExpired, URLSafeTimedSerializer
from starlette.exceptions import HTTPException

from dashboard.auth import (
    create_session_token,
    get_signer,
    require_auth,
    verify_password,
    verify_session_token,
)

# ---------------------------------------------------------------------------
# get_signer
# ---------------------------------------------------------------------------


def test_get_signer_raises_without_key(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", None)
    with pytest.raises(RuntimeError, match="DASHBOARD_SECRET_KEY"):
        get_signer()


def test_get_signer_returns_serializer(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    signer = get_signer()
    assert isinstance(signer, URLSafeTimedSerializer)


# ---------------------------------------------------------------------------
# create_session_token
# ---------------------------------------------------------------------------


def test_create_session_token_returns_str(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    token = create_session_token()
    assert isinstance(token, str)
    assert len(token) > 0


# ---------------------------------------------------------------------------
# verify_session_token
# ---------------------------------------------------------------------------


def test_verify_valid_token(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    token = create_session_token()
    assert verify_session_token(token) is True


def test_verify_invalid_token(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    assert verify_session_token("this-is-garbage") is False


def test_verify_expired_token(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    signer = URLSafeTimedSerializer("mysecret")
    token = signer.dumps("authenticated")

    original_get_signer = get_signer

    def mock_signer():
        s = original_get_signer()

        def expiring_loads(payload, max_age=None):  # noqa: ARG001
            raise SignatureExpired("token expired", payload=payload, date_signed=None)

        s.loads = expiring_loads
        return s

    monkeypatch.setattr("dashboard.auth.get_signer", mock_signer)
    assert verify_session_token(token) is False


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------


def test_verify_password_correct(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "correctpass")
    assert verify_password("correctpass") is True


def test_verify_password_wrong(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "correctpass")
    assert verify_password("wrongpass") is False


def test_verify_password_no_key(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", None)
    assert verify_password("anypass") is False


# ---------------------------------------------------------------------------
# require_auth
# ---------------------------------------------------------------------------


def test_require_auth_passes_with_cookie(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    token = create_session_token()
    request = MagicMock()
    request.cookies = {"wa_session": token}
    result = asyncio.run(require_auth(request))
    assert result is None


def test_require_auth_redirects_no_cookie(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    request = MagicMock()
    request.cookies = {}
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_auth(request))
    assert exc_info.value.status_code == 302
    assert exc_info.value.headers["Location"] == "/login"


def test_require_auth_redirects_bad_cookie(monkeypatch):
    monkeypatch.setattr("config.settings.settings.dashboard_secret_key", "mysecret")
    request = MagicMock()
    request.cookies = {"wa_session": "bad-token"}
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_auth(request))
    assert exc_info.value.status_code == 302
    assert exc_info.value.headers["Location"] == "/login"
