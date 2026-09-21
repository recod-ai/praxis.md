"""Git-backed version history for the workspace (docs/design/schema.md#versioning).

Every scope (a project, or the shared workspace that holds every note base)
is its own real git repository, rooted at whatever directory its content
currently lives in. Every save commits it, authored as the acting user.
Merging concurrent edits to the same note's body is delegated to git itself
— `git merge-file`, the same line-based three-way merge git uses internally
for a real merge commit — rather than reimplemented here. A clean merge is
committed like any other save; a real conflict is left unresolved and
handed back as-is (with `<<<<<<<`/`=======`/`>>>>>>>` markers) for the
frontend to show, not silently picked one way or the other.

An encrypted project's repo lives inside its sealed vault (see vault.py) —
that's the one it gets rooted at while it's unlocked. Note bases (and any
project that's never been encrypted) still share the one repo rooted at
config.WORKSPACE_DIR, same as this app has always worked.

LOCK serializes every read-modify-write-commit sequence across the whole
app — one lock for every repo, not one per repo, since this app's scale
doesn't need the extra concurrency and a single lock can't deadlock across
repos. commit_all() stages the *entire* root (`git add -A`) rather than a
specific path, on the assumption that a caller always holds LOCK for the
duration of its own read-modify-write-commit sequence — otherwise a second
in-flight write could be swept into the wrong commit.
"""

import shutil
import threading
from pathlib import Path

import git

from . import config

LOCK = threading.Lock()

_repos: dict[str, git.Repo] = {}


def repo(root: Path) -> git.Repo:
    key = str(root.resolve())
    r = _repos.get(key)
    if r is None:
        root.mkdir(parents=True, exist_ok=True)
        try:
            r = git.Repo(root)
        except git.InvalidGitRepositoryError:
            r = git.Repo.init(root)
        _repos[key] = r
    return r


def forget_repo(root: Path) -> None:
    """Drop a repo from the cache once its directory stops existing (e.g. a
    project's scratch copy was wiped on reseal) — GitPython holds file
    handles into it, and a fresh unlock later gets a brand new scratch dir
    anyway, so there's nothing to reuse."""
    _repos.pop(str(root.resolve()), None)


def _author(username: str) -> git.Actor:
    full_name = config.get_user_profile(username).get("full_name") or username
    return git.Actor(full_name, f"{username}@praxis.local")


def ensure_baseline_commit(root: Path) -> None:
    """If this root has files but no commits yet — the ordinary state the
    very first time git versioning is turned on for it — give every file a
    real baseline commit. Without this, a file's first-ever concurrent edit
    couldn't be merge-checked at all: current_blob_sha would have nothing to
    compare against (no HEAD), so two people's first save of the same
    never-committed file would just silently overwrite one another instead
    of conflicting."""
    r = repo(root)
    if not r.head.is_valid():
        commit_all(root, "Initial import", "system")


def init_standalone_repo(root: Path, exclude: tuple[str, ...] = ()) -> None:
    """Give a project its own fresh git history, right before it's sealed
    into a vault for the first time (see vault.encrypt_project). `exclude`
    keeps this repo's own commits from ever picking up the things that live
    alongside it but aren't part of what gets sealed — project.yml and the
    vault.enc file itself — so only tasks/ and knowledge/ become its
    initial commit."""
    r = repo(root)
    if exclude:
        exclude_path = Path(r.git_dir) / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        with exclude_path.open("a", encoding="utf-8") as f:
            for pattern in exclude:
                f.write(f"{pattern}\n")
    ensure_baseline_commit(root)


def commit_all(root: Path, message: str, username: str) -> str | None:
    """Stage every change under `root` and commit as `username`. Returns the
    new commit's hexsha, or None if there was nothing to commit (e.g. a save
    that wrote back the exact bytes already on disk)."""
    r = repo(root)
    r.git.add(A=True)
    if not r.is_dirty(index=True, working_tree=False, untracked_files=False):
        return None
    actor = _author(username)
    commit = r.index.commit(message, author=actor, committer=actor)
    return commit.hexsha


def current_blob_sha(root: Path, relpath: str) -> str | None:
    """A stable version token for a tracked file's current committed
    content — the client echoes this back on save (as base_version) so we
    can tell whether the file changed underneath it since it was loaded.
    None if there's no commit yet, or the path isn't tracked at HEAD."""
    r = repo(root)
    if not r.head.is_valid():
        return None
    try:
        return r.git.rev_parse(f"HEAD:{Path(relpath).as_posix()}")
    except git.GitCommandError:
        return None


