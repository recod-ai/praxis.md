"""Private, persistent per-user interface state: the "Now" focus list and
status-change requests.

Unlike index.py — a cache rebuilt from the markdown files on every startup —
this database is the only copy of what it holds. That is a deliberate
exception to "nothing may exist only in the database" (docs/design/
schema.md): none of this is task state. A Now entry is just a pointer, and a request is a message *about* a task; the task's own
header (status included) is only ever written by its owner, through the
normal header path. Losing this file loses the Now lists and pending
requests, never a task.

Kept outside the workspace's git repository and outside the archeion
mirrors on purpose, so it stays private to the people involved.
"""

import datetime
import os
import sqlite3
import threading
from pathlib import Path

from . import config

DB_PATH = Path(os.environ.get("PRAXIS_STATE_DB") or config.WORKSPACE_DIR.parent / "state.db")

NOW_SOFT_LIMIT = 3
NOW_HARD_LIMIT = 20
NOTE_MAX = 200

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

_SCHEMA = [
    """
    CREATE TABLE now_items (
        username   TEXT NOT NULL,
        project    TEXT NOT NULL,
        task_id    TEXT NOT NULL,
        position   INTEGER NOT NULL,
        started_at TEXT NOT NULL,
        PRIMARY KEY (username, project, task_id)
    )
    """,
    """
    CREATE TABLE status_requests (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        project       TEXT NOT NULL,
        task_id       TEXT NOT NULL,
        requester     TEXT NOT NULL,
        from_status   TEXT NOT NULL,
        to_status     TEXT NOT NULL,
        note          TEXT NOT NULL DEFAULT '',
        state         TEXT NOT NULL DEFAULT 'pending',
        decision_note TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX one_pending_request ON status_requests (project, task_id, requester) WHERE state = 'pending'",
]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            for statement in _SCHEMA:
                conn.execute(statement)
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
        elif version < 2:
            conn.execute("ALTER TABLE now_items DROP COLUMN note")
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
        _conn = conn
    return _conn


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in _connection().execute(sql, params).fetchall()]


def _run(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        conn = _connection()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


# --- Now ---


def now_list(username: str) -> list[dict]:
    return _rows("SELECT * FROM now_items WHERE username = ? ORDER BY position, started_at", (username,))


def now_add(username: str, project: str, task_id: str) -> dict:
    """Idempotent: adding what's already there just returns the existing
    row (same start time)."""
    with _lock:
        existing = _rows(
            "SELECT * FROM now_items WHERE username = ? AND project = ? AND task_id = ?",
            (username, project, task_id),
        )
        if existing:
            return existing[0]
        count = _rows("SELECT COUNT(*) AS n FROM now_items WHERE username = ?", (username,))[0]["n"]
        if count >= NOW_HARD_LIMIT:
            raise ValueError(f"Now can hold at most {NOW_HARD_LIMIT} tasks — take something out first.")
        position = _rows("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM now_items WHERE username = ?", (username,))[0]["p"]
        _run(
            "INSERT INTO now_items (username, project, task_id, position, started_at) VALUES (?, ?, ?, ?, ?)",
            (username, project, task_id, position, _now_iso()),
        )
        return _rows(
            "SELECT * FROM now_items WHERE username = ? AND project = ? AND task_id = ?",
            (username, project, task_id),
        )[0]


def now_remove(username: str, project: str, task_id: str) -> None:
    _run("DELETE FROM now_items WHERE username = ? AND project = ? AND task_id = ?", (username, project, task_id))


def now_reorder(username: str, order: list[tuple[str, str]]) -> None:
    """Applies the given (project, task_id) order; entries not mentioned
    keep their relative order after the mentioned ones."""
    with _lock:
        current = [(r["project"], r["task_id"]) for r in now_list(username)]
        wanted = [key for key in order if key in current]
        rest = [key for key in current if key not in wanted]
        conn = _connection()
        for position, (project, task_id) in enumerate(wanted + rest):
            conn.execute(
                "UPDATE now_items SET position = ? WHERE username = ? AND project = ? AND task_id = ?",
                (position, username, project, task_id),
            )
        conn.commit()


# --- status-change requests ---


def request_create(project: str, task_id: str, requester: str, from_status: str, to_status: str, note: str) -> dict:
    """One live request per requester and task: a new one replaces both an
    older pending request and an old rejection."""
    with _lock:
        _run(
            "DELETE FROM status_requests WHERE project = ? AND task_id = ? AND requester = ?",
            (project, task_id, requester),
        )
        cur = _run(
            "INSERT INTO status_requests (project, task_id, requester, from_status, to_status, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project, task_id, requester, from_status, to_status, note.strip()[:NOTE_MAX], _now_iso()),
        )
        return request_get(cur.lastrowid)


def request_get(request_id: int) -> dict | None:
    rows = _rows("SELECT * FROM status_requests WHERE id = ?", (request_id,))
    return rows[0] if rows else None


def requests_pending() -> list[dict]:
    return _rows("SELECT * FROM status_requests WHERE state = 'pending' ORDER BY created_at")


def requests_by_requester(username: str) -> list[dict]:
    return _rows("SELECT * FROM status_requests WHERE requester = ? ORDER BY created_at DESC", (username,))


def request_reject(request_id: int, decision_note: str) -> None:
    _run(
        "UPDATE status_requests SET state = 'rejected', decision_note = ? WHERE id = ?",
        (decision_note.strip()[:NOTE_MAX], request_id),
    )


def request_delete(request_id: int) -> None:
    _run("DELETE FROM status_requests WHERE id = ?", (request_id,))


def requests_remap_statuses(project: str, mapping: dict[str, str]) -> None:
    """After a project's status list was edited: `mapping` is old name ->
    new name for every status that still exists (renamed or not). A request
    aimed at a status that's gone is dropped; one that only mentions a
    renamed status as its "from" just has that label updated. Done in one
    pass so a swap (a->b, b->a) can't collide with itself."""
    with _lock:
        for row in _rows("SELECT * FROM status_requests WHERE project = ?", (project,)):
            if row["to_status"] not in mapping:
                request_delete(row["id"])
                continue
            _run(
                "UPDATE status_requests SET to_status = ?, from_status = ? WHERE id = ?",
                (mapping[row["to_status"]], mapping.get(row["from_status"], row["from_status"]), row["id"]),
            )


def drop_project(project: str) -> None:
    """Forgets everything about a project — used when it becomes encrypted,
    so nothing about its tasks lingers outside the vault."""
    _run("DELETE FROM now_items WHERE project = ?", (project,))
    _run("DELETE FROM status_requests WHERE project = ?", (project,))
