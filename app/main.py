import asyncio
import datetime
import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import frontmatter
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import archeion_mirror, config, events, git_store, index, storage, vault
from .permissions import check_body_save, check_header_save


def _close_project(slug: str) -> None:
    """Reseal an open project and drop its cached repo handle — the one
    place both of those have to happen together, whether it's an idle
    timeout, an explicit lock, or shutdown (see lifespan/sweep below)."""
    vault.lock_project(slug, on_before_wipe=git_store.forget_repo)


async def _idle_sweep_loop() -> None:
    """Reseal any project nobody's touched in a while — see
    vault.IDLE_TIMEOUT_SECONDS. A minute's granularity is plenty; nothing
    here is time-critical."""
    while True:
        await asyncio.sleep(60)
        with git_store.LOCK:
            vault.sweep_idle(on_before_wipe=git_store.forget_repo)


@asynccontextmanager
async def lifespan(app: FastAPI):
    git_store.repo(config.WORKSPACE_DIR)  # create/open the shared workspace repo (note bases + unencrypted projects)
    git_store.ensure_baseline_commit(config.WORKSPACE_DIR)  # see its docstring — a one-time bootstrap for a pre-existing workspace
    config.ensure_default_templates()  # seed workspace/templates/ once — see its docstring
    index.rebuild_all()  # the index is a cache — always rebuilt fresh from the files at startup
    sweep_task = asyncio.create_task(_idle_sweep_loop())
    yield
    sweep_task.cancel()
    with git_store.LOCK:
        vault.lock_all(on_before_wipe=git_store.forget_repo)  # don't leave any project decrypted on disk across a restart


app = FastAPI(title="praxis.md", lifespan=lifespan)

# Dev-only default — override with a real secret via PRAXIS_SESSION_SECRET
# before this ever runs anywhere but a single developer's machine; it's
# what signs the session cookie.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("PRAXIS_SESSION_SECRET", "dev-only-insecure-secret"),
    same_site="lax",
)


@app.exception_handler(vault.ProjectLocked)
def _project_locked_handler(request: Request, exc: vault.ProjectLocked):
    return JSONResponse(status_code=423, content={"locked": True, "slug": exc.slug, "detail": str(exc)})


class LoginRequest(BaseModel):
    username: str


class MemberRequest(BaseModel):
    username: str
    role: str = config.DEFAULT_ROLE
    email: str | None = None  # optional: links `username` for OIDC login (see config.link_user_email)


class TransferOwnerRequest(BaseModel):
    new_owner: str


class RenameRequest(BaseModel):
    filename: str  # basename only, no extension — see _rename_item_file


class FolderRequest(BaseModel):
    path: str  # relative to the notes root, e.g. "meetings" or "meetings/2026"


class MoveRequest(BaseModel):
    folder: str  # relative to the notes root; "" moves it back to the root


class UserProfileRequest(BaseModel):
    full_name: str | None = None
    photo: str | None = None  # a data: URI (see set_user_profile) — full replace, like the header form


class KbConfigRequest(BaseModel):
    name: str
    icon: str


class ProjectConfigRequest(BaseModel):
    name: str
    icon: str


class NewScopeRequest(BaseModel):
    """Creating a project or note base — just a name (icon optional, falls
    back to the type's default). Slug is derived from the name server-side,
    never supplied by the client (see config.create_project/create_kb)."""

    name: str
    icon: str | None = None


class CreateRequest(BaseModel):
    type: Literal["task", "knowledge"]
    template: str | None = None
    assigned_to: list[str] = []
    tags: list[str] = []
    due_date: str | None = None
    status: str | None = None
    parent: str | None = None
    folder: str | None = None  # notes only — which subfolder of knowledge/ to create it in


class SaveRequest(BaseModel):
    body: str
    # the blob version the client started editing from (see GET .../items/{id});
    # omitted (or stale) just falls back to last-write-wins — see _save_item
    base_version: str | None = None


class HeaderUpdateRequest(BaseModel):
    """The owner-editable header fields, sent as a full replace (the client's
    structured form always submits every current value, never a partial
    patch) — see docs/design/schema.md#editor. status/due_date only apply
    to tasks and are ignored for knowledge; assigned_to applies to both
    ("Assigned to" vs "Users" in the UI — see check_body_save)."""

    tags: list[str] = []
    parent: str | None = None
    status: str | None = None
    due_date: str | None = None
    assigned_to: list[str] = []


def get_current_user(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Not logged in")
    return user


def require_project_access(slug: str, user: str = Depends(get_current_user)) -> str:
    if slug not in config.list_projects():
        raise HTTPException(404, "Project not found")
    members = config.get_members(slug)
    if members and user not in members:
        raise HTTPException(403, f"'{user}' is not a member of project '{slug}'")
    return user


def require_project_admin(slug: str, user: str = Depends(require_project_access)) -> str:
    if not config.is_admin(slug, user):
        raise HTTPException(403, f"'{user}' is not an admin of project '{slug}'")
    return user


def require_project_editor(slug: str, user: str = Depends(require_project_access)) -> str:
    if not config.can_edit(slug, user):
        raise HTTPException(403, f"'{user}' has guest (read-only) access to project '{slug}'")
    return user


def require_kb_access(slug: str, user: str = Depends(get_current_user)) -> str:
    if slug not in config.list_knowledge_bases():
        raise HTTPException(404, "Note base not found")
    members = config.get_kb_members(slug)
    if members and user not in members:
        raise HTTPException(403, f"'{user}' is not a member of note base '{slug}'")
    return user


def require_kb_admin(slug: str, user: str = Depends(require_kb_access)) -> str:
    if not config.is_kb_admin(slug, user):
        raise HTTPException(403, f"'{user}' is not an admin of note base '{slug}'")
    return user


def require_kb_editor(slug: str, user: str = Depends(require_kb_access)) -> str:
    if not config.can_edit_kb(slug, user):
        raise HTTPException(403, f"'{user}' has guest (read-only) access to note base '{slug}'")
    return user


# --- shared helpers, parametrized by scope directory ---


def _notes_root(scope_dir: Path) -> Path:
    return scope_dir / "knowledge"


def _scope_channel(scope_dir: Path) -> str:
    """The events.py channel name for a scope directory — "project:<slug>"
    or "kb:<slug>" — so a single publish reaches every client subscribed to
    that project/note base's SSE stream, see events."""
    kind = "project" if scope_dir.parent == config.PROJECTS_DIR else "kb"
    return f"{kind}:{scope_dir.name}"


async def _sse_stream(request: Request, channel: str):
    q = events.bus.subscribe(channel)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=15)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"  # SSE comment line — keeps proxies/browsers from timing out the connection
    finally:
        events.bus.unsubscribe(channel, q)


