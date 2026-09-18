import os
import re
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

# Editable/git-versioned like everything else under WORKSPACE_DIR — not the
# app's own source tree, so an edit made through the app (see main.py's
# template routes) survives a redeploy instead of being baked into the
# image and overwritten by the next build. Seeded once from the app's
# shipped starter templates by ensure_default_templates().
TEMPLATES_DIR = WORKSPACE_DIR / "templates"
_SHIPPED_TEMPLATES_DIR = APP_DIR / "templates"

PROJECTS_DIR = WORKSPACE_DIR / "projects"
KNOWLEDGE_BASES_DIR = WORKSPACE_DIR / "knowledge-bases"
USERS_DIR = WORKSPACE_DIR / "users"

DEFAULT_STATUSES = ["proposal", "backlog", "ready", "in_progress", "review", "done"]
VALID_TEMPLATE_TYPES = ("task", "knowledge")

# Pastel defaults for the Kanban/status-color feature — reuses this app's
# own container tokens (static/style.css's Material 3 palette) for the
# three hues that already exist here (primary/secondary/tertiary), filling
# in the rest with new tones in the same "light tint, dark-enough text"
# family, so a project that's never touched status_colors still looks
# intentional rather than arbitrary.
DEFAULT_STATUS_COLOR_PALETTE = [
    "#e4e1ff",  # primary container (lavender)
    "#e0e1f9",  # secondary container (slate-blue)
    "#cfe8fb",  # sky
    "#fdedc4",  # amber
    "#ffd9e8",  # tertiary container (pink)
    "#d3efdc",  # mint
]

VALID_ROLES = ["admin", "editor", "guest"]
DEFAULT_ROLE = "editor"

DEFAULT_PROJECT_ICON = "folder"
DEFAULT_KB_ICON = "menu_book"

# Folder metadata/ACL (docs/design/schema.md#notes-folders-and-two-views) —
# `folder.yml` is only written once someone deliberately configures a
# folder (creation, or the first time its settings panel is used); a
# folder with none is "normal", unrestricted (see permissions.check_folder_write)
# — same permissive-when-unset spirit as project members/knowledge
# assigned_to. `.attachments.yml` is separate and lazier still: it has to
# be able to exist even in a folder with no folder.yml at all (including
# the notes root, which is never "created" by anyone) — see main.py's
# attachment routes.
FOLDER_CONFIG_FILENAME = "folder.yml"
ATTACHMENTS_INDEX_FILENAME = ".attachments.yml"
SUBMODULES_REGISTRY_FILENAME = ".submodules.yml"
DEFAULT_FOLDER_TYPE = "normal"
VALID_FOLDER_TYPES = ("normal", "submodule")

MAX_UPLOAD_BYTES = int(os.environ.get("PRAXIS_MAX_UPLOAD_MB", "25")) * 1024 * 1024
# Read-only attachments only (main.py never parses these) — a deliberately
# short allowlist, not a general file-upload feature.
ALLOWED_ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


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


def get_status_colors(slug: str) -> dict[str, str]:
    """Explicit per-status overrides only — a status with no entry here
    falls back to DEFAULT_STATUS_COLOR_PALETTE, cycled by its position in
    get_statuses (see status_color, which resolves that fallback)."""
    return dict(read_project_config(slug).get("status_colors") or {})


def status_color(slug: str, status: str) -> str:
    overrides = get_status_colors(slug)
    if status in overrides:
        return overrides[status]
    statuses = get_statuses(slug)
    index = statuses.index(status) if status in statuses else 0
    return DEFAULT_STATUS_COLOR_PALETTE[index % len(DEFAULT_STATUS_COLOR_PALETTE)]


def set_status_color(slug: str, status: str, color: str) -> dict[str, str]:
    if status not in get_statuses(slug):
        raise ValueError(f"Unknown status: {status!r}")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError(f"Invalid color: {color!r} (expected #rrggbb)")
    cfg = read_project_config(slug)
    colors = dict(cfg.get("status_colors") or {})
    colors[status] = color.lower()
    cfg["status_colors"] = colors
    _write_project_config(slug, cfg)
    return colors


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


def read_folder_config(folder_dir: Path) -> dict:
    """`folder.yml` for one specific folder (a directory under some scope's
    knowledge/) — never present for a folder nobody has configured yet, see
    this module's FOLDER_CONFIG_FILENAME docstring."""
    path = folder_dir / FOLDER_CONFIG_FILENAME
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def write_folder_config(folder_dir: Path, cfg: dict) -> None:
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / FOLDER_CONFIG_FILENAME).write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def set_folder_users(folder_dir: Path, acting_user: str, users: list[str]) -> dict:
    """Write-ACL for a folder (docs/design/schema.md) — `owner` is set once,
    the first time anyone configures this folder (creation, or the first
    settings-panel edit of a pre-existing one), and never changes here."""
    cfg = read_folder_config(folder_dir)
    cfg.setdefault("owner", acting_user)
    cfg.setdefault("type", DEFAULT_FOLDER_TYPE)
    cfg["users"] = list(users)
    write_folder_config(folder_dir, cfg)
    return cfg


