# Practices review: object orientation, interface consistency

A self-assessment of the design practices behind praxis.md's code and UI,
written on request as a standalone review — not a spec (see schema.md for
that). Two parts: how the backend is actually object-oriented (and where
it deliberately isn't), and how the frontend keeps itself consistent
without a framework. (A third part on Material Design used to live here —
superseded by the newer, more thorough
[m3-compliance.md](m3-compliance.md); the original is kept in
`docs/_archive/2026-09/` for reference.)

## Part 1 — Backend: object orientation without class hierarchies

`app/*.py` has exactly one hand-written class-with-methods
(`SaveDecision`, a `@dataclass` — permissions.py:18) plus the Pydantic
`BaseModel` subclasses FastAPI uses for request validation. There is no
`Project`/`KnowledgeBase`/`Item` class hierarchy. That's a deliberate
choice, not an oversight — the object-oriented *design principles* below
are all still followed, just expressed through small functions and
FastAPI's own object model instead of custom classes:

- **Single Responsibility per module.** `storage.py` only reads/writes
  `.md` files and knows nothing about HTTP or permissions; `permissions.py`
  only decides yes/no on a save and knows nothing about the filesystem;
  `config.py` only owns `project.yml`/user-profile persistence and role
  lookup; `main.py` is the only place that knows about HTTP status codes
  and request/response shapes. Each module can be read (and would break,
  if it changed) independently of the others — the same guarantee a
  well-factored class hierarchy would give you, without needing one.

- **Encapsulation via value objects, not bare primitives.**
  `check_body_save`/`check_header_save` (permissions.py:23,39) return a
  `SaveDecision(allowed, reason)` instead of a bare `bool` or a
  `(bool, str)` tuple — callers can't accidentally read `reason` when
  `allowed` is true, or forget to check `allowed` before using `reason`,
  the way they could with an unstructured tuple. It's a minimal example of
  "make illegal states hard to represent," one of the actual goals classes
  are usually reached for.

- **Dependency injection / inversion of control**, via FastAPI's
  `Depends()` — the closest thing this codebase has to an IoC container.
  `require_project_editor` and `require_project_admin` both *depend on*
  `require_project_access` (main.py:108,114), which itself depends on
  `get_current_user` (main.py:86) — a chain of small, independently
  testable authorization checks composed by declaration, not by a route
  handler manually calling three functions in a row and remembering the
  order. Every route just declares which check it needs; FastAPI resolves
  and runs the chain.

- **Parametrization over inheritance.** A class-based design would be
  tempted to write `Project`/`KnowledgeBase` subclasses of some
  `ItemScope` base. Instead, every shared operation
  (`_list_items`, `_create_item`, `_save_header`, `_move_item`, ...) takes
  a plain `scope_dir: Path` (main.py:136 onward) — a project's directory
  and a standalone knowledge base's directory are interchangeable inputs
  to the same functions. This is composition instead of inheritance: no
  base class to design, no risk of a subclass overriding something it
  shouldn't, and the two route families (`/api/projects/{slug}/...` and
  `/api/knowledge-bases/{slug}/...`) share 100% of their logic while
  differing only in which `Depends()` chain guards them.

- **DRY at the logic layer, even though route *declarations* repeat.**
  Every `_verb_item(scope_dir, ...)` helper is called from exactly one
  project route and one kb route (e.g. `_save_header` from
  `project_save_header` and `kb_save_header`, main.py:498-501 and 559-562).
  The duplication that does exist — ~28 near-identical route
  declarations — is wiring, not logic: each pair differs only in which
  permission dependency it uses (project routes need
  `require_project_editor`/`admin`; kb routes just need `require_kb_exists`
  + `get_current_user`, since standalone knowledge bases have no
  membership model). Collapsing that into a router factory was considered
  and deliberately not done — it would trade ~28 lines of easy-to-scan,
  identical-shaped declarations for one function taking a
  permission-strategy parameter, which is a real abstraction cost for a
  problem that, as declarations, doesn't actually cause bugs (the logic
  itself is already deduplicated). Flagging it here rather than silently
  refactoring, in case you'd rather have it collapsed.

## Part 2 — Frontend: consistency without a framework or classes

`static/app.js` is a single 1500-line script, no ES6 classes, no
framework. Consistency comes from convention instead:

- **Naming families instead of methods on a class.** Every concern gets a
  verb-prefixed function group instead of an object: `render*` functions
  never mutate state (they only read `itemsCache`/`currentNoteFolder`/etc.
  and produce DOM), `save*`/`open*`/`select*` functions are the only ones
  allowed to mutate state and trigger a re-render. This is a manual,
  convention-enforced version of what a class's public/private methods
  would enforce for you — it holds because it's small enough for one
  person to keep consistent, which is also exactly the point at which a
  real framework or class structure would start paying for itself.

- **A small number of module-scoped state variables, not a scattered
  one.** `currentId`, `currentNoteFolder`, `itemsCache`, `scopeConfig`,
  `sessionUser` and a handful of others are the *entire* client-side
  state; every render function is a pure(-ish) function of that state.
  There's no hidden state stashed on DOM nodes or in closures elsewhere —
  a deliberate constraint that keeps "what does the app currently think is
  true" answerable by reading one part of the file.

- **Guards protect real invariants, not imagined ones.** `saveInFlight`
  and `headerSaveInFlight`/`headerSavePending` exist because overlapping
  autosave/header-save requests were an actual, reproduced bug this
  session (a fast save landing after a slow one, silently reverting a
  newer edit) — not defensive programming against a scenario that can't
  happen. Same for the in-flight check in `openItem` that flushes a
  pending autosave before switching documents.

- **Full-replace over partial-patch, chosen once and reused everywhere.**
  Tags, `assigned_to`, and the whole header are always sent as a complete
  replacement of the current value, never a diff/patch. One request shape
  to reason about, no merge logic, no partial-update edge cases — applied
  consistently instead of inventing a new update strategy per field.


## Known trade-offs — flagged, not fixed

- **Route declaration duplication** (Part 1) — logic is shared, the HTTP
  route wiring itself is not.
- **No drag-and-drop to move a note between folders** (existing gap from
  the previous session, unrelated to this review) — the
  `PUT .../items/{id}/folder` endpoint exists; only folder-scoped creation
  and navigation are wired up in the UI so far.
