# Material 3 compliance review

A pillar-by-pillar audit against Material Design 3, structured to match the
same seven sections it was requested in. For each: what was already true,
what changed in this pass, and — where full literal compliance would have
meant a real usability or scope trade-off for this specific app (a dense,
mouse-driven internal research-lab tool, not a mobile-first consumer app)
— what was deliberately adapted instead, and why. See also
practices-review.md for the OOP/interface-consistency review this one
builds on.

## 1. Color system and themes

**What M3 asks for:** a dynamic tonal-ramp system computed from one key
color (0–100 tone steps across Primary/Secondary/Tertiary/Neutral/Neutral
Variant), assigned to Color Roles, with WCAG AA contrast guaranteed by
construction, and a dark theme that avoids pure black.

**Done:**
- Every static color role M3 defines now exists as a token
  (`style.css` `:root`), for both themes: Primary/On-Primary/Primary-
  Container/On-Primary-Container, the same four for Secondary and
  Tertiary, Error/On-Error/Error-Container/On-Error-Container, Surface/
  On-Surface/On-Surface-Variant, Outline (`--border`)/Outline-Variant.
  Before this pass the app only had Primary and a generic "surface-alt" —
  Secondary and Tertiary didn't exist at all.
- Applied by role, not just defined: Secondary Container now backs tags,
  assigned-people chips, and status badges (M3's own example use-case for
  Secondary is "badges, chips") instead of the generic neutral tone they
  used before; the current-status badge uses Primary Container (a tonal
  highlight) instead of bare colored text.
- Every pairing was checked against real WCAG contrast math (not eyeballed
  — computed via the standard relative-luminance formula), in both
  themes: all 14 role pairs checked come out at 5:1–13:1, comfortably
  past the 4.5:1 AA minimum for normal text.
- Dark theme was already correct before this pass: `#121016`, not
  `#000000`, with surfaces getting progressively lighter (tinted) at
  higher elevation rather than staying flat black.

**Deliberately not done — dynamic/algorithmic color.** M3's tonal-ramp
system (deriving 0–100 tone steps for five palettes from one key color) is
an actual color-science algorithm (HCT color space), normally provided by
a runtime library (Google's `material-color-utilities`), not something to
hand-roll in a stylesheet. Adding that dependency to generate palettes
that could just as well be hand-picked once would be a real engineering
cost with no user-facing benefit for an app whose brand color never
changes at runtime. What's here instead is a hand-authored palette
assigned by the same *role* structure dynamic color would also produce —
the part that actually matters for consistency and accessibility.

## 2. Typography

**What M3 asks for:** five role families (Display/Headline/Title/Body/
Label), each with Large/Medium/Small, at defined sizes, using Roboto or
Google Sans.

**Done:**
- Roboto was already the primary font (loaded from Google Fonts, with a
  system-font fallback stack).
- All 15 roles now exist as utility classes (`.type-display-l` through
  `.type-label-s`), each with the same relative size/line-height/weight
  relationships M3 defines, applied to the page title and the editor's
  item-title.

**Deliberately compressed, not literal, sizes.** M3's own numbers (Display
Large = 57px, Headline Large = 32px, ...) are tuned for mobile-first
touch UIs with generous whitespace. This app is a dense, information-heavy
desktop board (task cards, kanban columns, a notes grid) built around a
14px base size; a literal 57px Display role would visually dominate a
screen otherwise full of 12–14px metadata and isn't needed anywhere in
this app to begin with (no hero numbers, no marketing-style headers). The
classes defined here keep the same *role hierarchy and relative weights*
— what actually carries the "guide reading order" intent — at sizes
proportioned to this app's own density. Applied concretely to the two
clearest "screen headline" / "card title" cases (`#main-title`, the
editor's `.meta-title`); the rest of the app's existing sizes already
land on one of these roles by size (item-card titles ≈ Title Medium,
section eyebrows ≈ Label Small/Medium, button text ≈ Label Large) without
every one of those elements having been individually re-classed — doing
that everywhere would be pure churn with no visual change, since the
numbers already match.

## 3. Layout and adaptive grid

**What M3 asks for:** an 8dp spacing grid (4dp for finer alignment),
three window-size breakpoints (Compact <600dp / Medium 600–839dp /
Expanded ≥840dp) with matching navigation patterns, and margins that grow
with screen size (16dp compact, 24dp+ medium/expanded).

**Done:**
- A formal spacing scale now exists (`--space-1` through `--space-6`, 4dp
  through 24dp) and is used in every rule touched or added this pass.
- **Adaptive breakpoints — the one pillar this app had zero support for
  before this pass.** Medium/Expanded was already the app's only layout: a
  persistent 280px sidebar (a Navigation Drawer) next to the content,
  which is exactly M3's Medium/Expanded pattern. Compact is new: below
  600px width the sidebar becomes a fixed overlay with its own elevation
  (instead of a permanent column eating most of a narrow viewport) and
  starts collapsed on load; the hamburger toggle that already existed
  now actually matters instead of being mostly decorative on a normal-
  width window. Content margins step from 16dp (compact) to 20/24dp
  (medium) automatically via the same breakpoints.
- Verified at a 480px viewport: sidebar starts hidden, the hamburger opens
  it as an overlay on top of the content (not pushing it over), content
  reflows to single-column with 16dp margins.

**Deliberately not done — a bottom Navigation Bar for Compact.** M3's
Compact pattern calls for primary navigation to move to a bottom tab bar.
This app's navigation is a multi-level tree (projects → tasks/notes/
members, plus note bases) that doesn't reduce to 3–5 fixed bottom-tab
destinations without inventing a different information architecture —
the overlay drawer keeps the *same* navigation model at every width
instead of forking it, which is more consistent with how someone already
using this app at a wider size would expect it to keep working.

