import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent

# .env holds real secrets (GOOGLE_CLIENT_SECRET, PRAXIS_SESSION_SECRET, ...)
# for a real deployment — it's gitignored, never committed; see .env.example
# for the full list of variables it can set. A missing .env (the common
# case for local dev) is a no-op, not an error. This has to run before
# anything below (or in any other module) reads os.environ, so config.py
# — imported first, ahead of everything else — is where it happens.
load_dotenv(APP_DIR / ".env")

WORKSPACE_DIR = Path(os.environ.get("PRAXIS_WORKSPACE_DIR", APP_DIR / "workspace")).resolve()
TEMPLATES_DIR = Path(os.environ.get("PRAXIS_TEMPLATES_DIR", APP_DIR / "templates")).resolve()

PROJECTS_DIR = WORKSPACE_DIR / "projects"
KNOWLEDGE_BASES_DIR = WORKSPACE_DIR / "knowledge-bases"
USERS_DIR = WORKSPACE_DIR / "users"

DEFAULT_STATUSES = ["proposal", "backlog", "ready", "in_progress", "review", "done"]

VALID_ROLES = ["admin", "editor", "guest"]
DEFAULT_ROLE = "editor"

DEFAULT_PROJECT_ICON = "folder"
DEFAULT_KB_ICON = "menu_book"


def project_dir(slug: str) -> Path:
    return PROJECTS_DIR / slug


def kb_dir(slug: str) -> Path:
    return KNOWLEDGE_BASES_DIR / slug


def read_project_config(slug: str) -> dict:
    path = project_dir(slug) / "project.yml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def get_statuses(slug: str) -> list[str]:
    return list(read_project_config(slug).get("statuses") or DEFAULT_STATUSES)


def get_member_roles(slug: str) -> dict[str, str]:
    """`members:` is a mapping of username -> role (admin | editor | guest).
    Empty/missing means "open to anyone" — see get_members/get_role for what
    that implies for access and role checks."""
    return dict(read_project_config(slug).get("members") or {})


def get_members(slug: str) -> list[str]:
    """Empty list means "open to anyone" — see docs/design/schema.md#permissions
    for why this isn't real access control yet (no authentication behind the
    acting username)."""
    return list(get_member_roles(slug).keys())


def get_role(slug: str, username: str) -> str:
    """A member's role. If the project has no members configured at all, it's
    open to anyone — everyone acts as `admin` in that case (same
    permissive-when-unset pattern as membership itself: an open project can't
    be locked out of getting its first real admin, and `admin` already
    implies full edit rights, so this also preserves the old "open project,
    anyone can edit" behavior)."""
    roles = get_member_roles(slug)
    if not roles:
        return "admin"
    return roles.get(username, DEFAULT_ROLE)


def is_admin(slug: str, username: str) -> bool:
    return get_role(slug, username) == "admin"


def can_edit(slug: str, username: str) -> bool:
    return get_role(slug, username) in ("admin", "editor")


def get_project_name(slug: str) -> str:
    """The editable display name — falls back to the slug (the directory
    name) until someone sets one explicitly, same as a note base's."""
    return read_project_config(slug).get("name") or slug


def get_project_icon(slug: str) -> str:
    return read_project_config(slug).get("icon") or DEFAULT_PROJECT_ICON


def set_project_name(slug: str, name: str) -> str:
    cfg = read_project_config(slug)
    cfg["name"] = name
    _write_project_config(slug, cfg)
    return name


def set_project_icon(slug: str, icon: str) -> str:
    cfg = read_project_config(slug)
    cfg["icon"] = icon
    _write_project_config(slug, cfg)
    return icon


def set_member_role(slug: str, username: str, role: str) -> dict[str, str]:
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r} (must be one of {VALID_ROLES})")
    cfg = read_project_config(slug)
    roles = dict(cfg.get("members") or {})
    roles[username] = role
    cfg["members"] = roles
    _write_project_config(slug, cfg)
    return roles


def remove_member(slug: str, username: str) -> dict[str, str]:
    cfg = read_project_config(slug)
    roles = {u: r for u, r in (cfg.get("members") or {}).items() if u != username}
    cfg["members"] = roles
    _write_project_config(slug, cfg)
    return roles


def _write_project_config(slug: str, cfg: dict) -> None:
    path = project_dir(slug) / "project.yml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def get_project_vault_salt(slug: str) -> str | None:
    """The scrypt salt for an encrypted project's secret (hex-encoded) — not
    itself sensitive (a salt's whole purpose is to be public), so it lives
    in the same plaintext project.yml as the name/icon/members. None means
    the project has never been encrypted."""
    return read_project_config(slug).get("vault_salt")


def set_project_vault_salt(slug: str, salt_hex: str) -> None:
    cfg = read_project_config(slug)
    cfg["vault_salt"] = salt_hex
    _write_project_config(slug, cfg)


