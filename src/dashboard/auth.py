"""Authentication helpers for the WealthAgent dashboard.

Uses itsdangerous.URLSafeTimedSerializer to sign session tokens stored
in httponly cookies.  Single-user: the secret is DASHBOARD_SECRET_KEY.
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.exceptions import HTTPException
from starlette.requests import Request

from config.settings import settings

_COOKIE_NAME = "wa_session"
_TOKEN_PAYLOAD = "authenticated"
_MAX_AGE = 86400  # 24 hours


def get_signer() -> URLSafeTimedSerializer:
    """Return a signer using DASHBOARD_SECRET_KEY.

    Raises RuntimeError if DASHBOARD_SECRET_KEY is not set.
    """
    key = settings.dashboard_secret_key
    if not key:
        raise RuntimeError("DASHBOARD_SECRET_KEY is not set")
    return URLSafeTimedSerializer(key)


def create_session_token() -> str:
    """Create a signed session token."""
    return get_signer().dumps(_TOKEN_PAYLOAD)


def verify_session_token(token: str) -> bool:
    """Return True if token is valid and not expired (max_age=86400 seconds = 24h)."""
    try:
        get_signer().loads(token, max_age=_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def verify_password(password: str) -> bool:
    """Return True if password matches DASHBOARD_SECRET_KEY."""
    key = settings.dashboard_secret_key
    return key is not None and password == key


async def require_auth(request: Request) -> None:
    """FastAPI dependency: raises HTTPException(302) redirecting to /login if not authenticated."""
    token = request.cookies.get(_COOKIE_NAME)
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