def read_submodules_registry(notes_root: Path) -> dict:
    """path -> {owner, remote} for every submodule folder in one scope's
    knowledge/ — kept OUTSIDE the folder itself (unlike a normal folder's
    folder.yml), because once a path is a real git submodule, everything
    inside it belongs to that submodule's own repo — the parent repo can no
    longer track a plain file living alongside it there. One registry file
    at the notes root instead, `.submodules.yml` (never inside any of the
    folders it describes)."""
    path = notes_root / SUBMODULES_REGISTRY_FILENAME
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def write_submodules_registry(notes_root: Path, entries: dict) -> None:
    notes_root.mkdir(parents=True, exist_ok=True)
    (notes_root / SUBMODULES_REGISTRY_FILENAME).write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")


def register_submodule(notes_root: Path, path: str, owner: str, remote: str) -> None:
    entries = read_submodules_registry(notes_root)
    entries[path] = {"owner": owner, "remote": remote}
    write_submodules_registry(notes_root, entries)


def unregister_submodule(notes_root: Path, path: str) -> None:
    entries = read_submodules_registry(notes_root)
    entries.pop(path, None)
    write_submodules_registry(notes_root, entries)


def read_attachments_index(folder_dir: Path) -> dict:
    """filename -> {owner, uploaded} for every attachment uploaded into this
    exact folder (not recursive) — see main.py's attachment routes. Lazily
    created on first upload; can exist even where read_folder_config can't
    (e.g. the notes root, which nobody ever "creates")."""
    path = folder_dir / ATTACHMENTS_INDEX_FILENAME
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def write_attachments_index(folder_dir: Path, entries: dict) -> None:
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / ATTACHMENTS_INDEX_FILENAME).write_text(yaml.safe_dump(entries, sort_keys=False), encoding="utf-8")


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


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "untitled"


def _unique_slug(base: str, existing: list[str]) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def create_project(name: str, owner: str, icon: str | None = None) -> str:
    """A brand-new project — unlike an open (memberless) one, it starts with
    the creator as its sole admin, so making one doesn't accidentally open
    it to everyone; they can add members from its Settings panel afterward."""
    slug = _unique_slug(_slugify(name), list_projects())
    d = project_dir(slug)
    (d / "tasks").mkdir(parents=True, exist_ok=True)
    (d / "knowledge").mkdir(parents=True, exist_ok=True)
    _write_project_config(slug, {
        "name": name,
        "icon": icon or DEFAULT_PROJECT_ICON,
        "members": {owner: "admin"},
    })
    return slug


PERSONAL_PROJECT_ICON = "person"


def personal_project_slug(username: str) -> str:
    return f"personal-{username}"


def is_personal_project(slug: str) -> bool:
    """A project flagged `personal: true` in its own project.yml — nobody
    else is ever added to it (see main.py's add_project_member), and it's
    left out of the regular project list (main.py's list_projects): it
    lives inside Home instead (see ensure_personal_project), not alongside
    real, multi-person projects."""
    return bool(read_project_config(slug).get("personal"))


def ensure_personal_project(username: str) -> str:
    """Every account gets exactly one project nobody else belongs to, for
    personal tasks/notes. Home's "my tasks" (main.py's my_tasks) is already
    just an aggregation over every project a person is in, so this is most
    of the feature: a project like any other, just one whose only member is
    its owner and that's hidden from the regular project list/Members UI
    (is_personal_project) — no separate "personal task" concept anywhere
    else. Slug is derived straight from the username, so this is
    idempotent: calling it again for the same person (e.g. on every
    login/Home load) is a no-op once it exists."""
    slug = personal_project_slug(username)
    if slug not in list_projects():
        d = project_dir(slug)
        (d / "tasks").mkdir(parents=True, exist_ok=True)
        (d / "knowledge").mkdir(parents=True, exist_ok=True)
        _write_project_config(slug, {
            "name": "Personal tasks",
            "icon": PERSONAL_PROJECT_ICON,
            "members": {username: "admin"},
            "personal": True,
        })
    return slug


