"""Mirrors each project's current tasks/knowledge into its own repo on
archeion (the agorae deployment's Forgejo) — a second, independent place
people can watch their work versioned while praxis.md is still new enough
that trusting it alone feels like a leap. Deliberately NOT a synced clone of
praxis.md's own internal git history (see git_store.py): this pushes fresh
commits against a mirror repo's own history, so a project sharing
WORKSPACE_DIR's repo with a dozen others never leaks anything about them.

Read-only for everyone but the bot pushing it: project members are added as
repo collaborators with "read" permission only (never "write") — editing
only ever happens through praxis.md itself, archeion is just the record.
The bot account itself holds no more than a "developers" team's "write"
permission on the praxis.md org (set up once, out of band — see
docs/PRAXIS.md), not repo ownership/admin, so a leaked credential here
can't do anything more than push commits — it can't even manage
collaborators itself (confirmed live: Forgejo refuses that to anything
short of repo-admin), which is why that one piece uses a separate
higher-privileged credential (PRAXIS_ARCHEION_ADMIN_*, the same account
metroon's own Forgejo sync already uses) instead of the bot's.

Every function here is best-effort and never raises: a mirror sync failing
should never stop someone from actually saving their work in praxis.md, and
this also has to behave when archeion isn't configured at all (the env vars
below unset) or is temporarily unreachable.
"""

import os
import shutil
from pathlib import Path

import git
import httpx

from . import forgejo

ARCHEION_URL = os.environ.get("PRAXIS_ARCHEION_URL", "").rstrip("/")
ARCHEION_ORG = os.environ.get("PRAXIS_ARCHEION_ORG", "")
BOT_USERNAME = os.environ.get("PRAXIS_ARCHEION_BOT_USERNAME", "")
BOT_PASSWORD = os.environ.get("PRAXIS_ARCHEION_BOT_PASSWORD", "")
ADMIN_USERNAME = os.environ.get("PRAXIS_ARCHEION_ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("PRAXIS_ARCHEION_ADMIN_PASSWORD", "")
MIRRORS_DIR = Path(os.environ.get("PRAXIS_ARCHEION_MIRRORS_DIR", "/data/archeion-mirrors"))


def _configured() -> bool:
    return bool(ARCHEION_URL and ARCHEION_ORG and BOT_USERNAME and BOT_PASSWORD)


def _collaborators_configured() -> bool:
    return bool(ARCHEION_URL and ARCHEION_ORG and ADMIN_USERNAME and ADMIN_PASSWORD)


def _api(method: str, path: str, **kw) -> httpx.Response:
    return forgejo.api(ARCHEION_URL, method, path, (BOT_USERNAME, BOT_PASSWORD), **kw)


def _admin_api(method: str, path: str, **kw) -> httpx.Response:
    return forgejo.api(ARCHEION_URL, method, path, (ADMIN_USERNAME, ADMIN_PASSWORD), **kw)


def _authenticated_clone_url(slug: str) -> str:
    return forgejo.authenticated_clone_url(ARCHEION_URL, ARCHEION_ORG, slug, BOT_USERNAME, BOT_PASSWORD)


def _ensure_repo(slug: str, name: str) -> None:
    res = _api("GET", f"/repos/{ARCHEION_ORG}/{slug}")
    if res.status_code == 200:
        return
    res = _api(
        "POST",
        f"/orgs/{ARCHEION_ORG}/repos",
        json={
            "name": slug,
            "description": f"praxis.md — {name} (read-only mirror; edit in praxis.md itself)",
            "private": True,
            "auto_init": True,
        },
    )
    res.raise_for_status()


def _mirror_clone(slug: str) -> git.Repo:
    path = MIRRORS_DIR / slug
    if (path / ".git").exists():
        return git.Repo(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return git.Repo.clone_from(_authenticated_clone_url(slug), path)


def sync_project(slug: str, name: str, content_dir: Path, acting_user: str, message: str) -> None:
    """Copies tasks/ and knowledge/'s current state into this project's
    mirror repo and pushes one new commit, if anything actually changed."""
    if not _configured():
        return
    try:
        _ensure_repo(slug, name)
        repo = _mirror_clone(slug)
        dest_root = Path(repo.working_dir)
        for sub in ("tasks", "knowledge"):
            dest_sub = dest_root / sub
            if dest_sub.exists():
                shutil.rmtree(dest_sub)
            src_sub = content_dir / sub
            if src_sub.exists():
                shutil.copytree(src_sub, dest_sub)
        repo.git.add(A=True)
        if not repo.is_dirty(index=True, working_tree=False, untracked_files=False):
            return
        # index.commit(), not git.commit() — the latter shells out to the
        # real `git commit`, which needs a user.name/user.email in git
        # config; this constructs the commit object directly from the given
        # Actor, same trick git_store.py's own commit_all already relies on.
        actor = git.Actor(acting_user, f"{acting_user}@praxis.local")
        repo.index.commit(message, author=actor, committer=actor)
        repo.git.push("origin", "HEAD:main")
    except (git.GitCommandError, httpx.HTTPError, OSError) as err:
        print(f"archeion mirror: sync failed for project {slug}: {err}")


def sync_collaborators(slug: str, name: str, members: list[str], get_email) -> None:
    """Adds/removes read-only collaborators on a project's mirror repo to
    match its current members list — never write access, see this module's
    own docstring for why. `get_email(username) -> str | None` is injected
    rather than importing config directly, so this stays a thin Forgejo
    client with no opinion on where an email comes from."""
    if not (_configured() and _collaborators_configured()):
        return
    try:
        _ensure_repo(slug, name)  # a project can get its first member before its first save

        target = set()
        for username in members:
            email = get_email(username)
            if email:
                target.add(forgejo.login_for_email(email))

        res = _admin_api("GET", f"/repos/{ARCHEION_ORG}/{slug}/collaborators")
        res.raise_for_status()
        # excludes the bot itself — its push access comes from the
        # "developers" team (see this module's docstring), never from a
        # collaborator entry, so this should never add or remove it
        current = {c["login"] for c in res.json()} - {BOT_USERNAME}

        for login in target - current:
            _admin_api(
                "PUT", f"/repos/{ARCHEION_ORG}/{slug}/collaborators/{login}", json={"permission": "read"}
            ).raise_for_status()
        for login in current - target:
            _admin_api("DELETE", f"/repos/{ARCHEION_ORG}/{slug}/collaborators/{login}").raise_for_status()
    except httpx.HTTPError as err:
        print(f"archeion mirror: collaborator sync failed for project {slug}: {err}")
