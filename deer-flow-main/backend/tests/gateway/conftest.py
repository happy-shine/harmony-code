"""Shared fixtures for gateway tests that need a real HTTP server."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from alembic.config import Config

from alembic import command


def _run_harmony_migrations(data_dir: Path) -> None:
    """Apply alembic head so harmony.db has the schema send_message expects.

    alembic/env.py resolves the DB url from ``$HARMONY_DATA_DIR`` and
    overrides whatever we pass on Config, so we have to point that env var
    at ``data_dir`` for the duration of the migration.

    We deliberately construct :class:`Config` **without** passing
    ``alembic.ini`` — the ini file's ``[loggers]`` section would cause
    alembic's ``env.py`` to call ``logging.config.fileConfig``, which by
    default disables all pre-existing loggers and breaks unrelated
    ``caplog``-based tests downstream.
    """
    backend_root = Path(__file__).resolve().parents[2]
    prev = os.environ.get("HARMONY_DATA_DIR")
    os.environ["HARMONY_DATA_DIR"] = str(data_dir)
    try:
        cfg = Config()
        cfg.set_main_option("script_location", str(backend_root / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HARMONY_DATA_DIR", None)
        else:
            os.environ["HARMONY_DATA_DIR"] = prev


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@dataclass
class GatewayServer:
    url: str
    proc: subprocess.Popen
    session_token: str = ""

    @property
    def auth_cookies(self) -> dict[str, str]:
        """Cookie dict ready to hand to ``httpx.Client(cookies=...)``.

        Populated by the ``gateway_server`` fixture after seeding a
        ``u_default`` user + session row so legacy tests that boot a
        real server keep working under Task 5.2 auth.
        """
        return {"harmony_session": self.session_token} if self.session_token else {}


@pytest.fixture
def gateway_server(tmp_path):
    """Boot the harmony gateway in a subprocess on a free port. Waits for readiness.

    Uses HARMONY_DATA_DIR=tmp_path so each test has an isolated data root.
    """
    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    _run_harmony_migrations(tmp_path)

    env = os.environ.copy()
    env["HARMONY_DATA_DIR"] = str(tmp_path)
    # Run from backend/ so the app.gateway import path resolves
    backend_root = Path(__file__).resolve().parents[2]

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.gateway.harmony_app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=backend_root,
        env={**env, "PYTHONPATH": str(backend_root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for /docs (FastAPI default) to respond
    deadline = time.time() + 10.0
    last_err: Exception | None = None
    ready = False
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0) as c:
                r = c.get(f"{url}/docs")
                if r.status_code == 200:
                    ready = True
                    break
        except Exception as e:
            last_err = e
        time.sleep(0.1)

    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise RuntimeError(f"gateway didn't start within 10s; last error: {last_err!r}")

    # Seed a ``u_default`` user + valid session row in the subprocess's
    # data dir, so tests that use this fixture can auth with a cookie.
    token = _seed_real_server_user(tmp_path)

    try:
        yield GatewayServer(url=url, proc=proc, session_token=token)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _seed_real_server_user(data_dir: Path) -> str:
    """Insert a ``u_default`` user + a 30-day session row directly via
    SQLAlchemy against the subprocess's harmony.db. Returns the session
    token for the caller to stick in a ``harmony_session`` cookie."""
    import secrets as _secrets
    from datetime import datetime, timedelta, timezone

    import sqlalchemy as _sa

    from app.auth.passwords import hash_password
    from app.db import get_engine

    engine = get_engine(data_dir)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token = _secrets.token_hex(16)
    with engine.begin() as conn:
        # User row (ignore if already there — shouldn't happen in a fresh
        # tmp_path, but be defensive).
        existing = conn.execute(
            _sa.text("SELECT id FROM users WHERE id = :id"), {"id": "u_default"}
        ).first()
        if existing is None:
            conn.execute(
                _sa.text(
                    "INSERT INTO users (id, email, password_hash, is_admin) "
                    "VALUES (:id, :email, :ph, 0)"
                ),
                {
                    "id": "u_default",
                    "email": "default@example.com",
                    "ph": hash_password("test"),
                },
            )
        conn.execute(
            _sa.text(
                "INSERT INTO auth_sessions (id, user_id, created_at, expires_at, last_seen_at) "
                "VALUES (:id, :uid, :now, :exp, :now)"
            ),
            {"id": token, "uid": "u_default", "now": now, "exp": now + timedelta(days=30)},
        )
    return token


# ---------------------------------------------------------------------------
# Auth: make existing M3/M4 router tests pass unchanged after Task 5.2.
#
# Prior to 5.2, ``current_user_id`` was a stub that returned ``u_default``
# without any cookie check. Every gateway test was written against that
# contract. Now the dep reads a real session cookie and 401s on failure.
#
# Rather than touching every legacy test, this autouse fixture overrides
# ``current_user_id`` / ``current_user`` on the harmony app via
# ``app.dependency_overrides`` so M3/M4 tests continue to see a seeded
# ``u_default`` without needing cookies. Tests that specifically want to
# exercise the unauthenticated or real-auth path (``test_auth_router.py``,
# any integration test driving sign-in/sign-out) opt out via the
# ``no_auto_auth`` marker.
# ---------------------------------------------------------------------------


def pytest_configure(config):  # pragma: no cover - pytest hook
    config.addinivalue_line(
        "markers",
        "no_auto_auth: skip the gateway-conftest auto-login fixture (test drives auth itself)",
    )


@pytest.fixture(autouse=True)
def _auto_login_u_default(request):
    """Autouse override: short-circuit ``current_user_id`` /
    ``current_user`` to ``u_default``.

    Scoped per test so ``app.dependency_overrides`` cleanup happens even
    on failure. Tests that set ``@pytest.mark.no_auto_auth`` skip the
    override entirely and see the real behavior (401 without cookie).
    """
    if request.node.get_closest_marker("no_auto_auth"):
        yield
        return

    # Import inside the fixture so the gateway app and its deps are fully
    # registered before we touch them.
    from app.db import UserRow
    from app.gateway.deps import current_user, current_user_id
    from app.gateway.harmony_app import app

    def _override_user_id() -> str:
        return "u_default"

    def _override_user() -> UserRow:
        return UserRow(
            id="u_default",
            email="default@example.com",
            password_hash="",
            created_at=None,
            is_admin=False,
        )

    app.dependency_overrides[current_user_id] = _override_user_id
    app.dependency_overrides[current_user] = _override_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(current_user_id, None)
        app.dependency_overrides.pop(current_user, None)