def blob_content(root: Path, blob_sha: str) -> str:
    """The raw text of a blob, by its content-addressed sha — works for any
    blob still in that repo's object store, independent of which commit or
    path it came from."""
    return repo(root).git.cat_file("-p", blob_sha)


def blob_at_commit(root: Path, commit_sha: str, relpath: str) -> str:
    """The file's text content as of a specific commit — used to restore an
    older version (see main.py's _restore_item)."""
    return repo(root).git.show(f"{commit_sha}:{Path(relpath).as_posix()}")


def file_history(root: Path, relpath: str) -> list[dict]:
    """Every commit that touched this path, newest first — the audit trail
    behind the frontend's History panel. `--follow` keeps this working
    across a rename (see _rename_item_file/_move_item)."""
    r = repo(root)
    if not r.head.is_valid():
        return []
    raw = r.git.log(
        "--follow", "--date=iso-strict", "--format=%H%x1f%an%x1f%ad%x1f%s",
        "--", Path(relpath).as_posix(),
    )
    history = []
    for line in raw.splitlines():
        if not line:
            continue
        sha, author, date, message = line.split("\x1f", 3)
        history.append({"sha": sha, "author": author, "date": date, "message": message})
    return history


def add_submodule(root: Path, relpath: str, url: str, clean_url: str | None = None) -> None:
    """Registers an existing repo as a real git submodule at relpath, right
    after the caller has git-rm'd whatever plain files used to live there
    (see main.py's _convert_folder_to_submodule) — plumbing
    (`git submodule add`), not GitPython's higher-level Submodule object
    model, which behaves inconsistently across versions (same preference
    for plain plumbing as this module's own merge_body). Leaves relpath
    checked out with the submodule's current content and stages
    .gitmodules + the new gitlink — caller still has to commit.

    `url` may carry credentials (the one-off admin clone URL); `clean_url`
    is what must remain recorded. `git submodule add` writes `url` into
    .gitmodules, which is committed, and into .git/config and the
    submodule's own remote — so it's rewritten straight away, before anything
    is committed, and no password ever reaches the workspace's history."""
    r = repo(root)
    r.git.submodule("add", "--force", url, relpath)
    if clean_url:
        r.git.config("-f", ".gitmodules", f"submodule.{relpath}.url", clean_url)
        r.git.submodule("sync", "--", relpath)
        r.git.add(".gitmodules")


def remove_submodule(root: Path, relpath: str) -> None:
    """Un-registers a submodule — deinit, then git-rm the gitlink/.gitmodules
    entry, then remove whatever's left of its checkout on disk. Never calls
    anything that could reach the remote repo itself: there is no
    delete-repo code path here or anywhere else in this codebase, by
    construction — see docs/design/schema.md, "Pastas submódulo", for why
    that guarantee matters (praxis.md's own "unlink" action must never be
    able to destroy the actual git history, only the local reference to
    it). Caller still has to commit."""
    r = repo(root)
    r.git.submodule("deinit", "-f", "--", relpath)
    r.git.rm("-f", relpath)
    shutil.rmtree(Path(root) / relpath, ignore_errors=True)
    # `git rm` already strips this submodule's own section out of
    # .gitmodules (confirmed live) but leaves the file itself behind even
    # once empty — tidy that up so unlinking the last submodule in a scope
    # doesn't leave a stray zero-byte file staged as "modified" forever.
    gitmodules = Path(root) / ".gitmodules"
    if gitmodules.exists() and not gitmodules.read_text(encoding="utf-8").strip():
        r.git.rm("-f", ".gitmodules")


def merge_body(base_text: str, ours_text: str, theirs_text: str) -> tuple[str, bool]:
    """Three-way merge of a note's body text via `git merge-file`. Returns
    (merged_text, had_conflicts) — on conflict, merged_text still contains
    git's usual <<<<<<<X/=======/>>>>>>> markers, for the frontend to show
    and the user to resolve by hand. Plain plumbing, not tied to any one
    repo's object store, so any repo handle can invoke it."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        current = tmp_path / "current"
        base = tmp_path / "base"
        other = tmp_path / "other"
        current.write_text(ours_text, encoding="utf-8")
        base.write_text(base_text, encoding="utf-8")
        other.write_text(theirs_text, encoding="utf-8")
        status, out, _err = git.Git().merge_file(
            "-p", "-L", "current version", "-L", "base version", "-L", "your edit",
            str(current), str(base), str(other),
            with_exceptions=False, with_extended_output=True,
        )
        return out, status != 0
