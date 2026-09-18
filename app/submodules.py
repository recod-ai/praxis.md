"""Turns a knowledge folder into a real git submodule, backed by its own
writable repo on the same Forgejo (archeion) instance the read-only
project mirror already uses (see archeion_mirror.py) — but a SEPARATE org
(PRAXIS_SUBMODULES_ORG), since the trust model is different: mirror repos
are pushed only by a bot with narrow "developers" write access; here,
students push directly with their own Forgejo accounts, so per-repo
collaborator entries actually matter. Reuses the same admin credentials
archeion_mirror's own collaborator sync already needs
(PRAXIS_ARCHEION_ADMIN_*) for repo/branch-protection/collaborator
management — there's no bot account here at all, nobody ever pushes as a
bot to a submodule repo.

Unlike archeion_mirror.py's silent best-effort (a failed mirror sync is a
skippable nice-to-have), every function here raises SubmoduleError with a
clear message on failure — main.py's conversion flow has to stop cleanly
partway through rather than leave a folder half-converted, and "not
configured at all" is itself a clear error here, not a quiet no-op, since
turning a folder into a submodule is a deliberate action someone just took,
not a fire-and-forget background sync.
"""

import os

import httpx

from . import forgejo
from .archeion_mirror import ADMIN_PASSWORD, ADMIN_USERNAME, ARCHEION_URL

SUBMODULES_ORG = os.environ.get("PRAXIS_SUBMODULES_ORG", "")
# ARCHEION_URL is reused for server-side API calls and for the initial,
# admin-authenticated clone/push during conversion — but it's routed over
# the deployment's internal Docker network (e.g. http://forgejo:3000),
# unreachable from a student's own machine. PUBLIC_URL is the externally
# reachable address of that same Forgejo, used only for the clone URL
# actually shown to a student (public_clone_url) — never for an API call.
PUBLIC_URL = os.environ.get("PRAXIS_SUBMODULES_PUBLIC_URL", "").rstrip("/")


class SubmoduleError(Exception):
    pass


def _configured() -> bool:
    return bool(ARCHEION_URL and SUBMODULES_ORG and ADMIN_USERNAME and ADMIN_PASSWORD and PUBLIC_URL)


def _require_configured() -> None:
    if not _configured():
        raise SubmoduleError(
            "Submodule folders need PRAXIS_ARCHEION_URL, PRAXIS_SUBMODULES_ORG, "
            "PRAXIS_SUBMODULES_PUBLIC_URL, and PRAXIS_ARCHEION_ADMIN_USERNAME/PASSWORD "
            "configured on the server — ask the admin."
        )


def _admin_api(method: str, path: str, **kw) -> httpx.Response:
    return forgejo.api(ARCHEION_URL, method, path, (ADMIN_USERNAME, ADMIN_PASSWORD), **kw)


def create_submodule_repo(repo_name: str) -> str:
    """Creates the repo (private, under SUBMODULES_ORG, idempotent — a
    retry after a partial failure won't create a duplicate) and locks its
    default branch against force-push/deletion before returning — see this
    module's docstring for why that has to happen up front, not as an
    afterthought. Returns an admin-authenticated clone URL, used exactly
    once by main.py's conversion flow for the initial server-side `git
    submodule add`/content push — students clone/push afterward with their
    own Forgejo accounts (see sync_collaborators), never this URL."""
    _require_configured()
    try:
        res = _admin_api("GET", f"/repos/{SUBMODULES_ORG}/{repo_name}")
        if res.status_code == 200:
            branch = res.json().get("default_branch") or "main"
        else:
            res = _admin_api(
                "POST",
                f"/orgs/{SUBMODULES_ORG}/repos",
                json={
                    "name": repo_name,
                    "description": "praxis.md — submodule folder (a real, writable git repo)",
                    "private": True,
                    "auto_init": True,
                },
            )
            res.raise_for_status()
            branch = res.json().get("default_branch") or "main"

        # No force-push, no branch deletion — the one hard requirement for
        # a submodule folder (docs/design/schema.md): praxis.md's own
        # "delete" action only ever unlinks the reference (see
        # git_store.remove_submodule), never the repo itself, but that
        # guarantee is worthless if a student (or anyone with push access)
        # could rewrite/delete history themselves. Confirmed live against
        # the deployed Forgejo (v9, codeberg.org/forgejo/forgejo:9): its
        # branch-protection API has no separate "allow force push" field at
        # all (checked the live swagger schema) — simply creating a
        # protection rule for a branch is enough on its own to make it
        # reject both a force-push ("branch main is protected from force
        # push") and a delete of the repo's default branch. `enable_push`
        # here only controls whether a *plain* (fast-forward) push is
        # allowed at all — turning it off would make this a read-only
        # mirror instead of a repo students can actually work in.
        _admin_api(
            "POST",
            f"/repos/{SUBMODULES_ORG}/{repo_name}/branch_protections",
            json={"branch_name": branch, "enable_push": True},
        )
    except httpx.HTTPError as err:
        raise SubmoduleError(f"Could not create/protect the submodule repo on Forgejo: {err}") from err
    return forgejo.authenticated_clone_url(ARCHEION_URL, SUBMODULES_ORG, repo_name, ADMIN_USERNAME, ADMIN_PASSWORD)


def public_clone_url(repo_name: str) -> str:
    """The URL shown to a student — externally reachable (PUBLIC_URL, not
    ARCHEION_URL) and with no embedded credentials, they authenticate as
    themselves (unlike create_submodule_repo's admin URL, which is only
    ever used once, server-side, over the internal ARCHEION_URL)."""
    return f"{PUBLIC_URL}/{SUBMODULES_ORG}/{repo_name}.git"


def sync_collaborators(repo_name: str, member_roles: dict[str, str], get_email) -> None:
    """Read for every member regardless of role (read is open to everyone
    in praxis.md's own model, see docs/design/schema.md) — write for
    editor/admin only. No per-person write list here (unlike a normal
    folder's `users:`, see permissions.check_folder_write) — this
    implements "anyone with editor access can push, on purpose, to keep
    the student's own git workflow simple." Best-effort/silent, same as
    archeion_mirror.sync_collaborators: a stale collaborator list for a
    moment isn't worth blocking a member-list save over. `get_email` is
    injected the same way archeion_mirror's own sync_collaborators does
    it — this module stays a thin Forgejo client, no opinion on where an
    email comes from."""
    if not _configured():
        return
    try:
        writers, readers = set(), set()
        for username, role in member_roles.items():
            email = get_email(username)
            if not email:
                continue
            login = forgejo.login_for_email(email)
            (writers if role in ("admin", "editor") else readers).add(login)

        res = _admin_api("GET", f"/repos/{SUBMODULES_ORG}/{repo_name}/collaborators")
        res.raise_for_status()
        current = {c["login"] for c in res.json()}

        for login in writers:
            _admin_api(
                "PUT", f"/repos/{SUBMODULES_ORG}/{repo_name}/collaborators/{login}", json={"permission": "write"}
            ).raise_for_status()
        for login in readers - writers:
            _admin_api(
                "PUT", f"/repos/{SUBMODULES_ORG}/{repo_name}/collaborators/{login}", json={"permission": "read"}
            ).raise_for_status()
        for login in current - writers - readers:
            _admin_api("DELETE", f"/repos/{SUBMODULES_ORG}/{repo_name}/collaborators/{login}").raise_for_status()
    except httpx.HTTPError as err:
        print(f"submodules: collaborator sync failed for {repo_name}: {err}")