def _publish_item_changed(scope_dir: Path, item_id: str | None, acting_user: str) -> None:
    """Tell every client subscribed to this scope's SSE stream that an item
    changed — the frontend refreshes its list and, if it's the item
    currently open, offers to reload rather than silently overwriting an
    in-progress edit. `by` lets the saving client's own tab recognize and
    ignore its own change instead of notifying itself. Only called when
    commit_all actually made a commit (rewriting identical bytes is a no-op,
    not a real change)."""
    events.bus.publish(_scope_channel(scope_dir), {"type": "item_changed", "id": item_id, "by": acting_user})


def _mirror_project_to_archeion(slug: str, acting_user: str, message: str) -> None:
    """Best-effort push of a project's current state to its archeion mirror
    (see archeion_mirror.py) — called after every project-scope item
    mutation. Never touches an encrypted project: its whole point is
    staying off any second copy, mirror included."""
    if vault.has_vault(slug):
        return
    archeion_mirror.sync_project(slug, config.get_project_name(slug), config.project_dir(slug), acting_user, message)


def _sync_project_collaborators_to_archeion(slug: str) -> None:
    if vault.has_vault(slug):
        return
    archeion_mirror.sync_collaborators(
        slug, config.get_project_name(slug), config.get_members(slug), lambda u: config.get_user_profile(u).get("email")
    )


def _sanitize_relpath(raw: str) -> str:
    """A folder path made only of sanitized segments, "/"-joined — used for
    both creating a folder and placing/moving a note into one. Empty
    segments (leading/trailing/doubled slashes, ".", "..") are dropped
    rather than rejected, so "a//b/" and "a/b" land the same place."""
    parts = [re.sub(r"[^\w-]", "-", part.strip()) for part in raw.split("/")]
    return "/".join(p for p in parts if p and p != "." and p != "..")


def _list_items(scope_dir: Path) -> list[dict]:
    return index.list_items(config.resolve_content_dir(scope_dir))


def _list_folders(scope_dir: Path) -> list[str]:
    """Every folder under knowledge/, flat (not just ones with notes
    directly in them — an empty folder someone just created has to be
    navigable too, and _list_items only sees files)."""
    root = _notes_root(config.resolve_content_dir(scope_dir))
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_dir())


def _create_folder(scope_dir: Path, req: FolderRequest, acting_user: str) -> dict:
    safe_path = _sanitize_relpath(req.path)
    if not safe_path:
        raise HTTPException(400, "Invalid folder path.")
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        folder_dir = _notes_root(content_dir) / safe_path
        folder_dir.mkdir(parents=True, exist_ok=True)
        # git tracks files, not directories — an empty folder needs a
        # placeholder to survive a fresh clone/checkout of the history
        (folder_dir / ".gitkeep").touch()
        if git_store.commit_all(git_root, f"Create folder {safe_path}", acting_user):
            _publish_item_changed(scope_dir, None, acting_user)
    return {"path": safe_path}


