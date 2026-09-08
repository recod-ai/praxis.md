# Practices review: object orientation, interface consistency, Material Design

A self-assessment of the design practices behind praxis.md's code and UI,
written on request as a standalone review — not a spec (see schema.md for
that). It's split in three parts: how the backend is actually
object-oriented (and where it deliberately isn't), how the frontend keeps
itself consistent without a framework, and a Material Design audit that
includes the concrete fixes made in this pass.

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

## Part 3 — Material Design audit

### What already matched Material's token model

The color system (`:root` custom properties, style.css:1-32) already maps
cleanly onto Material 3's color roles, and — verified this pass — every
color used anywhere in the stylesheet is either a token definition itself
or a `var(--token)` reference; there are no rogue hardcoded hex colors
elsewhere in the file:

| praxis.md token | Material 3 role |
|---|---|
| `--primary` / `--primary-contrast` | primary / on-primary |
| `--surface` / `--on-surface` | surface / on-surface |
| `--surface-alt` | surface-container(-ish) |
| `--on-surface-variant` | on-surface-variant |
| `--border` | outline |
| `--danger` | error |

(`--success` has no direct M3 counterpart — M3 doesn't define one either;
apps commonly add their own semantic role for it, same as here.)

The one hardcoded color left, `.avatar { color: #fff }` (style.css:378),
is a deliberate exception, not a miss: avatar backgrounds are arbitrary
per-user hues from `avatarColor()`, not `--primary`, so there's no
semantic token that means "text color for an arbitrary computed
background" — hardcoding white initials text is the honest choice here,
not a shortcut.

Shape (border-radius) already forms a small, consistent scale — 6 / 8 /
10 (`--radius`) / 12 / 20 / 50% — and elevation is a deliberately simplified
two-level system (`--shadow-1` resting, `--shadow-2` hover/raised), which
is enough for an app whose only elevated surfaces are cards, dialogs, and
one toast; Material's full 0–5 elevation scale exists for apps with far
more surface types than this one has.

### What this pass fixed

**1. Notes grid/list consistency (folders vs. files reading as one
family)** — done just before this review, kept here for the record: both
tile types now share the exact same card chrome (border/radius/shadow/
hover), with icon+name always in the same position, instead of folders
and notes looking like two unrelated components.

**2. Form field interaction states — the specific gap you flagged.**
Before this pass, `input`/`select` had *zero* hover or focus styling
anywhere in the stylesheet — buttons had hover backgrounds, shadows, and
transitions, but every text field, dropdown, and date picker gave no
visual feedback at all, and the one field that did touch `:focus`
(`#header-tags-input`) removed the browser's default ring without
supplying a replacement. Fixed (style.css, base `input, select, textarea`
rule):
- a hover state (border darkens toward `--on-surface-variant`);
- a focus state (border turns `--primary`, plus a soft ring via
  `color-mix(in srgb, var(--primary) 22%, transparent)` — computed from
  the existing token so light/dark mode need no separate ring color);
- the tags input gets its own subtle version (background tint instead of
  a box ring, appropriate for a borderless inline field) instead of no
  indicator at all.

**3. Native `<select>` chrome.** Every dropdown was rendering the
browser/OS's own arrow glyph — visually unrelated to the rest of the flat,
custom-styled UI, and inconsistent across OSes. Replaced with
`appearance: none` plus a themed chevron (`--select-chevron`, an inline
SVG data URI, one per theme) so every select in the app — login, header
panel, new-item template, add-member role, everything — now uses the same
arrow, in the theme's own `--on-surface-variant` color.

**4. `color-scheme` was never declared.** Without it, Chromium draws
native controls (notably the `<input type="date">` calendar icon) in
light-mode styling even when the app is in dark mode, which can render
close to invisible against a dark field. Declared `color-scheme: light`
/ `dark` per theme (style.css:4,20) so native chrome follows the app's own
theme automatically.

**5. Iconography — emoji to Material Symbols.** Every icon in the app was
a platform emoji glyph (`&#9776;`, `📁`, `⛶`, ...) — each rendering as a
different picture per OS/browser (Apple vs. Noto vs. Segoe emoji look
nothing alike), full-color and unable to take the surrounding text's
color, so an icon never actually matched its theme. Replaced all of them
with the Material Symbols Outlined webfont (loaded once, same weight/
optical size everywhere; glyphs render in `currentColor`, so an icon
inside a themed button already matches it with no extra rule):

| Where | Old glyph | New Material Symbol |
|---|---|---|
| Hamburger menu | ☰ | `menu` |
| Theme toggle | 🌙 / ☀️ | `dark_mode` / `light_mode` |
| Edit profile | ✎ | `edit` |
| Home nav item | 🏠 | `home` |
| Attach note | 📎 | `attach_file` |
| Enter/exit fullscreen | ⛶ (same glyph both states) | `fullscreen` / `fullscreen_exit` |
| Close editor | ✕ | `close` |
| "Open →" (main task) | → | `arrow_forward` |
| Project nav expand/collapse | ▾ / ▸ | `expand_more` / `chevron_right` |
| Notes: folder (list + grid) | 📁 | `folder` |
| Notes: file (list + grid) | 📄 | `description` |

**6. Buttons had no real hierarchy — the specific gap flagged after the
first pass of this review.** Before this fix, exactly one button style
existed beyond `.btn-primary`: a bordered pill with neutral (`on-surface`)
text, applied by default to *everything* — Cancel, Log out, Rename file,
Transfer ownership, the hamburger, the profile pencil, the editor's close
×, tag/nav/tab buttons (via one-off resets scattered across the
stylesheet) — so a genuine secondary action (Cancel next to Save) and an
icon-only utility control (hamburger) and a structural nav item all read
as the same undifferentiated gray box, none of them matching any of
Material's actual button variants. Rebuilt as three variants
(style.css, base `button` rule onward):
- **Filled** (`.btn-primary`, unchanged) — the one confirming/primary
  action per screen or dialog (Save, Create, Log in, + New).
- **Outlined** (now the bare `<button>` default) — every secondary action
  (Cancel, Log out, Rename file, Transfer ownership, Attach note,
  Fullscreen, + New folder, + Add subtask, ...): transparent container,
  `--primary`-colored label/icon (was neutral `on-surface` before — the
  detail that actually signals "this is a Material button" rather than "a
  box with a border"), border in `--border`, primary-tinted hover/focus.
- **Icon button** (`.btn-icon`, new) — a circular, label-less 32px target
  for the three true icon-only controls (hamburger, edit-profile pencil,
  editor close ×): no border, neutral `on-surface-variant` icon color (per
  M3's standard — not tinted primary, since it isn't a toggle/selected
  state), a soft circular hover tint.

Structural components that are `<button>` elements for semantics but
aren't the Button component at all — nav items, tabs, breadcrumbs, the tag
chip's × — already set their own color/border explicitly and were
re-checked to make sure none of them silently inherited the new
`--primary` default; `.nav-item` was the one case missing an explicit
`color`, now pinned to `--on-surface` so the nav drawer stays neutral
outside its `.active` state, per Material's Navigation spec rather than
its Button spec.

One knock-on fix found while reviewing this: `.member-row select`
overrode the base select's reserved right-padding with a shorthand
`padding: 4px 10px`, making the themed dropdown chevron (added earlier
this pass) overlap the role text. Given its own `padding-right`, keeping
the chevron clear.

**7. A stray spacing outlier.** `.header-field` used `padding: 5px 6px` —
the only odd-numbered padding value in the whole stylesheet, next to an
otherwise consistent 2/4/6/8/10/12px rhythm. Normalized to `padding: 6px`.

**8. Editor topbar had too many competing actions — flagged directly.**
Six buttons sat in one row regardless of how often each is actually used:
Rename file, Transfer ownership, Attach note, Fullscreen, the read/edit
toggle, Save, and close — most of them as labeled Outlined buttons, so the
row read as a wall of text rather than a small set of clear choices.
Rebalanced by relocating and by dropping labels where the icon alone (plus
a `title` tooltip) already says enough:
- **Rename file** and **Transfer ownership** moved out of the topbar
  entirely, next to the **Owner** row in the header panel instead — that's
  the data they act on, so the actions now sit where their subject already
  lives, rather than in a toolbar shared by unrelated view controls. Both
  became icon-only (`drive_file_rename_outline`, `swap_horiz`), and their
  inline status messages moved from the topbar's `#save-status` to the
  header panel's own `#header-save-status`, since that's where they're
  triggered from now.
- **Attach note**, **Fullscreen**, and the **read/edit toggle** stay in the
  topbar (they're view controls, not data edits) but dropped their text
  labels — icon-only, `.btn-icon`, with `title`/`aria-label` carrying the
  words instead. The read/edit toggle's icon now tracks the same thing its
  label used to (`edit` when reading — click to start editing;
  `visibility` when editing — click to preview), so the meaning doesn't
  get lost along with the text.
- **Save** keeps its label — it's the one action in the row still worth
  spelling out, and staying the only filled/labeled button in an otherwise
  icon-only row makes it more prominent, not less.

**9. Two latent `[hidden]` bugs, found while wiring up the icon-only
buttons.** Both `button:has(.material-symbols-outlined)` (added earlier
this review, for icon+label buttons) and `.header-field` (pre-existing)
set `display` unconditionally, which — per the CSS cascade — beats the
browser's own `[hidden] { display: none }` default UA rule regardless of
the `hidden` attribute actually being set (an author-stylesheet
declaration always outranks a UA one, independent of specificity). In
practice this meant `#header-parent-open`, `#attach-note-btn`, and the new
`#rename-file-btn`/`#transfer-owner-btn` kept rendering even when their
own code had just set `.hidden = true`, and every `.header-field` marked
hidden for a note (Status/Due date/Assigned to/Subtasks — task-only
fields) rendered as an empty row instead of disappearing. This codebase
already has the fix as an established, repeated pattern for exactly this
failure mode (`#header-panel[hidden]`, `.board-view[hidden]`,
`#toast[hidden]`, etc., style.css) — the two new rules
(`button:has(...)[hidden]`, `.header-field[hidden]`, `.btn-icon[hidden]`)
just extend it to the elements this pass touched. Anything given its own
unconditional `display` going forward needs the same `[hidden]` sibling
rule, or it'll silently ignore being hidden.

## Known trade-offs — flagged, not fixed

- **Typography isn't a named scale.** Font sizes (11px–18px) are chosen
  ad hoc per element rather than drawn from a small set of named type
  roles the way color/shape/spacing are. Low visual impact today; would
  only be worth formalizing if the type sizes start drifting inconsistently
  as more views get added.
- **Route declaration duplication** (Part 1) — logic is shared, the HTTP
  route wiring itself is not.
- **No drag-and-drop to move a note between folders** (existing gap from
  the previous session, unrelated to this review) — the
  `PUT .../items/{id}/folder` endpoint exists; only folder-scoped creation
  and navigation are wired up in the UI so far.

## Keeping it consistent going forward

For anyone (human or model) adding to this UI later: reuse an existing
`var(--token)` before defining a new color/shadow/radius; give any new
interactive control both a hover and a focus state (copy the base
`input`/`select` rule's pattern); use a Material Symbols ligature name for
any new icon, never a raw emoji or HTML entity.
