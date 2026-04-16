import sqlite3

from app.cc_adapter.session_store import SessionStore


def test_create_and_get_mapping(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo", user_id="u1")
    row = store.get("t_abc")
    assert row is not None
    assert row.thread_id == "t_abc"
    assert row.cwd == "/tmp/foo"
    assert row.session_id is None
    assert row.user_id == "u1"


def test_set_session_id(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo", user_id="u1")
    store.set_session_id("t_abc", "01HW_SID")
    row = store.get("t_abc")
    assert row.session_id == "01HW_SID"


def test_delete_mapping(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo", user_id="u1")
    store.delete("t_abc")
    assert store.get("t_abc") is None


# --- Task 5.3: user_id column + ownership-aware queries --------------------


def test_create_persists_user_id(tmp_path):
    """``create(..., user_id=...)`` persists the owner, returned by ``get``."""
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_owned", cwd="/tmp/x", user_id="u_alpha")
    row = store.get("t_owned")
    assert row is not None
    assert row.user_id == "u_alpha"


def test_ensure_schema_idempotent_adds_user_id_column(tmp_path):
    """Open a pre-5.3 DB with the 3-column schema; ``ensure_schema`` must
    ALTER the table to add ``user_id`` without dropping existing rows."""
    db_path = tmp_path / "sessions.db"
    # Seed a legacy DB exactly as the pre-5.3 code would have.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE cc_thread_session (thread_id TEXT PRIMARY KEY, session_id TEXT, cwd TEXT NOT NULL)")
        conn.execute("INSERT INTO cc_thread_session(thread_id, session_id, cwd) VALUES ('t_legacy', NULL, '/tmp/legacy')")
        conn.commit()
    finally:
        conn.close()

    # Upgrade schema via SessionStore.
    store = SessionStore(db_path=str(db_path))
    store.ensure_schema()

    # Legacy row survives; its user_id is NULL.
    legacy = store.get("t_legacy")
    assert legacy is not None
    assert legacy.user_id is None
    assert legacy.cwd == "/tmp/legacy"

    # New rows carry their user_id.
    store.create("t_new", cwd="/tmp/new", user_id="u_beta")
    assert store.get("t_new").user_id == "u_beta"

    # Second call is a no-op (idempotent).
    store.ensure_schema()
    assert store.get("t_new").user_id == "u_beta"


def test_list_for_user_returns_only_owned(tmp_path):
    """``list_for_user`` scopes by ``user_id``; other owners are invisible,
    and NULL-owner (legacy) rows are not returned to anyone."""
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("tA", cwd="/tmp/a", user_id="u1")
    store.create("tB", cwd="/tmp/b", user_id="u2")
    store.create("tC", cwd="/tmp/c", user_id="u1")

    # Seed a NULL-owner legacy row to confirm it's excluded.
    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    try:
        conn.execute("INSERT INTO cc_thread_session(thread_id, session_id, cwd, user_id) VALUES ('tLegacy', NULL, '/tmp/legacy', NULL)")
        conn.commit()
    finally:
        conn.close()

    u1_rows = store.list_for_user("u1")
    ids = {r.thread_id for r in u1_rows}
    assert ids == {"tA", "tC"}
    u2_rows = store.list_for_user("u2")
    assert {r.thread_id for r in u2_rows} == {"tB"}
