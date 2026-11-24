"""Turso (libSQL) storage backend.

Ascent's code speaks the sqlite3 subset: conn.execute / executescript /
commit / close, rows converted with dict(row), and cur.lastrowid for inserts.
This module wraps a Turso HTTP client in that exact surface so the rest of
the app does not know or care which engine is underneath. Selected by env:

    TURSO_DATABASE_URL   libsql://<db>-<org>.turso.io (or https://...)
    TURSO_AUTH_TOKEN     database auth token

Statements run one HTTP round-trip each; commits are no-ops because every
statement is its own request. That trades latency for durability - the
right trade for a single-owner app whose data must survive host restarts.
"""

import os

import libsql_client


class _Cursor:
    def __init__(self, result_set):
        self._rs = result_set
        self.lastrowid = result_set.last_insert_rowid

    def _rows_as_dicts(self):
        cols = list(self._rs.columns)
        return [dict(zip(cols, tuple(r))) for r in self._rs.rows]

    def fetchall(self):
        return self._rows_as_dicts()

    def fetchone(self):
        rows = self._rows_as_dicts()
        return rows[0] if rows else None


class TursoConnection:
    """sqlite3.Connection-shaped wrapper over libsql_client."""

    def __init__(self, url: str, token: str):
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self._client = libsql_client.create_client_sync(url, auth_token=token)

    def execute(self, sql: str, params=()):
        return _Cursor(self._client.execute(sql, list(params)))

    def executescript(self, script: str) -> None:
        statements = [s.strip() for s in script.split(";") if s.strip()]
        self._client.batch([libsql_client.Statement(s) for s in statements])

    def commit(self) -> None:  # each statement is already its own request
        return None

    def close(self) -> None:
        self._client.close()


def configured() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"))


def connect() -> TursoConnection:
    return TursoConnection(os.environ["TURSO_DATABASE_URL"], os.environ["TURSO_AUTH_TOKEN"])
