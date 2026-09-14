# Schema (draft)

Two item types: **task** and **knowledge**. No unified type — task has
workflow fields (status, due date, assignment) that knowledge doesn't.
The UI calls a knowledge item a **Note** (and a knowledge base a **Note
Base**) — purely a display label, changed because it reads more naturally;
`type: knowledge`, the `knowledge/` folder, and the `/api/knowledge-bases`
routes are unchanged, to avoid migrating every existing file's frontmatter
for a renaming with no functional effect.

What links one file to another is the frontmatter (`parent:`, shown in the
UI as **Main task** on a task or **Related task** on a note — same field,
label depends on which side is pointing, see Task/Knowledge below), not
physical location — except for notes specifically, which *are* organized
into real folders on disk (see Notes: folders, under Editor).

## Workspace layout

```
workspace/
  .git/                  # every save is a commit — see Version history (git)
  .praxis-index.db       # SQLite cache, git-ignored, rebuilt from the files at startup — see Index
  projects/
    <project-slug>/
      project.yml       # members, statuses — see Projects
      tasks/
      knowledge/
      # an *encrypted* project (see Encryption) has vault.enc here instead
      # of tasks/knowledge/ — those only exist in a live, unlocked copy
  knowledge-bases/
    <kb-slug>/           # standalone, not tied to any project — see Standalone knowledge bases
```

## Projects

A project owns **tasks** and **project-scoped knowledge**. Access is
restricted to the project's members.

Created via the **+** next to "Projects" in the sidebar (`POST /api/projects`,
just a name) — unlike a project with no `members:` configured (open to
anyone, see below), a *newly created* one starts with its creator as its
sole admin, so making one doesn't accidentally hand it to everyone; more
members get added from its Settings panel afterward. The slug is derived
from the name (lowercased, non-alphanumeric runs collapsed to `-`), with a
numeric suffix if that slug's already taken.

`project.yml`:

```yaml
members:                  # username -> role (admin | editor | guest)
  mrai: admin
  student1: editor
  advisor: guest
statuses:                 # optional — falls back to the default list, see Status
  - proposal
  - backlog
  - ready
  - in_progress
  - review
  - done
name: Coral Bleaching Forecast   # optional — editable display name, falls back to the slug
icon: water_drop                # optional — a Material Symbols name, falls back to "folder"
```

Membership check: only usernames listed in `members:` can see or edit
anything inside that project (tasks or knowledge). Enforced at the API
layer via the acting user sent on every request — see Permissions for the
important caveat about how weak that enforcement is without real auth.

