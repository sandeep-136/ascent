import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import turso


class FakeResultSet:
    def __init__(self, columns, rows, last_insert_rowid=None):
        self.columns = columns
        self.rows = rows
        self.last_insert_rowid = last_insert_rowid


def test_cursor_rows_become_dicts():
    rs = FakeResultSet(["id", "name"], [(1, "Python"), (2, "SQL")], last_insert_rowid=7)
    cur = turso._Cursor(rs)
    assert cur.lastrowid == 7
    rows = cur.fetchall()
    assert rows == [{"id": 1, "name": "Python"}, {"id": 2, "name": "SQL"}]
    assert rows[0]["name"] == "Python"


def test_cursor_fetchone_empty():
    cur = turso._Cursor(FakeResultSet(["id"], []))
    assert cur.fetchone() is None
    cur = turso._Cursor(FakeResultSet(["id"], [(42,)]))
    assert cur.fetchone() == {"id": 42}


def test_configured_requires_both_env(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    assert not turso.configured()
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://x-y.turso.io")
    assert not turso.configured()
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    assert turso.configured()


def test_libsql_scheme_rewritten_to_https(monkeypatch):
    captured = {}

    class FakeClient:
        def execute(self, sql, params=()):
            return FakeResultSet([], [])
        def batch(self, stmts):
            captured["batch"] = len(stmts)
        def close(self):
            pass

    import libsql_client
    def fake_create(url, auth_token=None):
        captured["url"] = url
        return FakeClient()
    monkeypatch.setattr(libsql_client, "create_client_sync", fake_create)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://ascent-private-me.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "tok")
    conn = turso.connect()
    assert captured["url"] == "https://ascent-private-me.turso.io"
    conn.executescript("CREATE TABLE a (id INTEGER); CREATE TABLE b (id INTEGER);")
    assert captured["batch"] == 2


def test_db_connect_dispatches_on_env(monkeypatch, tmp_path):
    from app import db
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    import sqlite3
    assert isinstance(db.connect(str(tmp_path / "x.db")), sqlite3.Connection)
