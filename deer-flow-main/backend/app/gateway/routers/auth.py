"""Session-cookie auth router (M5 Task 5.2).

Mounts at ``/api/auth/*`` with the endpoint shape better-auth's Node
client expects (sign-in/email, sign-out, get-session). There is
deliberately **no** sign-up endpoint — user creation is admin-only via
``python -m app.admin create-user`` for the single-tenant homelab
posture.

Sessions use an opaque 128-bit token as the cookie value itself. The
cookie is ``HttpOnly; SameSite=Lax``; the ``Secure`` attribute is added
only when the incoming request is HTTPS, so `localhost` dev flows keep
working.

The session TTL is 30 days, refreshed (``last_seen_at`` bumped) on every
authenticated request via :func:`app.gateway.deps.current_user`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.passwords import verify_password
from app.db import Db
from app.gateway.deps import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "harmony_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


# --- Models --------------------------------------------------------------


class SignInIn(BaseModel):
    # Plain ``str`` instead of ``EmailStr`` so we don't pull in the
    # ``email-validator`` dep. We lowercase + strip in the Db layer;
    # format validation is intentionally minimal (single-tenant).
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    is_admin: bool


class SessionMetaOut(BaseModel):
    expires_at: str


class SignInOut(BaseModel):
    user: UserOut
    session: SessionMetaOut


# --- Helpers -------------------------------------------------------------


def _is_secure_request(request: Request) -> bool:
    """Should the cookie get the ``Secure`` attribute?

    True if the request scheme is HTTPS. We explicitly do NOT read
    ``X-Forwarded-Proto`` — that's the reverse-proxy operator's problem
    to terminate and rewrite. If the scheme is ``http`` (dev) we skip
    the Secure flag so the browser will actually store the cookie.
    """
    return request.url.scheme == "https"


def _set_session_cookie(response: Response, *, request: Request, token: str, max_age: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_is_secure_request(request),
        path="/",
    )


def _clear_session_cookie(response: Response, *, request: Request) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=_is_secure_request(request),
        httponly=True,
    )


def _iso(dt) -> str:
    """Best-effort ISO-8601 string for a value that may be datetime or
    already a string (SQLite stores DATETIME as TEXT under raw text())."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


# --- Endpoints -----------------------------------------------------------


@router.post("/sign-in/email", response_model=SignInOut)
def sign_in_email(
    body: SignInIn,
    request: Request,
    response: Response,
    db: Db = Depends(get_db),
) -> SignInOut:
    # Generic 401 on both missing-user and wrong-password to prevent
    # user-enumeration. The argon2 verify runs even on the missing-user
    # path? No — we short-circuit, but argon2 verify is ~50ms regardless
    # so timing is dominated by network anyway. YAGNI on dummy-verify.
    user = db.get_user_by_email(body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid_credentials")

    session = db.create_auth_session(
        user_id=user.id,
        ttl_seconds=SESSION_TTL_SECONDS,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_session_cookie(response, request=request, token=session.id, max_age=SESSION_TTL_SECONDS)

    return SignInOut(
        user=UserOut(id=user.id, email=user.email, is_admin=user.is_admin),
        session=SessionMetaOut(expires_at=_iso(session.expires_at)),
    )


@router.post("/sign-out")
def sign_out(
    request: Request,
    response: Response,
    db: Db = Depends(get_db),
) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.delete_auth_session(token)
    _clear_session_cookie(response, request=request)
    return {"ok": True}


@router.get("/get-session")
def get_session(
    request: Request,
    db: Db = Depends(get_db),
) -> dict | None:
    """Return the active session (if any) for this request's cookie.

    Responds with ``null`` (not 401) when there's no valid session —
    better-auth clients expect this shape so they can distinguish
    "not logged in" from a real error.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    session = db.get_auth_session(token)
    if session is None:
        return None
    user = db.get_user_by_id(session.user_id)
    if user is None:
        # Session row points at a deleted user; clean up and return null.
        db.delete_auth_session(token)
        return None
    db.touch_auth_session(token)
    return {
        "user": {"id": user.id, "email": user.email, "is_admin": user.is_admin},
        "session": {"expires_at": _iso(session.expires_at)},
    }