def create_kb(name: str, owner: str, icon: str | None = None) -> str:
    slug = _unique_slug(_slugify(name), list_knowledge_bases())
    d = kb_dir(slug)
    (d / "knowledge").mkdir(parents=True, exist_ok=True)
    _write_kb_config(slug, {
        "name": name,
        "icon": icon or DEFAULT_KB_ICON,
        "members": {owner: "admin"},
    })
    return slug


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
    """Every username that's either a member of some project/note base, or
    has its own profile file (workspace/users/<username>.yml) — the latter
    covers a bare account auto-provisioned on first OIDC login (see
    provision_user_from_email) that isn't part of anything yet. A profile
    file is what makes someone "known" now; project.yml/kb.yml membership
    only decides what they can *see*, not whether they can log in or show
    up as a person (assignee, tag-chip, etc.) elsewhere."""
    users: set[str] = set()
    for slug in list_projects():
        users.update(get_members(slug))
    for slug in list_knowledge_bases():
        users.update(get_kb_members(slug))
    if USERS_DIR.exists():
        users.update(p.stem for p in USERS_DIR.iterdir() if p.suffix == ".yml")
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


def link_user_email(username: str, email: str) -> dict:
    """Links a username to the email its owner will log in with via OIDC
    (see find_username_by_email/main.py's oidc_callback) — the missing half
    of adding a project/kb member: membership grants access, this is what
    lets that person's Google/metroon identity actually resolve to it.
    Preserves any full_name/photo already on the profile, same
    read-modify-write shape as set_user_profile."""
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    profile = {**get_user_profile(username), "email": email.strip().lower()}
    (USERS_DIR / f"{username}.yml").write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return profile


def provision_user_from_email(email: str) -> str:
    """Auto-creates a bare account (no project/kb membership) for a
    verified email logging into praxis.md for the first time — see main.py's
    oidc_callback. Being allowed through metroon (which already gates every
    other agorae service the same way) is enough on its own; a project/kb
    admin adding someone as a member only decides what they can *see* once
    they're in, same as archeion/stoa already auto-provision on first OIDC
    login. Username mirrors Forgejo's own derivation (the email's local
    part) so a person's identity looks the same across services."""
    base = re.sub(r"[^a-z0-9._-]+", "-", email.split("@")[0].strip().lower()).strip("-.") or "user"
    username = _unique_slug(base, list_known_users())
    link_user_email(username, email)
    return username


def ensure_default_templates() -> None:
    """Seeds workspace/templates/ from the app's shipped starter templates,
    once, the first time each one is missing — never overwrites a template
    someone already edited or added through the app (see main.py's
    template routes). Same "seed once, then it's just data" pattern as
    git_store.ensure_baseline_commit; called from main.py's lifespan."""
    if not _SHIPPED_TEMPLATES_DIR.exists():
        return
    for item_type in VALID_TEMPLATE_TYPES:
        src_dir = _SHIPPED_TEMPLATES_DIR / item_type
        if not src_dir.exists():
            continue
        dest_dir = TEMPLATES_DIR / item_type
        dest_dir.mkdir(parents=True, exist_ok=True)
        for tpl in src_dir.glob("*.md"):
            dest = dest_dir / tpl.name
            if not dest.exists():
                dest.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")


def _sanitize_template_name(raw: str) -> str:
    """A safe, flat filename for a template — no path separators or
    traversal, always ending in .md. Same defensive spirit as main.py's
    _sanitize_relpath for folder paths."""
    name = re.sub(r"[^\w.-]", "-", raw.strip())
    name = name.removesuffix(".md") or "untitled"
    return f"{name}.md"


def list_template_names(item_type: str) -> list[str]:
    d = TEMPLATES_DIR / item_type
    return sorted(p.name for p in d.glob("*.md")) if d.exists() else []


def get_template(item_type: str, name: str) -> str:
    path = TEMPLATES_DIR / item_type / name
    if not path.exists():
        raise FileNotFoundError(name)
    return path.read_text(encoding="utf-8")


def set_template(item_type: str, raw_name: str, body: str) -> str:
    """Creates or overwrites a template — the same file either way, since
    there's nothing else identifying "this template" besides its name."""
    if item_type not in VALID_TEMPLATE_TYPES:
        raise ValueError(f"Invalid template type: {item_type!r}")
    name = _sanitize_template_name(raw_name)
    dest_dir = TEMPLATES_DIR / item_type
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / name).write_text(body, encoding="utf-8")
    return name


def find_username_by_email(email: str) -> str | None:
    """Maps a verified OIDC email (see main.py's /auth/oidc/callback) to
    an existing username, via the `email:` field on that user's profile
    (workspace/users/<username>.yml — hand-editable like project.yml/kb.yml,
    same "files are the source of truth" spirit as the rest of this app).
    Doesn't provision anything itself — see provision_user_from_email for
    what happens when this comes back empty."""
    email = (email or "").strip().lower()
    if not email:
        return None
    for username in list_known_users():
        if (get_user_profile(username).get("email") or "").strip().lower() == email:
            return username
    return None
