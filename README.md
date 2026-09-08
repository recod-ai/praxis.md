# praxis.md

Science-focused task and knowledge manager — markdown + YAML as the
organizing format, on the same premise as
[taskmd](https://github.com/taskmd/taskmd). Runs locally, not depending on
`knowledge-server` (Oracle Cloud) for now.

Full design in [docs/design/schema.md](docs/design/schema.md).

## Status

Working skeleton: API (FastAPI) + side-by-side editor (CodeMirror + preview,
with math via KaTeX) + Kanban view for tasks, serving multiple **projects**
and standalone **knowledge bases** under `workspace/`. Session-based login
(dropdown of known users, signed cookie via Starlette's `SessionMiddleware`)
— but **still no password**, so this is session management, not real
authentication (see docs/design/schema.md#login). No SQLite yet (the schema
allows adding it later without changing the file format — see
docs/design/schema.md#index).

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

### Real login (Google), instead of the dev dropdown

The dropdown above is a dev-only convenience — no password, anyone can
claim to be anyone. Real use needs `PRAXIS_AUTH_MODE=google` plus a real
Google OAuth client. What that takes, end to end:

1. **Create the OAuth client** — in the
   [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
   (any Google account works, doesn't need to be a Workspace admin):
   - Create a project (or reuse one).
   - **APIs & Services → OAuth consent screen**: User type "Internal" if
     this is a Google Workspace domain (e.g. `unicamp.br`) and only that
     domain should ever log in — otherwise "External" (with "Testing"
     publish status is fine while it's just your lab; Google caps
     External+Testing at 100 test users, added by email under "Test
     users" on that same screen).
   - **APIs & Services → Credentials → Create Credentials → OAuth client
     ID**, type "Web application".
   - **Authorized redirect URIs**: add `<your-url>/auth/google/callback`
     — for local testing that's `http://127.0.0.1:8000/auth/google/callback`
     (Google allows plain HTTP on localhost); a real deployment needs its
     actual HTTPS domain instead.
   - Save — it hands you a **Client ID** and **Client secret**.
2. **Put them in `.env`** (`cp .env.example .env`, then fill in
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and set
   `PRAXIS_AUTH_MODE=google`; `GOOGLE_HOSTED_DOMAIN=unicamp.br` is optional
   — it just narrows Google's account picker to that Workspace domain, it
   doesn't restrict who can log in on its own).
3. **Link each username to the email that'll sign in as them** — add
   `email: someone@unicamp.br` to `workspace/users/<username>.yml` (create
   the file if it doesn't exist yet; see Workspace layout below). Logging
   in with Google only *authenticates* — it never creates an account or
   grants access on its own, so an email with no username linked to it is
   refused, not auto-registered. Project/note-base membership
   (`project.yml`/`kb.yml`) still decides who can see what, exactly as in
   `dev` mode.
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
app/            FastAPI: routes, storage (reading/writing .md), permissions
static/         frontend (CodeMirror + preview + Kanban, no build step)
templates/      seed files (task/research_question.md so far)
workspace/      example projects + knowledge bases served by default
pyproject.toml  dependencies (uv) — see Running
.env.example    template for real secrets — copy to .env, never committed
```
