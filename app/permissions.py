"""Rules from docs/design/schema.md#permissions.

owner edits the entire header, through the dedicated header endpoint only —
never by slipping frontmatter changes into a body save. `assigned_to` is
the same field on both types (task or knowledge — "Assigned to" vs "Users"
in the UI, see main.py's _save_header) but its empty-list meaning differs:
on a task, empty has always meant "nobody but the owner"; on knowledge, an
empty list is the historical default this app started with — anyone with
access to the project/knowledge base edits the body — which only narrows
once an owner opts in by naming specific users, same permissive-when-unset
spirit as project/kb membership itself (config.py).

Since header and body are now separate write paths (see main.py's
/items/{id} vs /items/{id}/header), there's no header-conflict case to
detect here anymore — the body-save request has no header in it at all, so
it can't carry one that disagrees with what's stored.
"""

from dataclasses import dataclass


@dataclass
class SaveDecision:
    allowed: bool
    reason: str = ""


def check_body_save(existing_meta: dict, item_type: str, acting_user: str) -> SaveDecision:
    owner = existing_meta.get("owner")
    if acting_user == owner:
        return SaveDecision(True)

    assigned = existing_meta.get("assigned_to") or []
    if item_type == "task":
        if acting_user not in assigned:
            return SaveDecision(
                False,
                f"You're neither the owner nor in assigned_to for this task ({owner}/{assigned}).",
            )
    elif assigned and acting_user not in assigned:
        return SaveDecision(
            False,
            f"You're neither the owner nor one of the allowed users for this note ({owner}/{assigned}).",
        )

    return SaveDecision(True)


def check_header_save(existing_meta: dict, acting_user: str) -> SaveDecision:
    owner = existing_meta.get("owner")
    if acting_user != owner:
        return SaveDecision(False, f"Only the owner ({owner}) can edit the header.")
    return SaveDecision(True)


def check_folder_write(folder_meta: dict, project_role: str, acting_user: str) -> SaveDecision:
    """Gate for writing *into* a specific folder — creating a note there,
    uploading an attachment, creating a subfolder, moving a note in (see
    config.read_folder_config for where folder_meta comes from). This is on
    top of, not instead of, the ordinary scope-wide editor check every
    mutating route already enforces — a guest never reaches this at all.

    Same permissive-when-unset pattern as everything else in this app: a
    folder nobody has ever configured (folder_meta == {}) or an explicitly
    empty `users:` list both mean "any editor" — narrowing only once the
    folder's owner opts in by naming specific users. A submodule folder
    (see docs/design/schema.md) never has a `users:` list at all (write
    access there is everyone-with-editor-access, by design, enforced on the
    Forgejo side instead — see app/submodules.py), so it always falls into
    the same "any editor" branch as an unconfigured one.

    An admin can always write here — same "admin implies everything editor
    can do, and more" rule used everywhere else roles are checked."""
    if project_role == "admin":
        return SaveDecision(True)
    users = folder_meta.get("users") or []
    if not users:
        return SaveDecision(True)
    owner = folder_meta.get("owner")
    if acting_user == owner or acting_user in users:
        return SaveDecision(True)
    return SaveDecision(False, f"Only {owner} and {users} can write in this folder.")
