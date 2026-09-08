"""Reading/writing .md files — the only source of truth (docs/design/schema.md).

Nothing here depends on an index or database: every query scans the files.
Slow at thousands of files, but correct by construction and matches the
schema's rule ("nothing may exist only in the database") — an index
(SQLite or not) is a future optimization layered on top of this, not a
dependency.

Every function is scoped to a directory (a project's or a standalone
knowledge base's own folder), never the whole workspace — ids and lookups
never cross that boundary.
"""

import datetime
from pathlib import Path

import frontmatter


def all_files(scope_dir: Path) -> list[Path]:
    if not scope_dir.exists():
        return []
    return sorted(scope_dir.rglob("*.md"))


def load(path: Path) -> frontmatter.Post:
    return frontmatter.loads(path.read_text(encoding="utf-8"))


def find_path_by_id(scope_dir: Path, item_id: str) -> Path | None:
    for path in all_files(scope_dir):
        post = load(path)
        if post.metadata.get("id") == item_id:
            return path
    return None


def title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.lstrip("#").strip() or fallback
    return fallback


def excerpt_from_body(body: str, max_len: int = 160) -> str:
    """A rough plain-text preview for the notes grid — skips the title line,
    then takes the next non-empty lines, stripping the most common markdown
    markup. Not a full renderer (no math/tables/etc.) — "more or less" is
    the point, not fidelity."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    rest = lines[1:] if lines else []
    text = " ".join(rest)
    for marker in ("###", "##", "#", "**", "__", "*", "_", "`"):
        text = text.replace(marker, "")
    text = text.strip()
    return text[:max_len] + ("…" if len(text) > max_len else "")


def next_id(scope_dir: Path, item_type: str) -> str:
    year = datetime.date.today().year
    prefix = f"{item_type}-{year}-"
    seqs = []
    for path in all_files(scope_dir):
        if path.stem.startswith(prefix):
            try:
                seqs.append(int(path.stem[len(prefix):]))
            except ValueError:
                pass
    return f"{prefix}{max(seqs, default=0) + 1:03d}"
