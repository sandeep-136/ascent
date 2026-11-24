"""Private-by-default record store (SQLite).

Documents and analysis results are stored under unguessable ids. There is no
endpoint that lists other visitors' records: history lives in the visitor's
own browser (localStorage). A record is reachable only by its id, and a
record is shared only when its owner asks for a share link.
"""

import json
import sqlite3
import threading
import time
import uuid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,          -- 'document' | 'analysis'
    created_at REAL NOT NULL,
    payload TEXT NOT NULL        -- JSON
);
"""


class Store:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def put(self, kind: str, payload: dict) -> str:
        rid = uuid.uuid4().hex[:20]
        with self._lock:
            self._conn.execute(
                "INSERT INTO records (id, kind, created_at, payload) VALUES (?, ?, ?, ?)",
                (rid, kind, time.time(), json.dumps(payload)),
            )
            self._conn.commit()
        return rid

    def get(self, rid: str, kind: str | None = None) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT kind, payload FROM records WHERE id = ?", (rid,)
            ).fetchone()
        if row is None or (kind is not None and row[0] != kind):
            return None
        return json.loads(row[1])