Reachable through the project's own **Settings** entry in the nav (next to
**Tasks**/**Notes** — this used to be a separate **Members** entry; folded
into Settings alongside name/icon editing so a project and a note base
share one consistent pattern, see below). Members management itself is
unchanged — only where it lives moved.

**Roles**: each member has exactly one role.

- `admin` — everything `editor` can do, plus managing the member list
  (add/remove members, change anyone's role, including other admins). A
  project can have more than one admin.
- `editor` — can create and edit tasks/knowledge, same as before roles
  existed; still subject to the `owner`/`assigned_to` rules below.
- `guest` — **read-only**: can see everything in the project but every
  write (create, save, transfer ownership) is rejected server-side,
  regardless of `owner`/`assigned_to`. Meant for people who should see
  progress (an advisor, a visiting collaborator) without being able to
  change anything.

Same permissive-when-unset pattern as `members:` itself: an empty or
missing `members:` map means the project is open to anyone, and everyone
acts as `admin` in that case — a project is never locked out of getting
its first real admin set, and `admin` already implies full edit rights so
this also preserves the old "open project, anyone can edit" behavior.
Once real members are listed, roles narrow to exactly what's assigned.

## Standalone knowledge bases (Note bases)

Knowledge doesn't have to live inside a project — a knowledge base can
exist on its own (e.g. lab-wide reference material not tied to a specific
project); the UI calls this a **Note base** (see the intro note on the
Notes/Note base display-label decision). It now has its own `kb.yml`
(next to a project's `project.yml`, same shape: a `members:` map of
username → role, plus a `name:` — see below) and the exact same
permissive-when-unset roles model a project uses (§ Roles above): open to
anyone (acting as `admin`) until real members are listed, at which point
it narrows to exactly that list. This used to be a hardcoded "always open,
no config" policy; now that's just the *default state* of a real one.
Created the same way a project is (the **+** next to "Note Bases",
`POST /api/knowledge-bases`) — see Projects above for the slug and
creator-as-first-admin details, identical here.
Reachable through its own **Settings** entry in the nav (next to
**Notes**), which holds both the member list and:

- **Name** and **Icon** — an editable display name and a Material Symbols
  icon name (`kb.yml`'s `name:`/`icon:`, same two fields a project now has
  in `project.yml` — see Projects above), independent of the slug/
  directory name, which never changes once a note base exists (so existing
  `parent:`/`[[reference]]` links and API URLs are never affected by a
  rename — same reasoning as renaming a note's file, see below). Both fall
  back to something reasonable (the slug itself; "menu_book" for a note
  base, "folder" for a project) until someone sets their own. The icon is
  picked from a fixed list client-side (`SCOPE_ICONS`, app.js) rather than
  typed freely, so it can't end up pointing at a nonexistent glyph.

Adding the first member to a previously-open project *or* note base is
guarded against locking out whoever does it: if they name someone else
without also including themselves, the server adds them back as `admin`
automatically (`add_project_member`/`add_kb_member`, main.py) — otherwise
enabling membership on an open project you administer could accidentally
lock you out of your own project in one click.

## Task

```yaml
---
id: task-2026-014           # auto-generated by the app, never typed by hand
type: task                    # task | knowledge — explicit discriminator, not inferred from the folder
status: proposal              # comes from the project's config, see Status
parent: task-2026-003          # "Main task" in the UI — must be a task, never a knowledge item (optional)
owner: mrai                     # sole editor of the header — see Permissions (username)
assigned_to: []                 # usernames allowed to edit the body
due_date: null                  # optional
tags: []
created: 2026-09-05
updated: 2026-09-05
---
```

IDs are scoped **per project / per knowledge base**, not globally across
the workspace — consistent with each project being an isolated access
boundary; a task should never end up linking (via `parent`) into a
different project's content.

**Subtasks** and **Related notes** are both just the inverse of `parent`,
computed on the fly (no separate list to keep in sync) — see Editor: other
tasks pointing here are Subtasks, notes pointing here are Related notes.
Because `parent` is always a task, only a task can ever be pointed at;
a knowledge item's `parent` still points at a task, but nothing can point
back at *it*.

## Knowledge

```yaml
---
id: knowledge-2026-009
type: knowledge
parent: task-2026-003           # "Related task" in the UI (same field as Task's "Main task" above,
                                 #  just a different label — see Editor) — must be a task
owner: mrai
assigned_to: []                 # "Users" in the UI — see below; empty means "anyone with access"
tags: []
created: 2026-09-05
updated: 2026-09-05
---
```

No `status`, no `due_date` — knowledge isn't a unit of work with its own
flow, it's content. `assigned_to` is the same field a task uses (shown as
**Users** instead of **Assigned to** in the header panel — same chip UI,
same picker, populated from the current project's or note base's own
members either way), but its empty-list meaning is the opposite of a
task's: on a task, empty has always meant "nobody but the owner"; on
knowledge, empty is this app's original default — **anyone with access to
the project/knowledge base can edit the body** — which only narrows once
an owner opts in by naming specific users (`check_body_save`,
permissions.py). Only the `owner` locks the header either way.

**Renaming the file** (owner-only, `PUT .../items/{id}/filename`) is
supported for knowledge — the filename is purely for browsing the
workspace directly (git, a text editor); it's never how an item is looked
up. `find_path_by_id` scans for the `id:` field inside each file, so a
rename can't break a `parent:` link or a `[[reference]]`, both of which
point at the id, not the path.

## Templates

A template is a **pre-organized file**, not an enum value baked into the
code — lives under something like:

```
templates/
  task/
    research_question.md
  knowledge/
    experiment.md
    lit_review.md
    report.md
    paper_draft.md
```

Creation flow: **Create → Task → Research Question** copies the content of
`templates/task/research_question.md` (the structured sections) into the
new file's body — it's just a starting point. No record of which template
was used is kept on the created file (no `template:` field, no trace at
all) — once created, it's a task/knowledge item like any other. What's
inside a template is defined by the file itself under `templates/`,
editable by anyone, not something fixed in the tool. Tasks and knowledge
items can also be created **without** a template, with a free-form body.

### `templates/task/research_question.md`

```markdown
# {Research question in one sentence}

## Summary

## Preconditions

## Gap Context

## Validation Methodology

## Check-out Metric
```

### `templates/knowledge/{experiment,lit_review,report,paper_draft}.md`

Each a lightweight section skeleton, same spirit as the task template
above (a heading list to fill in, no prose): `experiment` (Objective,
Setup, Procedure, Results, Analysis, Next steps), `lit_review` (Citation,
Summary, Key claims, Methodology, Relevance to our work, Open questions),
`report` (Summary, Context, Findings, Caveats and limitations,
Recommendations), `paper_draft` (Abstract, Introduction, Related work,
Methods, Results, Discussion, Conclusion — a markdown draft even though
the final version will most likely ship as LaTeX).

## Permissions

- **`owner`**: sole editor of the **header** (the entire frontmatter —
  `status`, `due_date`, `parent`, `tags`, `assigned_to`, `owner` itself) of
  any task or knowledge item.
- **`assigned_to`**: can edit the **body** — on a task this is who besides
  the owner is responsible for it ("Assigned to"); on knowledge it's who
  besides the owner is allowed to edit it ("Users"), and unlike a task, an
  *empty* list here means "anyone with project/KB access", not "nobody" —
  see Knowledge above.
- **Transferring ownership**: a dedicated action (not a header edit) —
  `PUT .../items/{id}/owner`, only the *current* owner can call it.
- **Project role gates all of the above**: a `guest` (see Projects) can
  never write, full stop — checked before `owner`/`assigned_to` are even
  looked at, so a guest who happens to be listed as `owner` or
  `assigned_to` on some item still can't edit it. `admin`/`editor` don't
  change the `owner`/`assigned_to` rules at all; they just mean "not a
  guest."

**Enforcement**: checked server-side on every save. Header and body are
now two entirely separate write paths (see Editor) instead of one endpoint
accepting a whole raw file — `PUT .../items/{id}` only ever carries body
text, `PUT .../items/{id}/header` only ever carries header fields and is
owner-only. That means there's no "header conflict" case to detect
anymore: a body-save request has no header in it at all, so it can't
disagree with what's stored, and a header-save request is rejected outright
(403) if you're not the owner — no comparing two frontmatter blobs to
guess what changed, no silent partial apply.

**Limitation that still stands**: this protection lives in the application
layer. Editing the `.md` directly outside the app (another text editor,
file sync, git) doesn't go through this check — not something we're
planning to solve now.

**Project membership is enforced the same way** — at the API layer, based
on the logged-in session user (see Login below). There's still no
password — logging in is picking a username from a dropdown of known
users — so this isn't a real security boundary yet (anyone can log in as
anyone), but it's no longer a spoofable request header either: the server
tracks who's logged in via a signed session cookie, and every permission
check (owner, assigned_to, project membership) reads from that, not from
anything the client asserts per-request.

## Login

Two modes, picked with `PRAXIS_AUTH_MODE` (`app/main.py`):

- **`dev`** (the default, unchanged from how this started): logging in is
  choosing a username from a dropdown (`GET /api/users`, built from every
  project's and note base's `members:` list) and posting it to
  `/api/login`, no password. **Session management, not authentication** —
  there's nothing verifying the person picking "mrai" from the dropdown is
  actually mrai. Meant for local development/testing only.
- **`google`**: real OAuth2 against Google (via `authlib`), the real auth
  this doc used to describe as a later step. `/auth/google/login` and
  `/auth/google/callback` do the actual redirect/token exchange (an
  optional `GOOGLE_HOSTED_DOMAIN` narrows Google's account picker to one
  Workspace domain); the verified email is mapped to an existing username
  via that user's `email:` profile field (`config.find_username_by_email`)
  — a Google identity with no matching email is refused, not
  auto-registered. Login only ever *authenticates* an identity; membership
  (`members:` in `project.yml`/`kb.yml`) is still the only thing that
  grants access to anything, exactly as in `dev` mode. `/api/login` itself
  is disabled (404) in this mode, so the dev path can't be used to bypass it.

Either way, the server remembers you via a signed session cookie
(Starlette's `SessionMiddleware`) for the rest of the requests —
`owner`/`as_user` are no longer sent by the client at all, every route
reads the acting user off the session. `/api/logout` clears it.

**Profile**: a username is still the only identity that matters for
permissions/membership — full name, photo, and (for `google` mode) email
are self-edited (`PUT /api/users/me/profile`; email itself is set by
hand-editing the profile file, not through that form — see
`config.find_username_by_email`'s docstring) and there's no way to edit
anyone else's. Stored one file per user, `workspace/users/<username>.yml`
(`full_name:`, `photo:`, `email:`) — a photo is a data: URI kept small
client-side (capped at 2MB in the upload form) rather than a
separately-stored image file, so there's no upload directory to manage;
still just a file, still rebuildable/inspectable like everything else. A
user with no profile file just shows their bare username and an initials
avatar, same as before this existed.

## Editor

The header is never edited as raw YAML in the main UI — the on-disk format
is unchanged (still plain `---\n<yaml>\n---\n<body>`, still parsed with
`python-frontmatter`), but the **client** now works with header and body as
two separate things, matching how the server already separated them:

- **Read mode**: renders the body, and shows a structured header panel as a
  vertical property list (owner, status, due date, assigned_to, tags,
  parent, subtasks) — not a horizontal row of independent boxes, and not a
  textarea of YAML. Editable only by the owner (fields are `disabled`
  otherwise); each field commits immediately on change via
  `PUT .../items/{id}/header`, a full replace of those fields (the form
  always submits everything it currently shows, never a partial patch —
  the same pattern used for a project member's role). `status`/`due_date`
  fields are hidden entirely for knowledge (they don't exist on that type);
  `assigned_to` is shown for both, labeled **Assigned to** on a task and
  **Users** on a note (see Knowledge above for why the label, not just the
  field, differs).
  - **Tags** and **assigned_to** are both pick-from-a-list, never free
    text: chips with a remove button, plus a control to add — an
    autocompleting input (suggesting every tag already used in the
    project/KB, via `GET .../tags`, but still free to create a new one) for
    tags, and a `<select>` of the remaining project/KB members for
    assigned_to. Neither uses a native multi-select — picking more than one
    person from one of those requires ctrl/cmd-click, which reads as "only
    one at a time" if you don't already know that.
  - **Main task / Related task** (the `parent` field, same field and same
    `<select>` either way) is populated from every *task* in the current
    project (shown by title, not a bare id to remember) — knowledge items
    never appear here, on a task or a note: the target is always a task.
    The label changes with what's pointing, not what's pointed at: a task
    pointing at a task is a hierarchy, so it's labeled **Main task**; a
    note pointing at a task is just an association, not a subtask
    relationship, so it's labeled **Related task** instead — the same
    field, picker, and server-side rule, purely a UI label swap based on
    the current item's own `type`. Plus an "Open →" button that navigates
    there — navigating is not an edit, so that button ignores the
    owner-only lock and works for anyone. Enforced server-side too, not
    just by what the picker offers (`_check_parent_is_task`, used by both
    create and header-save) — you can't set something else as a main/
    related task even by calling the API directly.
  - **Subtasks** (tasks only) and **Related notes** (tasks only) are both
    just the inverse of `parent`, split by the type of whatever's pointing
    back: other *tasks* whose `parent` is this one are Subtasks; *notes*
    whose `parent` is this one are Related notes. Both computed
    client-side from the already-loaded item list (no extra request) and
    listed as clickable links. **+ Add subtask** opens the create dialog
    with `parent` preset to the current task's id; there's no equivalent
    "+ Add related note" button, since that relationship is created from
    the note's own side (set its Related task), not from the task's. A
    knowledge item can never have either section (nothing can point a
    main/related task at a note), so both are hidden for those.
- **Edit mode**: the header panel is hidden entirely — the editor
  (source + rendered preview) only ever shows and saves the **body**, via
  `PUT .../items/{id}` (also a full replace of the body text, autosaved —
  see below). There's no way to touch the header from here, by
  construction, not just by convention.
- **Attaching/citing a note**: `[[item-id]]` anywhere in the body renders
  as a clickable link to that item in the preview (an unknown id renders
  as a dashed, non-clickable "broken reference" instead of a dead link).
  The **📎 Attach note** button opens a picker searchable by title or id —
  **knowledge items only** — and inserts `[[id]]` at the cursor, so citing
  a note never requires memorizing or typing its id. Citing another
  *task* this way isn't offered by the picker on purpose: task-to-task
  relationships go through Main task/Subtasks instead, so there's exactly
  one way to express "this task belongs under that one."
- Switching to a different item while the editor is still open (a main
  task, a subtask, an attached note) flushes any pending body autosave
  first — otherwise the edit-in-progress on the item you're leaving would
  be silently dropped instead of saved.

This was a deliberate reversal of an earlier decision to require raw
frontmatter editing as a first-class feature — doing it this way instead
made the owner-only header rule trivial to enforce (a dedicated,
owner-gated endpoint that only ever accepts header fields) instead of
having to diff two full frontmatter blobs on every body save. The
underlying files stay exactly as capable of being hand-edited outside the
app (git, another editor) as before — this only changes what the app's own
editor exposes.

**Autosave**: body edits save automatically ~1.5s after the last
keystroke (capped at 8s of continuous typing), and flush immediately if
the editor is closed before that.

**Math**: the render pane supports math notation (`$...$` inline,
`$$...$$` block) via KaTeX.

### Notes: folders and two views

Unlike tasks, notes can be organized into real folders — plain directories
on disk under a project's/note base's `knowledge/`, created and browsed
through the app (`GET`/`POST .../folders`, `_list_folders`/`_create_folder`
in main.py). `storage.all_files` already scans recursively, so nesting a
note under a folder needs no change there; the folder a note lives in is
just its path relative to `knowledge/`, computed server-side per item
(`""` = root). Creating a note while browsing a folder places it there;
there's no drag-to-move between folders yet (a natural follow-up, not
built this round).

Two ways to browse them, Drive-style:

- **List**: one row per folder (first) then note, columns Name / Owner /
  Modified (`updated:`, not filesystem mtime — mtime isn't preserved
  reliably across git clones/copies, `updated:` already is the thing every
  save bumps).
- **Grid**: square tiles, folders first, each note tile showing a rough
  plain-text excerpt of its body (`storage.excerpt_from_body` — strips the
  title line and the most common markdown markup, not a real renderer;
  "more or less" was the explicit bar, not fidelity).

A breadcrumb (**All notes / folder / subfolder…**) tracks the current
folder and navigates back up; both views share the same folder state.

## Task views

Beyond the flat list, tasks support a **Kanban view**: one column per
status (from the project's `statuses:` list), cards are tasks. Same
underlying data — just a different way to look at it, no schema impact.

Both views (List, grouped by status; Kanban) support **drag-and-drop**
between statuses — same underlying action either way (a header save of
just the `status` field), so the same owner-only rule applies in both. A
rejected drag shows an inline toast rather than a blocking `alert()`.

## Status

Valid values are a **project's choice** (configurable, not fixed in the
tool). Implemented in `project.yml` at the project root (`statuses:` key).
Default if the file doesn't exist or doesn't have that key:

```
proposal → backlog → ready → in_progress → review → done
```

## Tags

To avoid near-duplicate tag proliferation ("ml" vs "ML" vs
"machine-learning"), the editor never lets tags be typed as free text onto
an item — the header panel shows them as removable chips, and adding one is
pick-an-existing-tag-or-create-a-new-one (an `<input>` with a native
`<datalist>` suggesting every tag already used in that project/knowledge
base, via `GET .../tags`). Nothing enforces a closed vocabulary — typing a
name that doesn't match a suggestion just creates it — the goal is nudging
towards reuse, not restricting what's possible. `GET .../tags` is computed
by scanning the files each time (same as everything else — see Index),
no separate registry to keep in sync; SQLite remains an option later if
that scan ever becomes slow enough to matter.

## Index (SQLite, a cache — `app/index.py`)

The `.md` files are the only source of truth; SQLite (`workspace/.praxis-index.db`,
git-ignored) is a cache on top, never a dependency. The rule that matters
isn't "no database", it's: **nothing may exist only in the database**.
`index.rebuild_all()` reconstructs every row by re-scanning the files, and
runs automatically on every app startup — a deleted or corrupted `.db` is
never a data-loss risk, only a rebuild.

One table (`scope_dir, id, path, data_json`) holds one JSON blob per item —
the same shape `_list_items` used to compute by scanning files on every
request (metadata + path + title + excerpt + folder). A write (`_save_item`,
`_save_header`, `_create_item`, a rename, transfer, or move) calls
`index.reindex_item()` right after writing the file, so the index is always
updated in the same request that changed the file, never lazily.

## Version history (git — `app/git_store.py`)

The workspace (`workspace/`, its own git repository, separate from the
app's own source repo) is versioned with GitPython: every save is a
commit, authored as the acting user (`username@praxis.local`, display name
from their profile if set). This is real, inspectable git history — `git
log`, `git show`, `git diff` all work against `workspace/` directly from a
terminal, independent of the app.

**Merging is delegated to git, not reimplemented.** A GET on an item
returns a `version` — the git blob sha of its body at the time it was
read. A save (`PUT .../items/{id}`) echoes that back as `base_version`. If
the file hasn't changed since (the common case — one person editing), the
save just writes and commits as before. If it *has* changed (someone else
saved in between), the two edits are merged with `git merge-file` — the
same line-based three-way merge git uses internally for a real merge
commit, just handed three blobs (base/ours/theirs) instead of branches:

- **No overlap** (the two edits touched different lines): merges cleanly,
  commits automatically as `Merge concurrent edits to <id>`, and the
  response carries the merged body so the saving client's editor is
  updated to match what's now on disk (`{"merged": true, "body": ...}`).
- **Real overlap** (both edited the same lines): nothing is written. The
  request gets a 409 with git's own `<<<<<<<`/`=======`/`>>>>>>>` markers
  in `detail.merged_body`, for the frontend to drop into the editor as-is
  — the user resolves it exactly like resolving a merge conflict on the
  command line, then saves again.

A file that predates git tracking (the one-time bootstrap of turning this
on for an already-populated workspace) gets a single `Initial import`
commit at startup (`git_store.ensure_baseline_commit()`) before anything
else runs — otherwise a file's *first* concurrent edit would have no
committed version to diff against, and would silently overwrite instead of
merging or conflicting.

All of this is serialized by one process-wide lock (`git_store.LOCK`): a
mutating request holds it for its entire read-check-merge-write-commit
sequence, and `commit_all()` stages the whole workspace (`git add -A`)
rather than a specific path — safe only because the lock guarantees no
other write is ever half-done when that happens.

Editing a `.md` file directly (a text editor, outside the app entirely) is
not prevented — it's just an uncommitted change until the next save
through the app sweeps it into that save's commit (`git add -A`), same as
any other uncommitted change in a normal git working tree.

The one exception to all of the above is an **encrypted** project (see
Encryption, next) — while unlocked it's a fully independent repo of its
own, not a subdirectory of `workspace/`'s, and everything in this section
still applies to it exactly, just scoped to that repo instead.

## Encryption (`app/vault.py`)

The threat model here isn't an external attacker — it's whoever
administers this server (or its git remote/backups) casually reading a
student's project just because it's sitting right there in the one place
they already have full access to for other reasons (see the conversation
that led here). So the unit of protection is a whole project, not a file
or a field, and the goal is removing *easy, incidental* access, not
defending against a determined, actively malicious operator.

**At rest**, an encrypted project is one opaque file,
`workspace/projects/<slug>/vault.enc` — a tar of that project's
`tasks/`/`knowledge/`/its own `.git` history, AES-256-GCM-encrypted with a
key derived (scrypt, interactive cost) from a random secret generated once
and shown to whoever encrypts the project — the server never stores the
secret, only a salt and a hash to check a re-entered one against
(`project.yml`'s `vault_salt:` — not sensitive, a salt's whole purpose is
to be public). No compression (`tarfile` mode `"w"`) — these are small
text files, and paying CPU for a few saved KB on every lock/unlock isn't
worth the latency. `project.yml` itself (name, icon, members, salt) is
never part of what's encrypted — the app needs it to route requests and
render the nav before anyone has unlocked anything.

**Unlocked**, a project is decrypted into an unpredictable, RAM-backed
scratch directory (`/dev/shm` by default — gone on a crash or reboot, so
nothing decrypted survives one by accident) shared by every currently
logged-in member, not per-session — the password unlocks the *project*,
matching its own membership granularity, not an individual's personal
access. `config.resolve_scope()` is the one seam that makes this
invisible to the rest of the app: every read/write resolves a scope
directory to wherever its content currently lives (the scratch copy, or
the plain workspace path for anything never encrypted) and to which repo
tracks it, raising `vault.ProjectLocked` (→ HTTP 423) if nobody's unlocked
it yet — `storage.py`/`index.py`/`git_store.py`/`permissions.py` never
know encryption exists at all. A project reseals itself automatically
after `PRAXIS_PROJECT_IDLE_TIMEOUT` (seconds, default 1800) of no
activity, or immediately via its Settings panel's "Lock now"; either way
that's `vault.lock_project()` re-tarring the scratch copy's current state
(new commits included) back into `vault.enc` and wiping the scratch copy.

**What this doesn't do**: it's not zero-knowledge — while unlocked, the
server process holds the real key in memory and could be made to leak it
by whoever can already run code as it, same as any server-held secret.
Rotating a project's password (re-encrypting under a new one, e.g. after
removing a member) only protects *future* saves — it doesn't rewrite git
history, so it can't retroactively protect anything already committed
under the old key, and a removed member who already had the old secret
could still have local copies from before removal. Neither is unique to
this design; they're inherent to any access-revocation scheme over
already-shared content.

A scope (a project or note base) has one live channel, pushed to every
connected client over Server-Sent Events — chosen over WebSockets because
the app only ever needs server→client push (every write already goes
through the ordinary REST endpoints); SSE gets that with a plain
`StreamingResponse`, no extra library, and the browser's `EventSource`
reconnects on its own. `GET /api/projects/{slug}/events` (and the note-base
equivalent) is that stream; `app/events.py`'s `EventBus` is an in-process
pub/sub with one queue per connected client — single-process only, fine at
this app's scale, and the one piece that would need a real broker (Redis
pub/sub or similar) if that ever changes.

Two kinds of event travel over it:

- **`item_changed`** — published whenever a mutating request actually
  commits (see Version history), carrying the item's `id` and the
  username that made the change (`by`). A client ignores its own `by`
  (it already knows what it just saved); for anyone else, it refreshes
  its item list, and if that item happens to be open, shows a banner
  ("Someone else just updated this item.", Reload / Dismiss) rather than
  silently overwriting an in-progress edit or reloading out from under
  someone mid-keystroke.
- **`presence`** — who currently has a specific item open. A client
  heartbeats `POST .../items/{id}/presence` every 8s while that item's
  editor is open (`app/events.py`'s `touch_presence`, `PRESENCE_TTL = 20`
  seconds); the response, and every subsequent `presence` event for that
  item, is the roster of everyone else currently there, shown as a small
  pill next to the item's title.

**Role-based responses.** `GET .../items/{id}` (`_get_item` in main.py)
returns a `permissions` object — `can_edit_body`, `can_edit_header`,
`can_rename`, `can_transfer_owner`, `can_move` — computed the same way the
mutating routes themselves decide whether to 403/409, not re-derived on
the client. The frontend hides or disables a button from this rather than
guessing from role + owner comparisons itself, so a button can never
promise something the API would then refuse. (There's no `can_delete`:
this app has no delete endpoint for an item at all yet, for any user.)

**Audit / restore.** `GET .../items/{id}/history` lists every commit that
touched that item (sha, author, date, message — from `git_store.file_history`,
newest first); `POST .../items/{id}/history/{sha}/restore` restores its
body to what it was at that commit. Restoring is implemented as an
ordinary save (old body, current version as `base_version`) rather than
rewriting history, so it goes through the exact same permission check and
concurrent-edit safety net as any other save — someone else's edit made
after the version being restored from still merges or conflicts normally
instead of being silently discarded.

## Open items

- No remote/push — version history is local to this machine's
  `workspace/.git`, there's no notion of syncing it anywhere else yet.
- Presence and the event bus are in-process, single-worker state
  (`app/events.py`) — running more than one server process/worker would
  need a real pub/sub backend, not just more `uvicorn` workers.
- History/restore covers the body only, not header fields (tags, status,
  assigned_to/parent) — those aren't versioned back-and-forth yet, only
  ever overwritten going forward.
- `PRAXIS_AUTH_MODE=dev` (the default) is still a no-password dropdown —
  everything above (owner/assigned_to/project membership) trusts whoever
  the session says is logged in, with nothing verifying that claim. Real
  identity verification exists (`PRAXIS_AUTH_MODE=google`, see the README),
  but is opt-in, not the default.
- No delete for a project, note base, task, or note — only creation,
  editing, and (for a task/note's body) restoring an earlier version.