def resolve_scope(scope_dir: Path) -> tuple[Path, Path]:
    """(content_dir, git_root) for a scope directory.

    content_dir is where its actual files (tasks/knowledge) live right now.
    For a note base, or a project that was never encrypted, that's just
    `scope_dir` itself — this only diverges once a project has been sealed
    (see vault.py): from then on its real content only exists in a live,
    decrypted scratch copy while unlocked, so this looks that up instead,
    raising vault.ProjectLocked if nobody's unlocked it yet (main.py turns
    that into a 423 the frontend reads as "ask for this project's
    password").

    git_root is which repo tracks that content: WORKSPACE_DIR for a note
    base or an unencrypted project, exactly as this app has always worked —
    but an unlocked encrypted project is its own independent repo, rooted
    at that same live scratch copy (its .git came along in the same
    archive), not a subdirectory of the shared workspace repo."""
    if scope_dir.parent != PROJECTS_DIR:
        return scope_dir, WORKSPACE_DIR
    slug = scope_dir.name
    from . import vault  # deferred — vault imports config, avoid the cycle

    if not vault.has_vault(slug):
        return scope_dir, WORKSPACE_DIR
    live = vault.active_dir(slug)
    if live is None:
        raise vault.ProjectLocked(slug)
    return live, live


def resolve_content_dir(scope_dir: Path) -> Path:
    return resolve_scope(scope_dir)[0]


def list_projects() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.name for p in PROJECTS_DIR.iterdir() if p.is_dir())


def list_knowledge_bases() -> list[str]:
    if not KNOWLEDGE_BASES_DIR.exists():
        return []
    return sorted(p.name for p in KNOWLEDGE_BASES_DIR.iterdir() if p.is_dir())


# --- note base (standalone knowledge base) settings: name + members/roles —
# a kb.yml next to a project's project.yml, same shape and same
# permissive-when-unset philosophy (an empty/missing members list means
# "open to anyone", same as a project with no members configured — see
# get_role above for why that's deliberate, not a gap). This used to be the
# *only* policy a standalone knowledge base had (no way to configure
# members at all); now it's just the default state of a real one. ---


def read_kb_config(slug: str) -> dict:
    path = kb_dir(slug) / "kb.yml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def get_kb_name(slug: str) -> str:
    """The editable display name shown in the UI — falls back to the slug
    itself (the directory name) until someone sets one explicitly."""
    return read_kb_config(slug).get("name") or slug


def get_kb_icon(slug: str) -> str:
    return read_kb_config(slug).get("icon") or DEFAULT_KB_ICON


def get_kb_member_roles(slug: str) -> dict[str, str]:
    return dict(read_kb_config(slug).get("members") or {})


def get_kb_members(slug: str) -> list[str]:
    return list(get_kb_member_roles(slug).keys())


def get_kb_role(slug: str, username: str) -> str:
    roles = get_kb_member_roles(slug)
    if not roles:
        return "admin"
    return roles.get(username, DEFAULT_ROLE)


def is_kb_admin(slug: str, username: str) -> bool:
    return get_kb_role(slug, username) == "admin"


def can_edit_kb(slug: str, username: str) -> bool:
    return get_kb_role(slug, username) in ("admin", "editor")


def set_kb_name(slug: str, name: str) -> str:
    cfg = read_kb_config(slug)
    cfg["name"] = name
    _write_kb_config(slug, cfg)
    return name


def set_kb_icon(slug: str, icon: str) -> str:
    cfg = read_kb_config(slug)
    cfg["icon"] = icon
    _write_kb_config(slug, cfg)
    return icon


def set_kb_member_role(slug: str, username: str, role: str) -> dict[str, str]:
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role!r} (must be one of {VALID_ROLES})")
    cfg = read_kb_config(slug)
    roles = dict(cfg.get("members") or {})
    roles[username] = role
    cfg["members"] = roles
    _write_kb_config(slug, cfg)
    return roles


def remove_kb_member(slug: str, username: str) -> dict[str, str]:
    cfg = read_kb_config(slug)
    roles = {u: r for u, r in (cfg.get("members") or {}).items() if u != username}
    cfg["members"] = roles
    _write_kb_config(slug, cfg)
    return roles


def _write_kb_config(slug: str, cfg: dict) -> None:
    path = kb_dir(slug) / "kb.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def list_known_users() -> list[str]:
    """Every username listed as a member of any project or note base —
    powers the login dropdown. There's no separate user registry (yet);
    project.yml/kb.yml membership is it."""
    users: set[str] = set()
    for slug in list_projects():
        users.update(get_members(slug))
    for slug in list_knowledge_bases():
        users.update(get_kb_members(slug))
    return sorted(users)


def get_user_profile(username: str) -> dict:
    """Optional per-user profile (full_name, photo — a data: URI, kept small
    client-side before upload). Usernames themselves still come only from
    project membership (see list_known_users); this is just display
    metadata, and a missing file means "no profile set yet", not "unknown
    user" — same permissive-when-absent spirit as the rest of config.py."""
    path = USERS_DIR / f"{username}.yml"
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def set_user_profile(username: str, full_name: str | None, photo: str | None) -> dict:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    existing_email = get_user_profile(username).get("email")
    profile = {"full_name": full_name, "photo": photo, "email": existing_email}
    (USERS_DIR / f"{username}.yml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return profile


def find_username_by_email(email: str) -> str | None:
    """Maps a verified Google email (see main.py's /auth/google/callback) to
    an existing username, via the `email:` field on that user's profile
    (workspace/users/<username>.yml — hand-editable like project.yml/kb.yml,
    same "files are the source of truth" spirit as the rest of this app).
    Google login only ever authenticates an identity that's already a
    member of something; it never creates an account on its own."""
    email = (email or "").strip().lower()
    if not email:
        return None
    for username in list_known_users():
        if (get_user_profile(username).get("email") or "").strip().lower() == email:
            return username
    return None
