# praxis.md

Science-focused task and knowledge manager — markdown + YAML as the
organizing format, on the same premise as
[taskmd](https://github.com/taskmd/taskmd). Runs standalone (see "Running"
below) or deployed as part of the `agorae` lab (see
[agorae/docs/PRAXIS.md](https://github.com/recod-ai/agorae/blob/main/docs/PRAXIS.md)
for that deployment's specifics — domain, OIDC client, archeion mirroring
credentials).

Full design in [docs/design/schema.md](docs/design/schema.md).

## Status

Working skeleton: API (FastAPI) + side-by-side editor (CodeMirror + preview,
with math via KaTeX) + Kanban view for tasks, serving multiple **projects**
and standalone **knowledge bases** under `workspace/`. A SQLite index
(`app/index.py`, rebuilt from the `.md` files on startup, queried
throughout the API) makes lookups by id fast — but it's a cache, not a
second source of truth: `app/storage.py`'s file-scanning functions remain
the ground truth per schema.md's rule ("nothing may exist only in the
database"), and the index can always be rebuilt from the files alone.
Session-based login (dropdown of known users, signed cookie via
Starlette's `SessionMiddleware`) for local dev — but **still no
password**, so on its own that's session management, not real
authentication (see docs/design/schema.md#login and "Real login (OIDC)"
below for the real thing). Also has per-project encryption (see
"Encrypting a project" below) and, in deployments that configure it,
mirrors every unencrypted project's content to a read-only Forgejo repo
(see "Archeion mirroring" below).

## Running

Dependencies are managed with [uv](https://docs.astral.sh/uv/) instead of
pip — install uv itself once (see its docs), then:

```bash
uv sync
uv run uvicorn app.main:app --reload
```

`uv sync` creates `.venv` and installs everything from `uv.lock` — no
separate `pip install` step, and no need to activate the venv yourself
(`uv run` does that for the one command). If you'd rather activate it the
usual way, `uv sync` still leaves a normal `.venv/bin/activate` behind.

Open `http://127.0.0.1:8000` — the shipped workspace starts with no users
configured at all, so the dropdown is empty at first; type any username in
its "— choose a user —" field (dev mode invents one on the spot) to log
in, and it becomes an admin of "Example Project"/"Example Notes"
automatically (an empty `members:` list means "open to anyone, everyone's
admin" — see docs/design/schema.md#permissions). Add real members from
there (each project/note base's Settings panel).

Real secrets (session secret, Google OAuth credentials — see below) go in
a `.env` file, copied from `.env.example` and never committed
(`.gitignore` excludes it); `python-dotenv` loads it automatically, so
nothing extra needs to be passed on the command line. For a quick local
run, `PRAXIS_SESSION_SECRET` can also just be set inline:

```bash
PRAXIS_SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))") uv run uvicorn app.main:app --reload
```

By default it serves `workspace/` — to point at another workspace:

```bash
PRAXIS_WORKSPACE_DIR=/path/to/workspace uv run uvicorn app.main:app --reload
```

### Real login (OIDC), instead of the dev dropdown

The dropdown above is a dev-only convenience — no password, anyone can
claim to be anyone. Real use needs `PRAXIS_AUTH_MODE=oidc` plus a real
OAuth2/OIDC client — any standard OIDC provider works (this isn't
Google-specific, just discovery-URL + client credentials), but in agorae's
own deployment that provider is **metroon** (agorae's own small
access-registry service — see `docs/METROON.md` in the `agorae` repo), not
Google directly. What real login takes, end to end:

1. **Create the OAuth client** — with metroon, that means adding an entry
   to `metroon_clients` in agorae's `vars.yml`:
   ```yaml
   metroon_clients:
     - client_id: "praxis"
       client_secret: "<random>"
       redirect_uris:
         - "https://praxis.agorae.dedyn.io/auth/oidc/callback"
   ```
   (for local testing against a real metroon instance, use
   `http://127.0.0.1:8000/auth/oidc/callback` instead). Re-run
   `playbooks/metroon.yml` after adding it.
2. **Put the values in `.env`** (`cp .env.example .env`, then fill in
   `PRAXIS_OIDC_DISCOVERY_URL` — metroon's is
   `https://metroon.agorae.dedyn.io/.well-known/openid-configuration` —
   `PRAXIS_OIDC_CLIENT_ID`, `PRAXIS_OIDC_CLIENT_SECRET`, and set
   `PRAXIS_AUTH_MODE=oidc`).
3. **That's it — logins auto-provision.** Anyone your OIDC provider
   authenticates (with a verified email) gets a praxis.md account on their
   first login if they don't have one yet, username derived from the email's
   local part (`config.provision_user_from_email`). It starts in zero
   projects/note bases, though — project/note-base membership
   (`project.yml`/`kb.yml`) is what decides who can actually *see* anything,
   exactly as in `dev` mode. Add someone as a member ahead of time (with an
   `email` alongside their `username` — see the "Add member" form, or
   `POST .../members`) to have them land inside a specific project/note base
   the moment they first log in, instead of starting with none.
4. `uv run uvicorn app.main:app --reload` (or however it's actually
   deployed) — `.env` is picked up automatically.

### Encrypting a project

A project's Settings panel (the gear next to its name) has an
**Encryption** section, admin-only. "Encrypt this project" generates a
random password, seals `tasks/`/`knowledge/`/its git history into one
opaque `vault.enc` file, and shows that password exactly once — there's no
recovery if it's lost, by design (see docs/design/schema.md#encryption).
From then on, opening that project's content asks for the password
(decrypted into a RAM-backed scratch copy for as long as it's actively
used, and resealed automatically after a period of inactivity —
`PRAXIS_PROJECT_IDLE_TIMEOUT`, in seconds, defaults to 1800). Everything
about the project except its actual task/note content — name, icon,
members — stays in plain `project.yml`, unaffected.

### Archeion mirroring

If `PRAXIS_ARCHEION_URL` and friends are set (see `.env.example` — org, bot
account, and a separate admin account for managing collaborators), every
save to an **unencrypted** project pushes its current `tasks/`/`knowledge/`
content to a dedicated repo under a Forgejo org, as a second, read-only
record (`app/archeion_mirror.py`) — independent of praxis.md's own internal
git history (`app/git_store.py`), which stays unexposed. Project members
are synced there as read-only collaborators, never given write access.
Encrypted projects are never mirrored — the whole point of encrypting one
is keeping it off any second copy. See
[agorae/docs/PRAXIS.md](https://github.com/recod-ai/agorae/blob/main/docs/PRAXIS.md)
for how the org/bot account itself gets set up (a one-time manual step,
not something this app provisions on its own).

## Workspace layout

```
workspace/
  projects/
    <project-slug>/
      project.yml        # members + statuses
      tasks/
      knowledge/
  knowledge-bases/
    <kb-slug>/             # standalone, not tied to any project
```

## Implementation decisions not fully settled in the schema

- `type: task` / `type: knowledge` is an explicit frontmatter field (the
  schema didn't list it, but the code needs a reliable way to know an
  item's type without depending on which folder it's in).
- Who edits the body of a **knowledge** item (no `assigned_to`):
  implemented as "anyone with access to the project/knowledge base" — only
  `owner` stays locked on the header. Still an open item, revisit if wrong.
- Standalone knowledge bases have no membership restriction (open to
  anyone who can reach the app) — projects do. Flagged as an assumption in
  the schema.
- Project membership check is enforced at the API layer, based on the
  session's logged-in user — not real access control until login actually
  verifies identity (currently just a dropdown, no password).

## Structure

```
app/            FastAPI: routes, storage (reading/writing .md), the SQLite
                index, permissions, vault (encryption), archeion mirroring
static/         frontend (CodeMirror + preview + Kanban, no build step)
templates/      seed files (task/knowledge templates)
workspace/      example projects + knowledge bases served by default
docs/design/    schema.md (file format/permissions reference) + other
                design notes — see docs/_archive/ for superseded ones
pyproject.toml  dependencies (uv) — see Running
Dockerfile      how agorae (or any deployment) containerizes this
.env.example    template for real secrets — copy to .env, never committed
```
