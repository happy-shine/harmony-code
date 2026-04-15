from app.cc_adapter.session_store import SessionStore


def test_create_and_get_mapping(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo")
    row = store.get("t_abc")
    assert row is not None
    assert row.thread_id == "t_abc"
    assert row.cwd == "/tmp/foo"
    assert row.session_id is None


def test_set_session_id(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo")
    store.set_session_id("t_abc", "01HW_SID")
    row = store.get("t_abc")
    assert row.session_id == "01HW_SID"


def test_delete_mapping(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo")
    store.delete("t_abc")
    assert store.get("t_abc") is None
