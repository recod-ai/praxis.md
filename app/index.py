"""A SQLite index over the workspace's markdown files — purely a cache.

Every row here is derived straight from a .md file's frontmatter (see
storage.py); nothing may exist only in the database (docs/design/schema.md
— the same rule storage.py itself is built on). If the index file is
deleted, rebuild_all() reconstructs it exactly by re-scanning the files,
which is also what happens once automatically on every app startup — so a
stale or corrupt index is never a data-loss risk, only a rebuild.

Scoped by scope_dir exactly like storage.py: nothing here ever queries or
returns rows across a project/note-base boundary.
"""

import datetime
import json
import sqlite3
from pathlib import Path

from . import config, storage

DB_PATH = config.WORKSPACE_DIR / ".praxis-index.db"

_conn: sqlite3.Connection | None = None


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS items ("
            " scope_dir TEXT NOT NULL,"
            " id TEXT NOT NULL,"
            " path TEXT NOT NULL,"
            " data_json TEXT NOT NULL,"
            " PRIMARY KEY (scope_dir, id)"
            ")"
        )
        _conn.commit()
    return _conn


def _folder_of(scope_dir: Path, path: Path, meta: dict) -> str:
    notes_root = scope_dir / "knowledge"
    if meta.get("type") == "knowledge" and path.parent != notes_root:
        try:
            return str(path.parent.relative_to(notes_root))
        except ValueError:
            pass
    return ""


def _item_data(scope_dir: Path, path: Path) -> dict:
    post = storage.load(path)
    meta = dict(post.metadata)
    meta["path"] = str(path.relative_to(scope_dir))
    meta["title"] = storage.title_from_body(post.content, meta.get("id", path.stem))
    meta["excerpt"] = storage.excerpt_from_body(post.content)
    meta["folder"] = _folder_of(scope_dir, path, meta)
    return meta


def reindex_item(scope_dir: Path, path: Path) -> None:
    """Re-read one file from disk and upsert its row — called after every
    write so the index never drifts from the files it mirrors."""
    data = _item_data(scope_dir, path)
    conn = _connection()
    conn.execute(
        "INSERT INTO items (scope_dir, id, path, data_json) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(scope_dir, id) DO UPDATE SET path = excluded.path, data_json = excluded.data_json",
        (str(scope_dir), data["id"], data["path"], json.dumps(data, default=str)),
    )
    conn.commit()


def remove_item(scope_dir: Path, item_id: str) -> None:
    """Drops one item's row after its file has already been deleted from
    disk (see main.py's _delete_item) — the index is purely a cache, so
    this never needs to touch git/storage itself."""
    conn = _connection()
    conn.execute("DELETE FROM items WHERE scope_dir = ? AND id = ?", (str(scope_dir), item_id))
    conn.commit()


def rebuild_scope(scope_dir: Path) -> None:
    conn = _connection()
    conn.execute("DELETE FROM items WHERE scope_dir = ?", (str(scope_dir),))
    for path in storage.all_files(scope_dir):
        data = _item_data(scope_dir, path)
        conn.execute(
            "INSERT INTO items (scope_dir, id, path, data_json) VALUES (?, ?, ?, ?)",
            (str(scope_dir), data["id"], data["path"], json.dumps(data, default=str)),
        )
    conn.commit()


def rebuild_all() -> None:
    for slug in config.list_projects():
        rebuild_scope(config.project_dir(slug))
    for slug in config.list_knowledge_bases():
        rebuild_scope(config.kb_dir(slug))


def list_items(scope_dir: Path) -> list[dict]:
    rows = _connection().execute(
        "SELECT data_json FROM items WHERE scope_dir = ?", (str(scope_dir),)
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def find_path_by_id(scope_dir: Path, item_id: str) -> Path | None:
    row = _connection().execute(
        "SELECT path FROM items WHERE scope_dir = ? AND id = ?", (str(scope_dir), item_id)
    ).fetchone()
    return scope_dir / row[0] if row else None


def list_tags(scope_dir: Path) -> list[str]:
    tags: set[str] = set()
    for item in list_items(scope_dir):
        tags.update(item.get("tags") or [])
    return sorted(tags)


def next_id(scope_dir: Path, item_type: str) -> str:
    year = datetime.date.today().year
    prefix = f"{item_type}-{year}-"
    rows = _connection().execute(
        "SELECT id FROM items WHERE scope_dir = ? AND id LIKE ?", (str(scope_dir), f"{prefix}%")
    ).fetchall()
    seqs = []
    for (item_id,) in rows:
        try:
            seqs.append(int(item_id[len(prefix):]))
        except ValueError:
            pass
    return f"{prefix}{max(seqs, default=0) + 1:03d}"