## 4. Elevation, surfaces, and layers

**What M3 asks for:** elevation conveyed primarily through a lighter/more
Primary-tinted surface color as elevation increases, shadows as a
secondary cue, roughly: level 0 background, 1–2 cards/lists/app bars,
3–5 dialogs/menus/FABs.

**Done:**
- A real 0–5 elevation scale now exists (`--elevation-0` … `--elevation-5`)
  paired with three surface-tint steps (`--surface-1/2/3`, each mixing a
  little more Primary into Surface via `color-mix`) — previously there
  were only two flat shadow tokens and one fixed "alt" surface color, with
  no tint progression at all.
- Applied by the same level grouping M3 describes: level 0 = page
  background (unchanged, already correct); level 1 = cards, kanban cards,
  notes tiles (now `--surface-1`, a barely-there tint, plus the existing
  shadow); level 2 = the sidebar and header panel (`--surface-alt` is now
  literally an alias for `--surface-2`, formalizing what was already its
  de facto role); level 3 = every dialog and the toast (`--surface-3` +
  `--elevation-3`), the modal/floating tier.

**Deliberately kept to 3 of the 6 defined levels in active use.** Levels 4
and 5 exist as tokens but nothing in this app is elevated that high (no
nested menus over dialogs, no drag-preview layer) — defining unused levels
now costs nothing and means they're ready if a future component needs
them, without inventing an artificial use just to exercise every number.

## 5. Components

**What M3 asks for:** a Filled/Tonal/Outlined-or-Text/FAB button
hierarchy by emphasis, floating labels on text fields with errors shown
under the affected field, and a named shape scale from None to Full.

**Done:**
- **Button hierarchy — the specific gap raised earlier in this review,
  now genuinely three-tiered instead of two.** Filled (`.btn-primary`,
  unchanged) for the one primary action per screen/dialog; **Tonal**
  (`.btn-tonal`, new) for a real secondary-but-not-neutral action — applied
  to "+ New folder", which sits next to the primary "+ New" and deserves
  more visual weight than a plain Outlined button without competing with
  it; Outlined (the bare `<button>` default, from the previous pass) for
  every neutral secondary action; Icon button (`.btn-icon`) for label-less
  utility controls.
- **Touch target minimum, 48×48dp** — `.btn-icon` grew from 32px to a full
  48px hit area (the glyph inside stays a compact 22px), matching M3's
  accessibility minimum literally. The two places a smaller target was
  kept on purpose — the notes breadcrumb's back arrow, the tag chip's ×
  — are dense inline controls inside an already-small component, the same
  kind of exemption M3's own Chip component gets from this rule.
  (Separately: WCAG 2.2's own AA minimum, distinct from M3's stricter
  touch guidance, is actually 24×24px — this app is mouse-driven, not a
  phone, so 24px would already have been technically sufficient; 48px was
  used anyway since M3 was asked for literally.)
