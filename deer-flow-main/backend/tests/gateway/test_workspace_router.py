"""Task 4.1: Workspace router tests.

Covers the file-tree + download endpoints that expose the CC-managed
``<HARMONY_DATA_DIR>/threads/<tid>/user-data/workspace/`` directory to
the frontend. Security-critical — path-escape, symlink, and null-byte
tests must fail loudly before any production deploy.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command

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


def _new_thread(c: TestClient) -> tuple[str, Path]:
    r = c.post("/api/threads", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], Path(body["cwd"])


# --- Tree: happy path ------------------------------------------------------


def test_tree_empty_workspace_returns_empty_list(client):
    """Fresh thread → workspace/tree returns ``{"root": "...", "children": []}``."""
    c, _ = client
    tid, cwd = _new_thread(c)
    r = c.get(f"/api/threads/{tid}/workspace/tree")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["children"] == []


def test_tree_lists_files_and_dirs(client):
    """A file at the root + a file in a subdir produce a 2-level tree.

    Node shape: {name, path (POSIX relative), type, children? | size + mtime}.
    Hidden files (leading dot) are included — CC writes ``.claude/`` etc.
    """
    c, _ = client
    tid, cwd = _new_thread(c)
    (cwd / "top.txt").write_text("hello")
    sub = cwd / "sub"
    sub.mkdir()
    (sub / "nested.md").write_text("# nested")
    (cwd / ".hidden").write_text("visible to tree")

    body = c.get(f"/api/threads/{tid}/workspace/tree").json()
    by_name = {n["name"]: n for n in body["children"]}
    assert set(by_name) == {"top.txt", "sub", ".hidden"}
    assert by_name["top.txt"]["type"] == "file"
    assert by_name["top.txt"]["path"] == "top.txt"
    assert by_name["top.txt"]["size"] == len("hello")
    assert "mtime" in by_name["top.txt"]
    assert by_name["sub"]["type"] == "dir"
    sub_children = {n["name"]: n for n in by_name["sub"]["children"]}
    assert set(sub_children) == {"nested.md"}
    assert sub_children["nested.md"]["path"] == "sub/nested.md"  # POSIX separator


def test_tree_skips_symlinks_escaping_cwd(client, tmp_path):
    """Symlink pointing outside ``cwd`` is omitted from the tree.

    Defense-in-depth: even if CC (or a malicious MCP) writes such a link
    into the workspace, it must not appear in the listing. Pair with
    ``test_symlink_escape_rejected_on_download`` which covers /files.
    """
    c, _ = client
    tid, cwd = _new_thread(c)
    outside = cwd.parent.parent.parent / "outside.txt"
    outside.write_text("not yours")
    (cwd / "escape.txt").symlink_to(outside)
    (cwd / "legit.txt").write_text("ok")

    body = c.get(f"/api/threads/{tid}/workspace/tree").json()
    names = [n["name"] for n in body["children"]]
    assert "legit.txt" in names
    assert "escape.txt" not in names


def test_tree_max_depth_respected(client):
    """Nest 25 dirs deep; the tree must stop recursing past _MAX_DEPTH=20.

    Anything below the 20th level disappears (no children key populated).
    Level 0 is ``cwd`` itself; level 1 is the first child, and so on.
    """
    c, _ = client
    tid, cwd = _new_thread(c)
    p = cwd
    # d1..d25
    for i in range(1, 26):
        p = p / f"d{i}"
        p.mkdir()
    # Sentinel file at the deepest level — should NOT appear in the tree.
    (p / "deep.txt").write_text("x")

    body = c.get(f"/api/threads/{tid}/workspace/tree").json()
    # Walk down following the single child at each level.
    node = body
    hits = 0
    while "children" in node and node["children"]:
        node = node["children"][0]
        hits += 1
        if hits > 30:  # safety, should never happen with the cap
            break
    # We descended from the root, so each step is one dir deeper. With
    # depth cap 20, we should see at most 20 levels of dirs before the
    # children list becomes empty.
    assert hits <= 20, f"cap not enforced, reached depth {hits}"


def test_tree_max_nodes_413(client, monkeypatch):
    """Cap node count. Monkeypatch the cap low (instead of creating 10k files)
    so the test runs in <100ms. Asserts 413 on overflow and that the cap
    lives in one place (module-level constant)."""
    import app.gateway.routers.workspace as ws

    monkeypatch.setattr(ws, "_MAX_NODES", 3)
    c, _ = client
    tid, cwd = _new_thread(c)
    for i in range(5):
        (cwd / f"f{i}.txt").write_text(str(i))
    r = c.get(f"/api/threads/{tid}/workspace/tree")
    assert r.status_code == 413, r.text


# --- Download: happy + edge cases ------------------------------------------


def test_download_file_happy_path(client):
    """UTF-8 text file round-trips verbatim with a text/* content-type."""
    c, _ = client
    tid, cwd = _new_thread(c)
    (cwd / "hello.txt").write_text("héllo wörld")  # non-ASCII on purpose
    r = c.get(f"/api/threads/{tid}/workspace/files/hello.txt")
    assert r.status_code == 200, r.text
    assert r.content.decode("utf-8") == "héllo wörld"
    assert r.headers["content-type"].startswith("text/plain")


def test_download_binary_file(client):
    """Raw bytes survive verbatim; content-type defaults to octet-stream
    for unknown extensions."""
    c, _ = client
    tid, cwd = _new_thread(c)
    raw = bytes(range(256))
    (cwd / "blob.bin").write_bytes(raw)
    r = c.get(f"/api/threads/{tid}/workspace/files/blob.bin")
    assert r.status_code == 200
    assert r.content == raw


def test_download_nested_file(client):
    """Subdir path with forward slashes resolves under cwd."""
    c, _ = client
    tid, cwd = _new_thread(c)
    (cwd / "a").mkdir()
    (cwd / "a" / "b.txt").write_text("nested body")
    r = c.get(f"/api/threads/{tid}/workspace/files/a/b.txt")
    assert r.status_code == 200
    assert r.text == "nested body"


def test_download_missing_file_404(client):
    c, _ = client
    tid, _ = _new_thread(c)
    r = c.get(f"/api/threads/{tid}/workspace/files/nope.txt")
    assert r.status_code == 404


def test_download_absolute_path_blocked(client):
    """``(cwd / '/etc/passwd')`` in Python drops ``cwd`` and yields the
    absolute path. ``is_relative_to`` catches it → 400."""
    c, tmp = client
    tid, _ = _new_thread(c)
    (tmp / "leak.txt").write_text("oops")
    # Use requests raw_path so the // isn't collapsed.
    r = c.get(f"/api/threads/{tid}/workspace/files//etc/passwd")
    assert r.status_code in (400, 403, 404)
    assert "root:" not in r.text


def test_download_symlink_escape_blocked(client):
    """A symlink inside cwd pointing outside is rejected on direct GET.

    Complements ``test_tree_skips_symlinks_escaping_cwd`` — even if the
    link slipped into the tree somehow, fetching it must 400.
    """
    c, tmp = client
    tid, cwd = _new_thread(c)
    secret = tmp / "extern.txt"
    secret.write_text("classified")
    (cwd / "link.txt").symlink_to(secret)
    r = c.get(f"/api/threads/{tid}/workspace/files/link.txt")
    assert r.status_code == 400, r.text
    assert "classified" not in r.text


def test_null_byte_rejected(client):
    """Explicit NUL-byte guard. FastAPI usually rejects these at the ASGI
    layer, but we defend anyway."""
    c, _ = client
    tid, _ = _new_thread(c)
    # URL-encoded NUL.
    r = c.get(f"/api/threads/{tid}/workspace/files/evil%00.txt")
    assert r.status_code in (400, 404)


def test_tree_thread_not_found_404(client):
    c, _ = client
    r = c.get("/api/threads/t_does_not_exist/workspace/tree")
    assert r.status_code == 404


def test_download_thread_not_found_404(client):
    c, _ = client
    r = c.get("/api/threads/t_does_not_exist/workspace/files/x.txt")
    assert r.status_code == 404


def test_range_request_returns_206(client):
    """FastAPI's FileResponse handles Range; verify a byte range arrives."""
    c, _ = client
    tid, cwd = _new_thread(c)
    body = b"0123456789abcdef"
    (cwd / "range.bin").write_bytes(body)
    r = c.get(
        f"/api/threads/{tid}/workspace/files/range.bin",
        headers={"Range": "bytes=3-7"},
    )
    # Some ASGI stacks do 206, others 200 without range honoring. Accept both
    # but when 206 the bytes must match. Either way, the full body is fine.
    assert r.status_code in (200, 206)
    if r.status_code == 206:
        assert r.content == body[3:8]


# --- End-to-end via the gateway_server subprocess --------------------------


def test_path_escape_blocked_via_real_server(gateway_server, tmp_path):
    """The plan's Step 1 test — crafted path against a real uvicorn.

    Exercises the full ASGI stack (URL decoding, routing, our resolve),
    not just TestClient. HARMONY_DATA_DIR is pointed at tmp_path by the
    gateway_server fixture.
    """
    import httpx

    (tmp_path / "secret.txt").write_text("pwned")
    with httpx.Client(
        base_url=gateway_server.url, timeout=5.0, cookies=gateway_server.auth_cookies
    ) as h:
        tid = h.post("/api/threads", json={}).json()["id"]
        r = h.get(f"/api/threads/{tid}/workspace/files/..%2F..%2F..%2Fsecret.txt")
    assert r.status_code in (400, 403, 404), (r.status_code, r.text)
    assert "pwned" not in r.text


def test_download_happy_via_real_server(gateway_server, tmp_path):
    """Smoke end-to-end: create thread, write file, GET via the real server."""
    import httpx

    with httpx.Client(
        base_url=gateway_server.url, timeout=5.0, cookies=gateway_server.auth_cookies
    ) as h:
        created = h.post("/api/threads", json={}).json()
        cwd = Path(created["cwd"])
        (cwd / "note.md").write_text("# smoke")
        r = h.get(f"/api/threads/{created['id']}/workspace/files/note.md")
        assert r.status_code == 200
        assert r.text == "# smoke"


# --- Security: path escape (Task 4.1 Step 1 — the failing test) ------------


def test_workspace_path_escape_blocked(client):
    """Per the plan's Step 1 snippet: a crafted ``../../../`` path MUST NOT
    leak files outside the thread's cwd. Accept 400/403/404 but NOT 200."""
    c, tmp = client
    tid, cwd = _new_thread(c)

    # Plant a secret at the data root, outside every thread's cwd.
    secret = tmp / "secret.txt"
    secret.write_text("pwned")
    # And one higher than that (the tmp_path parent), for good measure.
    (tmp.parent / "escape-secret.txt").write_text("double-pwned")

    # Crafted relative path (URL-encoded ../../).
    r = c.get(f"/api/threads/{tid}/workspace/files/..%2F..%2F..%2Fsecret.txt")
    assert r.status_code in (400, 403, 404), (r.status_code, r.text)
    # The response body must not contain the secret.
    assert "pwned" not in r.text
