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
from alembic import command
from alembic.config import Config


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
        [sys.executable, "-m", "uvicorn",
         "app.gateway.harmony_app:app",
         "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=backend_root,
        env={**env, "PYTHONPATH": str(backend_root)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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

    try:
        yield GatewayServer(url=url, proc=proc)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