- **Shape scale** — a named scale now exists (`--shape-none` 0 through
  `--shape-full`), and components were mapped onto it rather than left as
  scattered numbers: buttons now use `--shape-full` (a true pill,
  correct at any height, instead of a fixed 20px radius that only looked
  fully round by coincidence at this button's specific height); chips and
  badges use `--shape-sm` (8px) — M3's chips use a **small**, not fully
  rounded, radius, which corrected an actual mismatch (this app's chips
  were pill-shaped like buttons before this pass, not to spec); cards/
  dialogs/tiles keep `--radius` (now `--shape-md`, 12px, the nearest
  step to the pre-existing 10px — a 2px nudge, not a visual change).

**Deliberately not done — floating labels; FAB placement.**
- Every input's label sits beside or above it as plain text, not floating
  inside the field. Converting every field in this app would mean two
  very different things depending on context: the standalone dialogs
  (login, profile, new-item) *could* reasonably take floating labels with
  moderate, contained risk; the header panel's fields are laid out as a
  dense label-left/value-right metadata table (12+ rows, external 100px-
  wide label column, by design so the whole header scans like a
  spreadsheet) where a floating label per row would fight that layout
  rather than fit it. Doing floating labels in the three dialogs but not
  the table would leave the app with *two* field conventions instead of
  one, which is a worse inconsistency than the single current convention
  — so this was left alone everywhere rather than half-converted.
- "+ New" (this app's one "primary, continuous action" per M3's own FAB
  description) stays a Filled Button in place in the toolbar rather than
  becoming an actual floating FAB. A real FAB is fixed-position and
  always-elevated; relocating the create action to float over page
  content would be a real information-architecture change for a toolbar-
  driven, ClickUp-style layout this app has used consistently everywhere
  else — ten other create/action buttons already live in that same
  topbar convention. Note the current implementation is not a
  half-measure either way: a Filled Button correctly has *no* resting
  elevation per M3's own button spec (only a FAB does), so "+ New" already
  matches the component it actually is.
- Error messages for the header panel land in one shared status line
  below all its fields, not under one specific field. This isn't an
  oversight: the header saves as one full-replace request (a deliberate,
  documented choice from earlier in this project, see schema.md), so most
  failures aren't localized to a single field to begin with — a shared
  status line reports what actually happened more honestly than
  inventing a specific field to blame.

## 6. Motion

**What M3 asks for:** natural accelerate/decelerate easing (the
"Emphasized" curve), container-transform-style transitions instead of
abrupt fades, ~100–200ms for feedback and ~300–500ms for full transitions.

**Done:** Motion tokens now exist (`--motion-fast` 150ms, `--motion-
standard` 300ms, `--motion-emphasized` 400ms, plus `--easing-standard`
and `--easing-emphasized` cubic-béziers) and replace the ad hoc `0.15s
ease`/`0.2s ease` that were scattered across hover/focus states and the
sidebar's collapse transition. The sidebar collapse — the one transition
in this app that's a genuine container transform (a column sliding out of
view, not just a color fade) — now specifically uses the emphasized
easing at the standard 300ms duration, matching M3's guidance for that
category of transition; simple hover/focus feedback (buttons, inputs)
uses the fast tier with standard easing, matching the 100–200ms feedback
guidance.

## 7. Accessibility

**What M3 asks for:** 48×48dp minimum touch targets, 4.5:1 contrast for
normal text (3:1 for large text/graphics), and never conveying state by
color alone.

**Done:**
- Touch targets: covered under §5 above.
- Contrast: covered under §1 — every role pairing this app actually uses
  was checked against the real WCAG formula, not assumed; all pass 4.5:1
  with margin in both themes.
- **A real bug found and fixed while auditing color-independence**: the
  autosave status text (`#save-status`) could show the neutral "Unsaved
  changes…" message in whatever color a *previous* save cycle had left
  behind — e.g. still rendering in error-red after an earlier failed
  save, even though nothing was currently wrong. Fixed by resetting the
  color explicitly every time that message is set, and by changing the
  element's CSS default color from danger-red to neutral (it was
  previously defaulting to looking like a standing error before any save
  had even happened).
- Existing color-independence was checked, not just assumed: broken
  `[[reference]]` links use a dashed underline *and* color (not color
  alone); the error toast, header-save-status, and rename/transfer status
  messages always pair color with an explanatory sentence, never color by
  itself; status badges show the status name as text next to the color.
  No case was found in this app where color was the *only* channel for a
  state — the one near-miss (save-status) is the bug fixed above.

## Summary

| Pillar | Status |
|---|---|
| 1. Color & themes | Full role set added (Secondary/Tertiary were missing); dynamic/algorithmic generation deliberately out of scope (see above) |
| 2. Typography | Full role scale added and applied to the clearest cases; sizes compressed for this app's density (see above) |
| 3. Layout & grid | Spacing scale formalized; Compact breakpoint added (was entirely missing); bottom nav bar deliberately not adopted (see above) |
| 4. Elevation & surfaces | Full 0–5 scale + surface tinting added (was 2 flat shadow levels, no tint) |
| 5. Components | Tonal button added, touch targets fixed to 48dp, shape scale corrected (chips were mis-shaped as pills); floating labels and FAB placement deliberately not adopted (see above) |
| 6. Motion | Duration/easing tokens added, applied to existing transitions |
| 7. Accessibility | Contrast verified numerically; one real color-state bug found and fixed |
