"""Task 4.3: Uploads router tests.

Covers the harmony rewrite of ``/api/threads/{tid}/uploads``:

* writes go to ``<HARMONY_DATA_DIR>/threads/<tid>/user-data/uploads/``
  (the same dir ``create_thread`` pre-creates in ``messages.py``),
* a row is inserted into ``uploads`` per file,
* filename safety (traversal, NUL, empty),
* 100 MB size cap (patched down for the test),
* list newest-first, delete-removes-row-and-file + DB-first semantics.

Security-critical — the filename tests must fail loudly before any
production deploy, same as the workspace router.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.db import Db, get_engine


BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_migrations(data_dir: Path) -> None:
    prev = os.environ.get("HARMONY_DATA_DIR")
    os.environ["HARMONY_DATA_DIR"] = str(data_dir)
    try:
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HARMONY_DATA_DIR", None)
        else:
            os.environ["HARMONY_DATA_DIR"] = prev


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    from app.gateway.harmony_app import app

    return TestClient(app), tmp_path


def _new_thread(c: TestClient) -> str:
    r = c.post("/api/threads", json={})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _uploads_dir(tmp_path: Path, tid: str) -> Path:
    return tmp_path / "threads" / tid / "user-data" / "uploads"


# --- POST /uploads --------------------------------------------------------


def test_upload_single_file_writes_to_thread_uploads_dir(client):
    c, tmp = client
    tid = _new_thread(c)

    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("hello.txt", b"hi there", "text/plain"))],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list) and len(body) == 1
    entry = body[0]
    assert entry["id"].startswith("up_")
    assert entry["filename"] == "hello.txt"
    assert entry["size"] == len(b"hi there")
    assert entry["content_type"] == "text/plain"

    on_disk = _uploads_dir(tmp, tid) / "hello.txt"
    assert on_disk.exists()
    assert on_disk.read_bytes() == b"hi there"


def test_upload_inserts_db_row(client):
    c, tmp = client
    tid = _new_thread(c)
    c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("a.txt", b"abc", "text/plain"))],
    )
    db = Db(get_engine(tmp))
    rows = db.list_uploads_for_thread(tid)
    assert len(rows) == 1
    row = rows[0]
    assert row.thread_id == tid
    assert row.filename == "a.txt"
    assert row.size == 3
    assert row.content_type == "text/plain"


def test_upload_multiple_files(client):
    c, tmp = client
    tid = _new_thread(c)
    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[
            ("files", ("one.txt", b"1", "text/plain")),
            ("files", ("two.bin", b"\x00\x01\x02", "application/octet-stream")),
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2
    names = sorted(e["filename"] for e in body)
    assert names == ["one.txt", "two.bin"]

    ud = _uploads_dir(tmp, tid)
    assert (ud / "one.txt").read_bytes() == b"1"
    assert (ud / "two.bin").read_bytes() == b"\x00\x01\x02"

    db = Db(get_engine(tmp))
    assert len(db.list_uploads_for_thread(tid)) == 2


def test_upload_dot_filename_rejected(client):
    """``.`` is a directory ref, not a file."""
    c, _ = client
    tid = _new_thread(c)
    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", (".", b"x", "text/plain"))],
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_filename"


def test_upload_traversal_rejected(client):
    c, _ = client
    tid = _new_thread(c)
    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("../evil.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_filename"


def test_upload_absolute_path_rejected(client):
    c, _ = client
    tid = _new_thread(c)
    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("/etc/passwd", b"x", "text/plain"))],
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_filename"


def test_upload_backslash_path_rejected(client):
    """Windows-style separator: reject cross-platform so same-host Windows
    clients can't sneak a ``..\\`` past a POSIX gateway."""
    c, _ = client
    tid = _new_thread(c)
    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("sub\\evil.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_filename"


def test_normalize_filename_rejects_nul_and_empty():
    """Unit cover for :func:`_normalize_filename`.

    HTTP clients (httpx/TestClient) strip or percent-encode NUL bytes in
    multipart filenames before the router ever sees them, so the HTTP
    layer can't exercise the NUL branch. Exercise it directly.
    """
    from app.gateway.routers.uploads import _normalize_filename

    with pytest.raises(ValueError):
        _normalize_filename("bad\x00name.txt")
    with pytest.raises(ValueError):
        _normalize_filename("")
    with pytest.raises(ValueError):
        _normalize_filename(None)
    with pytest.raises(ValueError):
        _normalize_filename(".")
    with pytest.raises(ValueError):
        _normalize_filename("..")
    with pytest.raises(ValueError):
        _normalize_filename("a/b.txt")
    with pytest.raises(ValueError):
        _normalize_filename("a\\b.txt")
    with pytest.raises(ValueError):
        _normalize_filename("x" * 256)

    # Happy path: unchanged return.
    assert _normalize_filename("ok.txt") == "ok.txt"
    assert _normalize_filename("  spaced.txt  ") == "spaced.txt"
    assert _normalize_filename("x" * 255) == "x" * 255


def test_upload_nonexistent_thread_404(client):
    c, _ = client
    r = c.post(
        "/api/threads/t_does_not_exist/uploads",
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 404


def test_upload_too_large_413(client, monkeypatch):
    """Patch the 100 MB constant down to a small value and exceed it."""
    import app.gateway.routers.uploads as uploads_mod

    monkeypatch.setattr(uploads_mod, "MAX_UPLOAD_BYTES", 10)
    c, _ = client
    tid = _new_thread(c)
    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("big.bin", b"0123456789abcdef", "application/octet-stream"))],
    )
    assert r.status_code == 413
    assert r.json()["detail"] == "upload_too_large"