def _move_item(scope_dir: Path, item_id: str, req: MoveRequest, acting_user: str) -> dict:
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        path = index.find_path_by_id(content_dir, item_id)
        if path is None:
            raise HTTPException(404, "Item not found")
        post = storage.load(path)
        if post.metadata.get("owner") != acting_user:
            raise HTTPException(403, "Only the owner can move this note.")
        if post.metadata.get("type") != "knowledge":
            raise HTTPException(400, "Only notes can be organized into folders.")

        dest_dir = _notes_root(content_dir) / _sanitize_relpath(req.folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        new_path = dest_dir / path.name
        if new_path != path and new_path.exists():
            raise HTTPException(409, f"A file named {new_path.name} already exists there.")
        path.rename(new_path)
        index.reindex_item(content_dir, new_path)
        if git_store.commit_all(git_root, f"Move {item_id} to {req.folder or '(root)'}", acting_user):
            _publish_item_changed(scope_dir, item_id, acting_user)
    return {"path": str(new_path.relative_to(content_dir))}


def _delete_folder(scope_dir: Path, raw_path: str, acting_user: str) -> dict:
    """Deleting a folder deletes everything inside it (notes and any
    nested subfolders) — allowed only when acting_user owns every note in
    that subtree, checked up front so it's all-or-nothing: no partial
    delete that leaves someone else's notes orphaned in a folder that no
    longer shows up in the tree. A note can never be another item's
    `parent` (only a task can be — see _check_parent_is_task), so unlike
    _delete_item there's no "still referenced elsewhere" case to guard."""
    safe_path = _sanitize_relpath(raw_path)
    if not safe_path:
        raise HTTPException(400, "Invalid folder path.")
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        folder_dir = _notes_root(content_dir) / safe_path
        if not folder_dir.is_dir():
            raise HTTPException(404, "Folder not found.")

        inside = [
            item for item in index.list_items(content_dir)
            if item.get("folder") == safe_path or (item.get("folder") or "").startswith(f"{safe_path}/")
        ]
        not_owned = sorted(item["id"] for item in inside if item.get("owner") != acting_user)
        if not_owned:
            raise HTTPException(
                403,
                "Can only delete a folder if you own every note inside it — "
                f"not the owner of: {', '.join(not_owned)}.",
            )

        shutil.rmtree(folder_dir)
        index.rebuild_scope(content_dir)
        if git_store.commit_all(git_root, f"Delete folder {safe_path}", acting_user):
            _publish_item_changed(scope_dir, None, acting_user)
    return {"path": safe_path}


def _list_tags(scope_dir: Path) -> list[str]:
    """Every tag currently in use — powers the "pick an existing tag"
    autocomplete instead of letting tags be typed as free text."""
    return index.list_tags(config.resolve_content_dir(scope_dir))


def _get_item(scope_dir: Path, item_id: str, acting_user: str, can_edit_scope: bool) -> dict:
    content_dir, git_root = config.resolve_scope(scope_dir)
    path = index.find_path_by_id(content_dir, item_id)
    if path is None:
        raise HTTPException(404, "Item not found")
    post = storage.load(path)
    relpath = str(path.relative_to(git_root))
    item_type = post.metadata.get("type", "knowledge")
    is_owner = post.metadata.get("owner") == acting_user
    return {
        "id": item_id,
        "metadata": dict(post.metadata),
        "body": post.content,
        "filename": path.stem,
        # the client echoes this back as base_version on save, so a
        # concurrent edit can be detected and merged — see _save_item
        "version": git_store.current_blob_sha(git_root, relpath),
        # what the *server* will actually let this user do to this specific
        # item — combines the scope-wide editor-or-above check every
        # mutating route already enforces with the item-level owner/
        # assigned_to rules in permissions.py, so the frontend can hide or
        # disable a button instead of re-deriving the same rule and having
        # it drift from what the API actually allows
        "permissions": {
            "can_edit_body": can_edit_scope and check_body_save(post.metadata, item_type, acting_user).allowed,
            "can_edit_header": can_edit_scope and check_header_save(post.metadata, acting_user).allowed,
            "can_rename": can_edit_scope and is_owner,
            "can_transfer_owner": can_edit_scope and is_owner,
            "can_move": can_edit_scope and is_owner and item_type == "knowledge",
            "can_delete": can_edit_scope and is_owner,
        },
    }


def _check_parent_is_task(scope_dir: Path, parent_id: str) -> None:
    """Main task: always a task, whether the item pointing at it is a task
    or a note — checked here (create and header-save both go through this)
    so the API can't be used to set something else as a "main task",
    not just the picker in the UI."""
    content_dir = config.resolve_content_dir(scope_dir)
    parent_path = index.find_path_by_id(content_dir, parent_id)
    if parent_path is None:
        raise HTTPException(400, f"Main task '{parent_id}' not found.")
    if storage.load(parent_path).metadata.get("type") != "task":
        raise HTTPException(400, "Main task must be a task, not a knowledge item.")


def _create_item(scope_dir: Path, req: CreateRequest, project_slug: str | None, owner: str) -> dict:
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        if req.parent:
            _check_parent_is_task(scope_dir, req.parent)

        item_id = index.next_id(content_dir, req.type)
        today = datetime.date.today().isoformat()

        body = ""
        if req.template:
            tpl_path = config.TEMPLATES_DIR / req.type / req.template
            if not tpl_path.exists():
                raise HTTPException(404, f"Template not found: {req.type}/{req.template}")
            body = tpl_path.read_text(encoding="utf-8")

        assigned_to = req.assigned_to
        if req.type == "task" and not assigned_to and project_slug and config.get_members(project_slug) == [owner]:
            # a solo project (personal tasks — see config.ensure_personal_project)
            # has nobody else to leave a task unassigned "for" — defaulting to
            # yourself is what makes it show up in Home's "my tasks"
            # (main.py's my_tasks filters on assigned_to, not just ownership),
            # with no effect on who's allowed to edit it (the owner always is).
            assigned_to = [owner]

        meta = {
            "id": item_id,
            "type": req.type,
            "parent": req.parent,
            "owner": owner,
            "tags": req.tags,
            # "Assigned to" on a task, "Users" on a note (who besides the owner
            # can edit the body) — same field either way, see check_body_save
            "assigned_to": assigned_to,
            "created": today,
            "updated": today,
        }
        if req.type == "task":
            statuses = config.get_statuses(project_slug) if project_slug else config.DEFAULT_STATUSES
            meta["status"] = req.status or statuses[0]
            meta["due_date"] = req.due_date

        post = frontmatter.Post(body, **meta)
        if req.type == "task":
            dest_dir = content_dir / "tasks"
        else:
            dest_dir = _notes_root(content_dir)
            if req.folder:
                dest_dir = dest_dir / _sanitize_relpath(req.folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"{item_id}.md"
        dest_path.write_bytes(frontmatter.dumps(post).encode("utf-8"))

        index.reindex_item(content_dir, dest_path)
        if git_store.commit_all(git_root, f"Create {req.type} {item_id}", owner):
            _publish_item_changed(scope_dir, item_id, owner)

    return {"id": item_id, "path": str(dest_path.relative_to(content_dir))}


def _save_item(scope_dir: Path, item_id: str, req: SaveRequest, acting_user: str) -> dict:
    """Body-only save. The request carries no header at all — assigned_to
    (or anyone, for knowledge) can hit this freely; there's no header to
    smuggle in and nothing here to reject on that account.

    Concurrency: if req.base_version is stale (someone else saved this note
    since the client loaded it), the two bodies are merged with git's own
    three-way text merge rather than one silently clobbering the other. A
    clean merge is committed like any other save; a real conflict raises
    409 with the conflict markers instead of writing anything — see
    docs/design/schema.md#versioning."""
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        path = index.find_path_by_id(content_dir, item_id)
        if path is None:
            raise HTTPException(404, "Item not found")

        post = storage.load(path)
        item_type = post.metadata.get("type", "knowledge")
        decision = check_body_save(post.metadata, item_type, acting_user)
        if not decision.allowed:
            raise HTTPException(409, decision.reason)

        relpath = str(path.relative_to(git_root))
        current_version = git_store.current_blob_sha(git_root, relpath)

        new_body = req.body
        merged = False
        if req.base_version and current_version and req.base_version != current_version:
            try:
                base_body = frontmatter.loads(git_store.blob_content(git_root, req.base_version)).content
                ours_body = frontmatter.loads(git_store.blob_content(git_root, current_version)).content
            except Exception:
                base_body = None
            if base_body is None:
                raise HTTPException(409, {
                    "conflict": True,
                    "reason": "Someone else saved this note and the version you started from is gone — reload to see the latest.",
                    "current_body": post.content,
                    "version": current_version,
                })
            merged_body, had_conflicts = git_store.merge_body(base_body, ours_body, new_body)
            if had_conflicts:
                raise HTTPException(409, {
                    "conflict": True,
                    "reason": "Someone else edited this note at the same time. Resolve the conflict markers below and save again.",
                    "merged_body": merged_body,
                    "version": current_version,
                })
            new_body = merged_body
            merged = True

        post.content = new_body
        post.metadata["updated"] = datetime.date.today().isoformat()
        path.write_bytes(frontmatter.dumps(post).encode("utf-8"))
        index.reindex_item(content_dir, path)
        message = f"Merge concurrent edits to {item_id}" if merged else f"Edit {item_id}"
        commit_sha = git_store.commit_all(git_root, message, acting_user)
        # NOT commit_sha itself — that's a *commit* sha, but every version
        # token elsewhere (current_version above, _get_item's response) is a
        # *blob* sha (git_store.current_blob_sha). Returning the commit sha
        # here fed a bogus base_version into every save after the first,
        # which forced the merge path every time and handed a commit object
        # to blob_content() as if it were the file's blob — see the fixed
        # bug's writeup in docs/design/schema.md#versioning.
        new_version = git_store.current_blob_sha(git_root, relpath) if commit_sha else current_version
        if commit_sha:
            _publish_item_changed(scope_dir, item_id, acting_user)

    return {"ok": True, "merged": merged, "body": new_body if merged else None, "version": new_version}


def _save_header(scope_dir: Path, item_id: str, req: HeaderUpdateRequest, acting_user: str) -> dict:
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        path = index.find_path_by_id(content_dir, item_id)
        if path is None:
            raise HTTPException(404, "Item not found")

        post = storage.load(path)
        decision = check_header_save(post.metadata, acting_user)
        if not decision.allowed:
            raise HTTPException(403, decision.reason)

        if req.parent:
            _check_parent_is_task(scope_dir, req.parent)

        post.metadata["tags"] = req.tags
        post.metadata["parent"] = req.parent
        post.metadata["assigned_to"] = req.assigned_to
        if post.metadata.get("type") == "task":
            post.metadata["status"] = req.status
            post.metadata["due_date"] = req.due_date

        post.metadata["updated"] = datetime.date.today().isoformat()
        path.write_bytes(frontmatter.dumps(post).encode("utf-8"))
        index.reindex_item(content_dir, path)
        if git_store.commit_all(git_root, f"Update {item_id} header", acting_user):
            _publish_item_changed(scope_dir, item_id, acting_user)
    return {"ok": True}


def _rename_item_file(scope_dir: Path, item_id: str, req: RenameRequest, acting_user: str) -> dict:
    """Renaming the file is safe independent of `id` — find_path_by_id looks
    items up by the `id:` frontmatter field, never by filename, so the file
    can be called anything without breaking any link that points at the id
    (parent:, [[refs]], the API routes themselves)."""
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        path = index.find_path_by_id(content_dir, item_id)
        if path is None:
            raise HTTPException(404, "Item not found")
        post = storage.load(path)
        if post.metadata.get("owner") != acting_user:
            raise HTTPException(403, "Only the owner can rename this file.")

        safe_name = re.sub(r"[^\w-]", "-", req.filename.strip()).strip("-")
        if not safe_name:
            raise HTTPException(400, "Invalid filename.")
        new_path = path.parent / f"{safe_name}.md"
        if new_path != path and new_path.exists():
            raise HTTPException(409, f"A file named {new_path.name} already exists.")

        path.rename(new_path)
        index.reindex_item(content_dir, new_path)
        if git_store.commit_all(git_root, f"Rename {item_id} to {safe_name}", acting_user):
            _publish_item_changed(scope_dir, item_id, acting_user)
    return {"filename": new_path.stem}


def _transfer_owner(scope_dir: Path, item_id: str, req: TransferOwnerRequest, acting_user: str) -> dict:
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        path = index.find_path_by_id(content_dir, item_id)
        if path is None:
            raise HTTPException(404, "Item not found")
        post = storage.load(path)
        if post.metadata.get("owner") != acting_user:
            raise HTTPException(403, "Only the current owner can transfer ownership.")
        post.metadata["owner"] = req.new_owner
        post.metadata["updated"] = datetime.date.today().isoformat()
        path.write_bytes(frontmatter.dumps(post).encode("utf-8"))
        index.reindex_item(content_dir, path)
        if git_store.commit_all(git_root, f"Transfer {item_id} to {req.new_owner}", acting_user):
            _publish_item_changed(scope_dir, item_id, acting_user)
    return {"ok": True}


def _delete_item(scope_dir: Path, item_id: str, acting_user: str) -> dict:
    content_dir, git_root = config.resolve_scope(scope_dir)
    with git_store.LOCK:
        path = index.find_path_by_id(content_dir, item_id)
        if path is None:
            raise HTTPException(404, "Item not found")
        post = storage.load(path)
        if post.metadata.get("owner") != acting_user:
            raise HTTPException(403, "Only the owner can delete this item.")
        # a dangling `parent:` would otherwise point at nothing — same
        # "main task must exist" invariant _check_parent_is_task enforces
        # on the way in, just checked on the way out instead
        children = [item["id"] for item in index.list_items(content_dir) if item.get("parent") == item_id]
        if children:
            raise HTTPException(409, f"Can't delete: {len(children)} item(s) still have this as their main task.")
        path.unlink()
        index.remove_item(content_dir, item_id)
        if git_store.commit_all(git_root, f"Delete {item_id}", acting_user):
            _publish_item_changed(scope_dir, item_id, acting_user)
    return {"ok": True}


def _item_history(scope_dir: Path, item_id: str) -> list[dict]:
    content_dir, git_root = config.resolve_scope(scope_dir)
    path = index.find_path_by_id(content_dir, item_id)
    if path is None:
        raise HTTPException(404, "Item not found")
    relpath = str(path.relative_to(git_root))
    return git_store.file_history(git_root, relpath)


def _restore_item(scope_dir: Path, item_id: str, sha: str, acting_user: str) -> dict:
    """Restore an item's body to what it was at an earlier commit — done as
    an ordinary save (old body, current version as base_version) rather
    than rewriting history, so it goes through the exact same permission
    check and concurrent-edit safety net as any other save."""
    content_dir, git_root = config.resolve_scope(scope_dir)
    path = index.find_path_by_id(content_dir, item_id)
    if path is None:
        raise HTTPException(404, "Item not found")
    relpath = str(path.relative_to(git_root))
    try:
        old_body = frontmatter.loads(git_store.blob_at_commit(git_root, sha, relpath)).content
    except Exception:
        raise HTTPException(404, "That version could not be found.")
    current_version = git_store.current_blob_sha(git_root, relpath)
    return _save_item(scope_dir, item_id, SaveRequest(body=old_body, base_version=current_version), acting_user)


# --- auth: two modes, picked with PRAXIS_AUTH_MODE ---
#
# "dev" (the default — everything this app had before, unchanged): a
# dropdown of every known username, no password, session set on trust
# alone. Meant for local development/testing only — see
# docs/design/schema.md#permissions for what it doesn't guarantee.
#
# "oidc": real OAuth2/OIDC against whatever issuer PRAXIS_OIDC_DISCOVERY_URL
# points at (any standard OIDC provider — in agorae's deployment, that's
# metroon, see its README's "Real login (OIDC)" section) — logging in
# actually proves who you are to that provider, and get_current_user's
# session is set to a username that identity is linked to
# (config.find_username_by_email), auto-provisioning a bare one on first
# login if none exists yet (config.provision_user_from_email) — anyone
# metroon already lets through is trusted here too, same as archeion/stoa.
# Membership (config.py's members: lists) still decides what that account
# can actually *see* — a fresh one starts in zero projects/note bases.

AUTH_MODE = os.environ.get("PRAXIS_AUTH_MODE", "dev")

oauth = None
if AUTH_MODE == "oidc":
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="oidc",
        server_metadata_url=os.environ["PRAXIS_OIDC_DISCOVERY_URL"],
        client_id=os.environ["PRAXIS_OIDC_CLIENT_ID"],
        client_secret=os.environ["PRAXIS_OIDC_CLIENT_SECRET"],
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )


@app.get("/api/users")
def list_users():
    # email deliberately excluded — this endpoint is unauthenticated (powers
    # the login picker before anyone's signed in), and email is only ever
    # meant to be readable by the account it belongs to and admins managing
    # membership, not broadcast to anyone who can reach the site.
    result = []
    for u in config.list_known_users():
        profile = {k: v for k, v in config.get_user_profile(u).items() if k != "email"}
        result.append({"username": u, **profile})
    return result


@app.get("/api/directory")
def directory(user: str = Depends(get_current_user)):
    """Everyone allowed through metroon (name + email) — powers the
    autocomplete on "Add member" (see static/app.js) so an admin can pick
    someone instead of typing their email from memory. Being allowed
    through metroon is already what grants login access here (see
    oidc_callback/config.provision_user_from_email), so this is just
    surfacing the same list metroon already gates everything else by.
    Empty, not an error, if metroon isn't configured to answer it (e.g. in
    dev mode, or if PRAXIS_METROON_URL/PRAXIS_INTERNAL_SYNC_SECRET are unset)."""
    metroon_url = os.environ.get("PRAXIS_METROON_URL", "")
    secret = os.environ.get("PRAXIS_INTERNAL_SYNC_SECRET", "")
    if not metroon_url or not secret:
        return []
    try:
        res = httpx.get(
            f"{metroon_url}/internal/directory",
            headers={"X-Internal-Secret": secret},
            timeout=5,
        )
        res.raise_for_status()
        return res.json()
    except httpx.HTTPError:
        return []


@app.get("/api/auth/config")
def auth_config():
    # In oidc mode, name/photo are only editable in metroon (see
    # docs/METROON.md and update_my_profile below) — this tells the
    # frontend where to send someone instead of showing its own fields.
    # PRAXIS_METROON_PUBLIC_URL is the browser-reachable one (unlike
    # PRAXIS_METROON_URL, used server-to-server by /api/directory above).
    metroon_url = os.environ.get("PRAXIS_METROON_PUBLIC_URL", "")
    return {
        "mode": AUTH_MODE,
        "metroon_profile_url": f"{metroon_url}/profile" if AUTH_MODE == "oidc" and metroon_url else None,
    }


@app.get("/api/session")
def get_session(request: Request):
    return {"user": request.session.get("user")}


@app.post("/api/login")
def login(req: LoginRequest, request: Request):
    if AUTH_MODE != "dev":
        raise HTTPException(404)
    request.session["user"] = req.username
    return {"user": req.username}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/auth/oidc/login")
async def oidc_login(request: Request):
    if AUTH_MODE != "oidc":
        raise HTTPException(404)
    redirect_uri = str(request.url_for("oidc_callback"))
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


@app.get("/auth/oidc/callback")
async def oidc_callback(request: Request):
    if AUTH_MODE != "oidc":
        raise HTTPException(404)
    token = await oauth.oidc.authorize_access_token(request)
    # Not token.get("userinfo") — authlib only populates that from the ID
    # token's own claims, and (per plain OIDC spec conformance, which
    # metroon's oidc-provider library follows) an authorization_code flow's
    # ID token omits scope-derived claims like email/email_verified; they're
    # only guaranteed via the userinfo *endpoint*. Fetching it explicitly
    # works regardless of what the provider puts in the ID token.
    userinfo = await oauth.oidc.userinfo(token=token)
    email = userinfo.get("email")
    if not email or not userinfo.get("email_verified"):
        return RedirectResponse(f"/?auth_error={quote('That account has no verified email.')}")
    username = config.find_username_by_email(email)
    if not username:
        # Anyone who gets this far is already allowed through metroon, which
        # gates every other agorae service the same way — so being allowed
        # in is enough on its own, same as archeion/stoa auto-provisioning
        # on first login. This account starts in zero projects/note bases;
        # a project/kb admin adding them as a member is what gives it
        # somewhere to actually work, not what gates login itself.
        with git_store.LOCK:
            username = config.provision_user_from_email(email)
            git_store.commit_all(config.WORKSPACE_DIR, f"Provision {username}", username)
    request.session["user"] = username
    return RedirectResponse("/")


@app.put("/api/users/me/profile")
def update_my_profile(req: UserProfileRequest, user: str = Depends(get_current_user)):
    # In oidc mode, metroon is the only place name/photo can be edited —
    # its own profile-sync push (below) is what updates them here. Same
    # dev-only shape as /api/login above.
    if AUTH_MODE == "oidc":
        raise HTTPException(404)
    with git_store.LOCK:
        profile = config.set_user_profile(user, req.full_name, req.photo)
        git_store.commit_all(config.WORKSPACE_DIR, f"Update {user} profile", user)
    return {"username": user, **profile}


class ProfileSyncRequest(BaseModel):
    email: str
    full_name: str | None = None
    photo: str | None = None  # a data: URI, same shape as UserProfileRequest.photo


# metroon's single profile-editing terminal (see docs/METROON.md) pushes name/
# photo edits here so they land in praxis.md too, the same way it already
# pushes them into Matrix and Forgejo. Keyed by email (not username) because
# that's all metroon knows about a person; find_username_by_email maps it to
# an existing account the same way OIDC login itself does — this never
# creates one, only updates an account that's already a project/kb member.
@app.post("/api/internal/profile-sync")
def sync_profile_from_metroon(req: ProfileSyncRequest, request: Request):
    secret = os.environ.get("PRAXIS_INTERNAL_SYNC_SECRET", "")
    if not secret or request.headers.get("x-internal-secret") != secret:
        raise HTTPException(401)
    username = config.find_username_by_email(req.email)
    if not username:
        return {"synced": False}
    with git_store.LOCK:
        config.set_user_profile(username, req.full_name, req.photo)
        git_store.commit_all(config.WORKSPACE_DIR, f"Update {username} profile (via metroon)", username)
    return {"synced": True, "username": username}


# --- global routes ---


@app.get("/api/templates")
def list_templates():
    return {item_type: config.list_template_names(item_type) for item_type in config.VALID_TEMPLATE_TYPES}


@app.get("/api/templates/{item_type}/{name}")
def get_template(item_type: str, name: str, user: str = Depends(get_current_user)):
    if item_type not in config.VALID_TEMPLATE_TYPES:
        raise HTTPException(404, f"No such template type: {item_type!r}")
    try:
        body = config.get_template(item_type, name)
    except FileNotFoundError:
        raise HTTPException(404, "Template not found")
    return {"type": item_type, "name": name, "body": body}


class TemplateRequest(BaseModel):
    body: str


# Global, not scoped to a project/kb — same trust level the rest of this
# app already gives any logged-in member (e.g. GET /api/directory, the
# unauthenticated GET /api/templates above): templates are a shared,
# collaborative resource, not anyone's private content, and there's no
# site-admin concept anywhere else in this app to gate it behind instead.
@app.put("/api/templates/{item_type}/{name}")
def save_template(item_type: str, name: str, req: TemplateRequest, user: str = Depends(get_current_user)):
    if item_type not in config.VALID_TEMPLATE_TYPES:
        raise HTTPException(404, f"No such template type: {item_type!r}")
    with git_store.LOCK:
        saved_name = config.set_template(item_type, name, req.body)
        git_store.commit_all(config.WORKSPACE_DIR, f"Save template {item_type}/{saved_name}", user)
    return {"type": item_type, "name": saved_name}


@app.get("/api/me/tasks")
def my_tasks(user: str = Depends(get_current_user)):
    """Tasks assigned to the current user, grouped by project — the Home
    view. A locked project is silently skipped rather than turning the
    whole dashboard into a password prompt for a project nobody asked to
    open — its tasks reappear here on their own once someone unlocks it.
    A personal project (GET /api/me/personal-project) that's never had a
    task created in it yet just doesn't show up here — nothing to lose,
    it's created lazily on first use, not eagerly on every Home load."""
    result = []
    for slug in config.list_projects():
        members = config.get_members(slug)
        if members and user not in members:
            continue
        try:
            items = _list_items(config.project_dir(slug))
        except vault.ProjectLocked:
            continue
        assigned = [
            item for item in items
            if item.get("type") == "task" and user in (item.get("assigned_to") or [])
        ]
        if assigned:
            result.append({"project": slug, "name": config.get_project_name(slug), "tasks": assigned})
    return result


@app.get("/api/me/personal-project")
def my_personal_project(user: str = Depends(get_current_user)):
    """The slug of the current user's own personal project (created on
    first use if it doesn't exist yet) — lets the frontend post new
    personal tasks straight from Home without that project ever showing up
    in the regular project list (see list_projects/is_personal_project)."""
    with git_store.LOCK:
        slug = config.ensure_personal_project(user)
        git_store.commit_all(config.WORKSPACE_DIR, f"Ensure personal project for {user}", user)
    return {"slug": slug, "name": config.get_project_name(slug)}


@app.post("/api/projects")
def create_project(req: NewScopeRequest, user: str = Depends(get_current_user)):
    if not req.name.strip():
        raise HTTPException(400, "Name can't be empty.")
    with git_store.LOCK:
        slug = config.create_project(req.name.strip(), owner=user, icon=req.icon)
        git_store.commit_all(config.WORKSPACE_DIR, f"Create project {slug}", user)
    return {"slug": slug, "name": config.get_project_name(slug), "icon": config.get_project_icon(slug)}


@app.get("/api/projects")
def list_projects(user: str = Depends(get_current_user)):
    result = []
    for slug in config.list_projects():
        if config.is_personal_project(slug):
            continue
        members = config.get_members(slug)
        if members and user not in members:
            continue
        result.append({
            "slug": slug,
            "name": config.get_project_name(slug),
            "icon": config.get_project_icon(slug),
            "members": members,
        })
    return result


@app.post("/api/knowledge-bases")
def create_kb(req: NewScopeRequest, user: str = Depends(get_current_user)):
    if not req.name.strip():
        raise HTTPException(400, "Name can't be empty.")
    with git_store.LOCK:
        slug = config.create_kb(req.name.strip(), owner=user, icon=req.icon)
        git_store.commit_all(config.WORKSPACE_DIR, f"Create note base {slug}", user)
    return {"slug": slug, "name": config.get_kb_name(slug), "icon": config.get_kb_icon(slug)}


@app.get("/api/knowledge-bases")
def list_knowledge_bases(user: str = Depends(get_current_user)):
    result = []
    for slug in config.list_knowledge_bases():
        members = config.get_kb_members(slug)
        if members and user not in members:
            continue
        result.append({
            "slug": slug,
            "name": config.get_kb_name(slug),
            "icon": config.get_kb_icon(slug),
            "members": members,
        })
    return result


# --- project-scoped routes ---


@app.get("/api/projects/{slug}/config")
def project_config(slug: str, user: str = Depends(require_project_access)):
    statuses = config.get_statuses(slug)
    return {
        "name": config.get_project_name(slug),
        "icon": config.get_project_icon(slug),
        "statuses": statuses,
        # fully resolved (explicit override or the default palette's
        # cycled fallback) so the frontend never has to reimplement that
        # logic — see config.status_color
        "status_colors": {s: config.status_color(slug, s) for s in statuses},
        "members": config.get_member_roles(slug),
        "role": config.get_role(slug, user),
    }


class StatusColorRequest(BaseModel):
    color: str  # "#rrggbb"


@app.put("/api/projects/{slug}/statuses/{status}/color")
def project_set_status_color(slug: str, status: str, req: StatusColorRequest, user: str = Depends(require_project_admin)):
    with git_store.LOCK:
        try:
            colors = config.set_status_color(slug, status, req.color)
        except ValueError as e:
            raise HTTPException(400, str(e))
        git_store.commit_all(config.WORKSPACE_DIR, f"Set {slug}'s {status} color", user)
    return {"status_colors": {s: config.status_color(slug, s) for s in config.get_statuses(slug)}}


@app.put("/api/projects/{slug}/config")
def project_update_config(slug: str, req: ProjectConfigRequest, user: str = Depends(require_project_admin)):
    if not req.name.strip():
        raise HTTPException(400, "Name can't be empty.")
    with git_store.LOCK:
        result = {"name": config.set_project_name(slug, req.name.strip()), "icon": config.set_project_icon(slug, req.icon)}
        git_store.commit_all(config.WORKSPACE_DIR, f"Update {slug} settings", user)
    return result


@app.post("/api/projects/{slug}/members")
def add_project_member(slug: str, req: MemberRequest, user: str = Depends(require_project_admin)):
    if config.is_personal_project(slug):
        raise HTTPException(400, "This is a personal project — nobody else can be added to it.")
    if req.role not in config.VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {req.role!r} (must be one of {config.VALID_ROLES})")
    with git_store.LOCK:
        # a project with no members configured is open to anyone (everyone acts
        # as admin — see get_role); adding the *first* real member flips that to
        # "only these people", which would otherwise lock out the admin doing it
        # if they added someone else without also adding themselves
        was_open = not config.get_members(slug)
        roles = config.set_member_role(slug, req.username, req.role)
        if was_open and user not in roles:
            roles = config.set_member_role(slug, user, "admin")
        if req.email:
            config.link_user_email(req.username, req.email)
        git_store.commit_all(config.WORKSPACE_DIR, f"Add {req.username} to {slug}", user)
    _sync_project_collaborators_to_archeion(slug)
    return {"members": roles}


@app.delete("/api/projects/{slug}/members/{username}")
def remove_project_member(slug: str, username: str, user: str = Depends(require_project_admin)):
    if config.is_personal_project(slug):
        raise HTTPException(400, "This is a personal project — its only member can't be removed.")
    with git_store.LOCK:
        roles = config.remove_member(slug, username)
        git_store.commit_all(config.WORKSPACE_DIR, f"Remove {username} from {slug}", user)
    _sync_project_collaborators_to_archeion(slug)
    return {"members": roles}


class UnlockRequest(BaseModel):
    secret: str


@app.get("/api/projects/{slug}/lock-status")
def project_lock_status(slug: str, user: str = Depends(require_project_access)):
    return {"encrypted": vault.has_vault(slug), "open": vault.is_open(slug)}


@app.post("/api/projects/{slug}/encrypt")
def project_encrypt(slug: str, user: str = Depends(require_project_admin)):
    """First-time setup: seal an existing plaintext project into a vault
    and hand back the generated secret — shown to the admin exactly once
    (see vault.py's module docstring for why there's no second chance)."""
    if vault.has_vault(slug):
        raise HTTPException(409, "This project is already encrypted.")
    with git_store.LOCK:
        scope_dir = config.project_dir(slug)
        secret = vault.encrypt_project(
            slug,
            git_init=lambda d: git_store.init_standalone_repo(d, exclude=("project.yml", "vault.enc", ".seal-staging-*")),
        )
        git_store.forget_repo(scope_dir)  # its tasks/knowledge no longer live here — see vault.encrypt_project
        index.rebuild_scope(scope_dir)  # now empty until someone unlocks it again
        git_store.commit_all(config.WORKSPACE_DIR, f"Encrypt project {slug}", user)
    return {"secret": secret}


@app.post("/api/projects/{slug}/unlock")
def project_unlock(slug: str, req: UnlockRequest, user: str = Depends(require_project_access)):
    if not vault.has_vault(slug):
        raise HTTPException(400, "This project isn't encrypted.")
    with git_store.LOCK:
        try:
            content_dir = vault.unlock_project(slug, req.secret)
        except vault.WrongSecret:
            raise HTTPException(401, "Wrong project password.")
        git_store.repo(content_dir)  # open (and cache) its own inner repo
        index.rebuild_scope(content_dir)  # the index had nothing for this project while it was sealed
    return {"ok": True}


@app.post("/api/projects/{slug}/lock")
def project_lock(slug: str, user: str = Depends(require_project_admin)):
    with git_store.LOCK:
        _close_project(slug)
    return {"ok": True}


@app.get("/api/projects/{slug}/tags")
def project_tags(slug: str, user: str = Depends(require_project_access)):
    return _list_tags(config.project_dir(slug))


@app.get("/api/projects/{slug}/folders")
def project_folders(slug: str, user: str = Depends(require_project_access)):
    return _list_folders(config.project_dir(slug))


@app.post("/api/projects/{slug}/folders")
def project_create_folder(slug: str, req: FolderRequest, user: str = Depends(require_project_editor)):
    result = _create_folder(config.project_dir(slug), req, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Create folder {req.path}")
    return result


@app.delete("/api/projects/{slug}/folders/{folder_path:path}")
def project_delete_folder(slug: str, folder_path: str, user: str = Depends(require_project_editor)):
    result = _delete_folder(config.project_dir(slug), folder_path, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Delete folder {folder_path}")
    return result


@app.put("/api/projects/{slug}/items/{item_id}/folder")
def project_move_item(slug: str, item_id: str, req: MoveRequest, user: str = Depends(require_project_editor)):
    result = _move_item(config.project_dir(slug), item_id, req, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Move {item_id}")
    return result


@app.get("/api/projects/{slug}/items")
def project_items(slug: str, user: str = Depends(require_project_access)):
    return _list_items(config.project_dir(slug))


@app.get("/api/projects/{slug}/items/{item_id}")
def project_get_item(slug: str, item_id: str, user: str = Depends(require_project_access)):
    return _get_item(config.project_dir(slug), item_id, acting_user=user, can_edit_scope=config.can_edit(slug, user))


@app.post("/api/projects/{slug}/items")
def project_create_item(slug: str, req: CreateRequest, user: str = Depends(require_project_editor)):
    result = _create_item(config.project_dir(slug), req, project_slug=slug, owner=user)
    _mirror_project_to_archeion(slug, user, f"Create {result['id']}")
    return result


@app.put("/api/projects/{slug}/items/{item_id}")
def project_save_item(slug: str, item_id: str, req: SaveRequest, user: str = Depends(require_project_editor)):
    result = _save_item(config.project_dir(slug), item_id, req, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Save {item_id}")
    return result


@app.put("/api/projects/{slug}/items/{item_id}/header")
def project_save_header(slug: str, item_id: str, req: HeaderUpdateRequest, user: str = Depends(require_project_editor)):
    result = _save_header(config.project_dir(slug), item_id, req, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Update {item_id} header")
    return result


@app.put("/api/projects/{slug}/items/{item_id}/owner")
def project_transfer_owner(slug: str, item_id: str, req: TransferOwnerRequest, user: str = Depends(require_project_editor)):
    result = _transfer_owner(config.project_dir(slug), item_id, req, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Transfer {item_id} to {req.new_owner}")
    return result


@app.put("/api/projects/{slug}/items/{item_id}/filename")
def project_rename_item(slug: str, item_id: str, req: RenameRequest, user: str = Depends(require_project_editor)):
    result = _rename_item_file(config.project_dir(slug), item_id, req, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Rename {item_id} to {req.filename}")
    return result


@app.delete("/api/projects/{slug}/items/{item_id}")
def project_delete_item(slug: str, item_id: str, user: str = Depends(require_project_editor)):
    result = _delete_item(config.project_dir(slug), item_id, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Delete {item_id}")
    return result


@app.get("/api/projects/{slug}/items/{item_id}/history")
def project_item_history(slug: str, item_id: str, user: str = Depends(require_project_access)):
    return _item_history(config.project_dir(slug), item_id)


@app.post("/api/projects/{slug}/items/{item_id}/history/{sha}/restore")
def project_item_restore(slug: str, item_id: str, sha: str, user: str = Depends(require_project_editor)):
    result = _restore_item(config.project_dir(slug), item_id, sha, acting_user=user)
    _mirror_project_to_archeion(slug, user, f"Restore {item_id} to {sha[:8]}")
    return result


@app.post("/api/projects/{slug}/items/{item_id}/presence")
def project_item_presence(slug: str, item_id: str, user: str = Depends(require_project_access)):
    return {"users": events.touch_presence(f"project:{slug}", item_id, user)}


@app.get("/api/projects/{slug}/events")
async def project_events(slug: str, request: Request, user: str = Depends(require_project_access)):
    return StreamingResponse(_sse_stream(request, f"project:{slug}"), media_type="text/event-stream")


# --- standalone knowledge base ("Note base") routes — same permissive-
# when-no-members-configured policy as a project (require_kb_access), now
# that a note base has real settings instead of being open to anyone
# logged in unconditionally ---


@app.get("/api/knowledge-bases/{slug}/config")
def kb_config(slug: str, user: str = Depends(require_kb_access)):
    return {
        "name": config.get_kb_name(slug),
        "icon": config.get_kb_icon(slug),
        "members": config.get_kb_member_roles(slug),
        "role": config.get_kb_role(slug, user),
    }


@app.put("/api/knowledge-bases/{slug}/config")
def kb_update_config(slug: str, req: KbConfigRequest, user: str = Depends(require_kb_admin)):
    if not req.name.strip():
        raise HTTPException(400, "Name can't be empty.")
    with git_store.LOCK:
        result = {"name": config.set_kb_name(slug, req.name.strip()), "icon": config.set_kb_icon(slug, req.icon)}
        git_store.commit_all(config.WORKSPACE_DIR, f"Update {slug} settings", user)
    return result


@app.post("/api/knowledge-bases/{slug}/members")
def add_kb_member(slug: str, req: MemberRequest, user: str = Depends(require_kb_admin)):
    if req.role not in config.VALID_ROLES:
        raise HTTPException(400, f"Invalid role: {req.role!r} (must be one of {config.VALID_ROLES})")
    with git_store.LOCK:
        # same safeguard as add_project_member: don't let enabling membership on
        # a previously-open note base lock out the admin who just turned it on
        was_open = not config.get_kb_members(slug)
        roles = config.set_kb_member_role(slug, req.username, req.role)
        if was_open and user not in roles:
            roles = config.set_kb_member_role(slug, user, "admin")
        if req.email:
            config.link_user_email(req.username, req.email)
        git_store.commit_all(config.WORKSPACE_DIR, f"Add {req.username} to {slug}", user)
    return {"members": roles}


@app.delete("/api/knowledge-bases/{slug}/members/{username}")
def remove_kb_member(slug: str, username: str, user: str = Depends(require_kb_admin)):
    with git_store.LOCK:
        roles = config.remove_kb_member(slug, username)
        git_store.commit_all(config.WORKSPACE_DIR, f"Remove {username} from {slug}", user)
    return {"members": roles}


@app.get("/api/knowledge-bases/{slug}/tags")
def kb_tags(slug: str, user: str = Depends(require_kb_access)):
    return _list_tags(config.kb_dir(slug))


@app.get("/api/knowledge-bases/{slug}/folders")
def kb_folders(slug: str, user: str = Depends(require_kb_access)):
    return _list_folders(config.kb_dir(slug))


@app.post("/api/knowledge-bases/{slug}/folders")
def kb_create_folder(slug: str, req: FolderRequest, user: str = Depends(require_kb_editor)):
    return _create_folder(config.kb_dir(slug), req, acting_user=user)


@app.put("/api/knowledge-bases/{slug}/items/{item_id}/folder")
def kb_move_item(slug: str, item_id: str, req: MoveRequest, user: str = Depends(require_kb_editor)):
    return _move_item(config.kb_dir(slug), item_id, req, acting_user=user)


@app.delete("/api/knowledge-bases/{slug}/folders/{folder_path:path}")
def kb_delete_folder(slug: str, folder_path: str, user: str = Depends(require_kb_editor)):
    return _delete_folder(config.kb_dir(slug), folder_path, acting_user=user)


@app.get("/api/knowledge-bases/{slug}/items")
def kb_items(slug: str, user: str = Depends(require_kb_access)):
    return _list_items(config.kb_dir(slug))


@app.get("/api/knowledge-bases/{slug}/items/{item_id}")
def kb_get_item(slug: str, item_id: str, user: str = Depends(require_kb_access)):
    return _get_item(config.kb_dir(slug), item_id, acting_user=user, can_edit_scope=config.can_edit_kb(slug, user))


@app.post("/api/knowledge-bases/{slug}/items")
def kb_create_item(slug: str, req: CreateRequest, user: str = Depends(require_kb_editor)):
    if req.type != "knowledge":
        raise HTTPException(400, "Standalone knowledge bases only hold knowledge items, not tasks")
    return _create_item(config.kb_dir(slug), req, project_slug=None, owner=user)


@app.put("/api/knowledge-bases/{slug}/items/{item_id}")
def kb_save_item(slug: str, item_id: str, req: SaveRequest, user: str = Depends(require_kb_editor)):
    return _save_item(config.kb_dir(slug), item_id, req, acting_user=user)


@app.put("/api/knowledge-bases/{slug}/items/{item_id}/header")
def kb_save_header(slug: str, item_id: str, req: HeaderUpdateRequest, user: str = Depends(require_kb_editor)):
    return _save_header(config.kb_dir(slug), item_id, req, acting_user=user)


@app.put("/api/knowledge-bases/{slug}/items/{item_id}/owner")
def kb_transfer_owner(slug: str, item_id: str, req: TransferOwnerRequest, user: str = Depends(require_kb_editor)):
    return _transfer_owner(config.kb_dir(slug), item_id, req, acting_user=user)


@app.put("/api/knowledge-bases/{slug}/items/{item_id}/filename")
def kb_rename_item(slug: str, item_id: str, req: RenameRequest, user: str = Depends(require_kb_editor)):
    return _rename_item_file(config.kb_dir(slug), item_id, req, acting_user=user)


@app.delete("/api/knowledge-bases/{slug}/items/{item_id}")
def kb_delete_item(slug: str, item_id: str, user: str = Depends(require_kb_editor)):
    return _delete_item(config.kb_dir(slug), item_id, acting_user=user)


@app.get("/api/knowledge-bases/{slug}/items/{item_id}/history")
def kb_item_history(slug: str, item_id: str, user: str = Depends(require_kb_access)):
    return _item_history(config.kb_dir(slug), item_id)


@app.post("/api/knowledge-bases/{slug}/items/{item_id}/history/{sha}/restore")
def kb_item_restore(slug: str, item_id: str, sha: str, user: str = Depends(require_kb_editor)):
    return _restore_item(config.kb_dir(slug), item_id, sha, acting_user=user)


@app.post("/api/knowledge-bases/{slug}/items/{item_id}/presence")
def kb_item_presence(slug: str, item_id: str, user: str = Depends(require_kb_access)):
    return {"users": events.touch_presence(f"kb:{slug}", item_id, user)}


@app.get("/api/knowledge-bases/{slug}/events")
async def kb_events(slug: str, request: Request, user: str = Depends(require_kb_access)):
    return StreamingResponse(_sse_stream(request, f"kb:{slug}"), media_type="text/event-stream")


app.mount("/", StaticFiles(directory=config.APP_DIR / "static", html=True), name="static")
