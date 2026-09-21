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
      project.yml       # members, statuses, status_colors — see Projects
      tasks/
      knowledge/
      # an *encrypted* project (see Encryption) has vault.enc here instead
      # of tasks/knowledge/ — those only exist in a live, unlocked copy
  knowledge-bases/
    <kb-slug>/           # standalone, not tied to any project — see Standalone knowledge bases
  users/
    <username>.yml       # optional profile: full_name, photo, email — see Login
  templates/
    task/, knowledge/    # editable starter files — see Templates
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
code — lives under `workspace/templates/` (`config.TEMPLATES_DIR`,
`GET/PUT /api/templates/{type}/{name}`), not the app's own source tree,
seeded once from a shipped starter set the first time each is missing
(`config.ensure_default_templates`, idempotent — never overwrites one
someone already edited or added):

```
workspace/templates/
  task/
    research_question.md
  knowledge/
    experiment.md
    lit_review.md
    report.md
    paper_draft.md
```

Creation flow: **Create → Task → Research Question** copies the content of
`workspace/templates/task/research_question.md` (the structured sections)
into the new file's body — it's just a starting point. No record of which
template was used is kept on the created file (no `template:` field, no
trace at all) — once created, it's a task/knowledge item like any other.
Tasks and knowledge items can also be created **without** a template, with
a free-form body.