# --- GET /uploads ---------------------------------------------------------


def test_list_empty_returns_empty_list(client):
    c, _ = client
    tid = _new_thread(c)
    r = c.get(f"/api/threads/{tid}/uploads")
    assert r.status_code == 200
    assert r.json() == []


def test_list_returns_newest_first(client, tmp_path):
    c, tmp = client
    tid = _new_thread(c)

    # Seed two rows via the Db directly so we can control created_at order
    # without relying on the 1-second SQLite precision.
    db = Db(get_engine(tmp))
    from sqlalchemy import text as _text

    old_id = db.insert_upload(
        thread_id=tid, user_id=None, filename="older.txt",
        size=1, content_type="text/plain",
    )
    new_id = db.insert_upload(
        thread_id=tid, user_id=None, filename="newer.txt",
        size=1, content_type="text/plain",
    )
    # Rewrite older's timestamp a minute into the past.
    with db.engine.begin() as conn:
        conn.execute(
            _text(
                "UPDATE uploads SET created_at = datetime('now', '-1 minute') "
                "WHERE id = :id"
            ),
            {"id": old_id},
        )

    r = c.get(f"/api/threads/{tid}/uploads")
    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body] == [new_id, old_id]
    # Response shape
    for e in body:
        assert set(e.keys()) >= {"id", "filename", "size", "content_type", "created_at"}


def test_list_only_shows_own_thread(client):
    c, tmp = client
    t1 = _new_thread(c)
    t2 = _new_thread(c)
    c.post(
        f"/api/threads/{t1}/uploads",
        files=[("files", ("one.txt", b"1", "text/plain"))],
    )
    c.post(
        f"/api/threads/{t2}/uploads",
        files=[("files", ("two.txt", b"2", "text/plain"))],
    )
    r1 = c.get(f"/api/threads/{t1}/uploads").json()
    r2 = c.get(f"/api/threads/{t2}/uploads").json()
    assert len(r1) == 1 and r1[0]["filename"] == "one.txt"
    assert len(r2) == 1 and r2[0]["filename"] == "two.txt"


# --- DELETE /uploads/{id} -------------------------------------------------


def test_delete_removes_db_row_and_file(client):
    c, tmp = client
    tid = _new_thread(c)
    entry = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("bye.txt", b"bye", "text/plain"))],
    ).json()[0]
    on_disk = _uploads_dir(tmp, tid) / "bye.txt"
    assert on_disk.exists()

    r = c.delete(f"/api/threads/{tid}/uploads/{entry['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert not on_disk.exists()

    db = Db(get_engine(tmp))
    assert db.get_upload(entry["id"]) is None


def test_delete_missing_upload_404(client):
    c, _ = client
    tid = _new_thread(c)
    r = c.delete(f"/api/threads/{tid}/uploads/up_nope")
    assert r.status_code == 404


def test_delete_file_missing_but_row_present_still_removes_row_and_logs(client, caplog):
    c, tmp = client
    tid = _new_thread(c)
    entry = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("ghost.txt", b"boo", "text/plain"))],
    ).json()[0]
    on_disk = _uploads_dir(tmp, tid) / "ghost.txt"
    on_disk.unlink()  # Someone nuked the file out-of-band
    assert not on_disk.exists()

    import logging
    with caplog.at_level(logging.WARNING, logger="app.gateway.routers.uploads"):
        r = c.delete(f"/api/threads/{tid}/uploads/{entry['id']}")
    assert r.status_code == 200
    db = Db(get_engine(tmp))
    assert db.get_upload(entry["id"]) is None
    # We logged something about the missing file
    assert any("ghost.txt" in rec.message or "filesystem" in rec.message.lower()
               for rec in caplog.records), caplog.text


def test_delete_cross_thread_404(client):
    """DELETE must reject an upload_id that exists but belongs to another thread."""
    c, tmp = client
    t1 = _new_thread(c)
    t2 = _new_thread(c)
    entry = c.post(
        f"/api/threads/{t1}/uploads",
        files=[("files", ("t1.txt", b"x", "text/plain"))],
    ).json()[0]
    r = c.delete(f"/api/threads/{t2}/uploads/{entry['id']}")
    assert r.status_code == 404
    # row still present
    db = Db(get_engine(tmp))
    assert db.get_upload(entry["id"]) is not None