**Editable from the app itself**, not just by hand on disk: the "New item"
dialog's template picker has a pencil icon (edit the selected template) and
a "+" (create a new one for the current type), both opening the same
plain-textarea editor dialog over `GET`/`PUT /api/templates/{type}/{name}`.
Global, not scoped to a project/kb — same trust level the rest of this app
already gives any logged-in member (there's no site-admin concept anywhere
else to gate it behind instead) — and, being under `workspace/`, every edit
is a real git commit like everything else here.

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
- **`oidc`**: real OAuth2/OIDC against *any* discovery-URL-based provider
  (via `authlib`) — generalized from an earlier Google-specific `google`
  mode (see the README's "Real login (OIDC)"; in `agorae`'s own deployment
  the provider is `metroon`, not Google directly). `/auth/oidc/login` and
  `/auth/oidc/callback` do the actual redirect/token exchange; the
  verified email is mapped to an existing username via that user's
  `email:` profile field (`config.find_username_by_email`) — but unlike
  this doc's earlier description, a verified email with **no** matching
  username is no longer refused: `config.provision_user_from_email`
  auto-provisions a bare account for it (username derived from the
  email's local part), same as archeion/stoa already do in `agorae`'s
  deployment. That account starts in zero projects/note bases — being
  authenticated is not, by itself, membership in anything; adding someone
  as a member ahead of time (with an `email` alongside their `username`)
  is what lands them inside a specific project/note base on first login
  instead of starting with none. Login only ever *authenticates* an
  identity; membership (`members:` in `project.yml`/`kb.yml`) is still the
  only thing that grants access to anything, exactly as in `dev` mode.
  `/api/login` itself is disabled (404) in this mode, so the dev path
  can't be used to bypass it.

Either way, the server remembers you via a signed session cookie
(Starlette's `SessionMiddleware`) for the rest of the requests —
`owner`/`as_user` are no longer sent by the client at all, every route
reads the acting user off the session. `/api/logout` clears it.

**Profile**: a username is still the only identity that matters for
permissions/membership — full name, photo, and (for `oidc` mode) email
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
(`""` = root). Creating a note while browsing a folder places it there.
Moving one is drag-and-drop, same owner-only rule as everything else
note-specific (`PUT .../items/{id}/folder`, `makeNoteDraggable` — a note
that isn't yours just isn't draggable) — dropping on a folder tile, a
breadcrumb segment, or the "back" button all call the same endpoint.

**Deleting a folder** (`DELETE .../folders/{path}`) removes it and
everything inside it — notes and any nested subfolders — but only when
the acting user owns *every* note in that subtree; one note that isn't
theirs blocks the whole delete (all-or-nothing, checked up front, not a
partial delete that orphans someone else's note). A note can never be
another item's `parent` (see _check_parent_is_task under Task), so unlike
deleting a single item, there's no "still referenced elsewhere" case to
guard here.

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

### Folder settings: owner and write access

A folder can carry its own metadata, `folder.yml` next to its `.gitkeep`
(`config.read_folder_config`/`write_folder_config`) — written the moment
it's created (`owner:` = creator) and editable afterward from the gear icon
next to a folder's delete button:

```yaml
owner: mrai
type: normal          # normal | submodule — see Submodule folders, below
users: []              # who besides owner can write here; [] = any editor
```

**Reading stays exactly as open as before** — anyone with project/KB
access sees everything in every folder, restricted or not; `users:` only
gates *writing into* that specific folder (creating a note there,
uploading an attachment, creating a subfolder, moving a note in —
`permissions.check_folder_write`, checked in addition to, not instead of,
the ordinary editor-or-above gate every mutating route already has). Same
permissive-when-unset pattern as `assigned_to` on a knowledge item: an
empty `users:` (or no `folder.yml` at all — every folder that predates
this feature) means "any editor," narrowing only once the owner names
specific people. A project/KB `admin` always passes this check regardless
of `users:`, same "admin implies everything editor can, and more" rule
used everywhere else. Only the folder's own `owner` (or an admin) can
change `users:` (`PUT .../folders/{path}/settings`) — this doesn't change
`_delete_folder`'s existing rule (still "owns every note inside," see
above), which is orthogonal to write-access-going-forward.

### Attachments: PDF and images

A folder can also hold plain uploaded files — PDF and common image types
(`.pdf .png .jpg .jpeg .gif .webp .svg`, `config.ALLOWED_ATTACHMENT_EXTENSIONS`,
capped at `PRAXIS_MAX_UPLOAD_MB`, default 25) — read-only from this app's
point of view: nothing here ever parses or edits one, only stores and
serves it (`GET .../attachments/{filename}?folder=...`, inline
`Content-Disposition` so the browser's own PDF/image viewer renders it).
They sit directly inside the folder they were uploaded to, alongside its
notes and subfolders — not a separate area. Since a plain file has nowhere
to keep an `owner` of its own, each folder gets a small lazily-created
sidecar, `.attachments.yml` (filename → `{owner, uploaded}`,
`config.read_attachments_index`) — kept separate from `folder.yml` because
it has to exist even where a folder has no `folder.yml` yet (including the
notes root, which nobody ever explicitly "creates"). Uploading is gated by
the same `check_folder_write` as creating a note there; deleting is
owner-only, mirroring a note's own delete rule.

**Referencing/embedding is just markdown** — no new syntax: an
attachment's URL is a normal URL, so `![caption](url)` already renders a
real `<img>` via `marked.parse` (see Editor, above), and `[name](url)`
works the same way for a PDF. The editor's **Insert attachment** button
(next to **Attach note**) lists the open item's own folder's attachments
and inserts the right one at the cursor, so nobody has to type a URL by
hand. Clicking either — the embedded image, the PDF link, or the row in
the folder browser itself — opens the same in-app viewer (a `<dialog>`
holding an `<img>` or an `<iframe>`) instead of navigating away or
downloading; this is the browser's own built-in image/PDF renderer, just
inside a modal, not a library this app ships.

### Submodule folders

A folder can be a **git submodule** instead of a plain directory of
`.md` files — for content that doesn't fit markdown well (code, a LaTeX
paper's full build tree, a dataset) but should still live right there in
the project/KB's own folder tree, with a real, independently clonable git
history. Where the rest of this app's version history is internal and
never exposed (`workspace/.git`, see Version history above), a submodule
folder's content is a genuine separate repo, hosted on the same Forgejo
(archeion) instance the read-only project mirror already uses — but a
different org (`PRAXIS_SUBMODULES_ORG`, distinct from the mirror's
`PRAXIS_ARCHEION_ORG`), since here people actually push, unlike a mirror.

**Converting a folder** (the gear icon's "Special folder" toggle,
`POST .../folders/{path}/submodule`, `app/submodules.py` +
`git_store.add_submodule`) requires owning every note already in that
folder's subtree — same rule, same reasoning as deleting a folder (an
all-or-nothing action over content that might include someone else's
work). Any existing content is pushed as the new repo's first commit
before the folder is replaced with the actual submodule (gitlink +
`.gitmodules`), so nothing is lost — it just now lives in the submodule's
own history instead of the workspace's. A submodule folder's `owner`/
`remote` live in a separate per-scope registry, `.submodules.yml` at the
notes root (`config.read_submodules_registry`) — never inside the folder
itself, since once a path is a real gitlink, the parent repo can no longer
track a plain file alongside it there.

**No per-person write list, on purpose**: unlike a normal folder's
`users:`, anyone with editor access to the project/KB can push — the
point is not complicating a student's own git workflow with a second
permission model. This is enforced on the Forgejo side instead: every
member becomes a repo collaborator, **read** regardless of role (matching
this app's own "reading is always open" rule) and **write** for
editor/admin (`submodules.sync_collaborators`, resynced whenever
membership changes — the same trigger points that already resync the
archeion mirror's own collaborators).

**History can't be force-pushed or deleted** — the repo's default branch
gets a Forgejo branch protection rule the moment it's created, before
anyone can push to it at all. Confirmed live against the deployed Forgejo
(v9): merely creating that rule is enough on its own — no separate "allow
force push" field exists in this version's API at all — to make Forgejo
reject a force-push ("branch main is protected from force push") and a
delete of the default branch. "Deleting" a submodule
folder from praxis.md (the same delete button every folder has) only ever
**unlinks** it — `git_store.remove_submodule` deinits and removes the
local gitlink/`.gitmodules` entry, but there is no code path anywhere in
this app that can delete the Forgejo repo itself. The repo — and its full
history — survives, recoverable by relinking the same URL or by whoever
actually administers that Forgejo instance. This is the strongest form of
the "can't lose history" guarantee: not a permission check that could be
bypassed or forgotten, but the simple absence of any delete-repo call in
the codebase.

**Not supported inside an encrypted project** — a submodule would need to
reach Forgejo on every unlock (its checkout lives inside the vault's
scratch copy, wiped on every reseal), and keeping a live external git
remote for content whose whole point is staying off any second copy
undercuts encryption's own threat model (see Encryption, below). Blocked
outright rather than allowed to half-work.

**A submodule's own contents are invisible to this app's index and
folder tree** — `storage.all_files` and `_list_folders` both stop at any
directory containing a `.git` entry (a submodule's checkout always has
one), so a student's own `README.md` or nested directories inside their
submodule never get scanned as if they were praxis.md items. Browsing into
a submodule folder shows its clone URL and instructions, not a file
browser — this app doesn't reimplement a git hosting UI.

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

**Color** is a separate, optional per-status override (`status_colors:` in
`project.yml`, `config.status_color`/`set_status_color`) — a status with
no override falls back to a curated pastel default (cycled by its position
in `statuses:`, reusing this app's own primary/secondary/tertiary container
tones where a hue already exists — see `config.DEFAULT_STATUS_COLOR_PALETTE`
and `static/style.css`'s Material 3 palette). Shown as the Kanban column's
header background and as the status badge on every card; changing it is a
small `<input type="color">` next to each column header, admin-only
(`PUT .../statuses/{status}/color`, same gate as renaming the project).

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

## Now list and status requests (SQLite, *not* a cache — `app/state_db.py`)

The one deliberate exception to "nothing may exist only in the database":
per-person **interface state**, never task state. Kept in its own file
(`PRAXIS_STATE_DB`, default `state.db` next to the workspace directory; a
dedicated mount in the agorae deployment), outside the workspace git
repository and outside the archeion mirrors, so it stays private.

- `now_items(username, project, task_id, position, started_at)` — the
  private "Now" focus list on Home. Just a pointer; putting a
  task there changes nothing about the task. Any task the person can see may
  be added. UI warns above 3 items, the server refuses above 20.
- `status_requests(id, project, task_id, requester, from_status, to_status,
  note, state, decision_note, created_at)` — a person assigned to a task (not
  its owner) asks the owner to change its status. Only the owner can accept
  (which applies the status through the normal header path, so the header is
  still only ever written by its owner) or reject; only the requester can
  cancel or dismiss. Visible to those two people and nobody else, admins
  included; the owner is read off the task each time, so a transferred task
  takes its requests with it. One live request per requester and task.

Rows that stop making sense are deleted when read: the task is gone, the
target status was removed or already reached, the requester is no longer
assigned. Editing a project's statuses renames or drops pending requests to
match. Encrypted projects are excluded entirely (`drop_project` on encrypt),
since nothing about their tasks may live outside the vault.

Losing this file loses Now lists and pending requests — never a task.

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
`can_rename`, `can_transfer_owner`, `can_move`, `can_delete` (the last
gated on scope-edit access plus being the item's owner, same as rename/
transfer) — computed the same way the mutating routes themselves decide
whether to 403/409, not re-derived on the client. The frontend hides or
disables a button from this rather than guessing from role + owner
comparisons itself, so a button can never promise something the API would
then refuse.

**Audit / restore.** `GET .../items/{id}/history` lists every commit that
touched that item (sha, author, date, message — from `git_store.file_history`,
newest first); `POST .../items/{id}/history/{sha}/restore` restores its
body to what it was at that commit. Restoring is implemented as an
ordinary save (old body, current version as `base_version`) rather than
rewriting history, so it goes through the exact same permission check and
concurrent-edit safety net as any other save — someone else's edit made
after the version being restored from still merges or conflicts normally
instead of being silently discarded.

## Archeion mirroring (`app/archeion_mirror.py`)

A separate, optional record of every **unencrypted** project's current
content, kept in a Forgejo repo — distinct from the git history described
above (`workspace/.git`, which stays wherever the app runs and is never
pushed anywhere). Every save/create/rename/transfer/restore/delete/move
that touches an unencrypted project re-syncs its own dedicated mirror
repo: copies its current `tasks/`/`knowledge/` into a separate local clone
and commits+pushes that, plus keeps the mirror repo's collaborator list
(read-only) in sync with the project's own `members:`. An encrypted
project (see Encryption above) is never mirrored — `has_vault(slug)` is
checked first and skips entirely, since the point of encrypting a project
is keeping it off any second copy. Configured entirely by environment
variables (`PRAXIS_ARCHEION_*`, see `.env.example`) — unset, this quietly
no-ops, same as the OIDC/metroon integration above.

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
  identity verification exists (`PRAXIS_AUTH_MODE=oidc`, see the README),
  but is opt-in, not the default.
