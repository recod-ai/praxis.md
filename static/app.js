// --- shared DOM root + editor instance (declared first: referenced by
// applyTheme() below, which runs immediately — a `const` declared later in
// the file cannot be touched before its own line runs, even via `typeof`,
// so this order matters) ---

const root = document.documentElement;
const editorDialog = document.getElementById("editor-dialog");
const editorEl = document.getElementById("editor");
const cm = CodeMirror.fromTextArea(editorEl, {
  mode: "markdown",
  lineWrapping: true,
  theme: root.getAttribute("data-theme") === "dark" ? "material-darker" : "default",
});

// --- theme ---

const themeToggle = document.getElementById("theme-toggle");

function applyTheme(theme) {
  root.setAttribute("data-theme", theme);
  themeToggle.innerHTML = theme === "dark"
    ? '<span class="material-symbols-outlined" aria-hidden="true">dark_mode</span> Dark'
    : '<span class="material-symbols-outlined" aria-hidden="true">light_mode</span> Light';
  themeToggle.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
  cm.setOption("theme", theme === "dark" ? "material-darker" : "default");
}

const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
applyTheme(localStorage.getItem("praxis_theme") || (systemDark ? "dark" : "light"));

themeToggle.addEventListener("click", () => {
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  localStorage.setItem("praxis_theme", next);
  applyTheme(next);
});

// --- sidebar collapse ---

const sidebar = document.getElementById("sidebar");

function toggleSidebar() {
  sidebar.classList.toggle("collapsed");
}

document.getElementById("hamburger").addEventListener("click", toggleSidebar);

// M3's Compact window size class (<600dp): the sidebar becomes an overlay
// (see the matching @media rule in style.css) rather than a permanent
// column, so it should start out of the way instead of covering most of a
// narrow viewport on first load
if (window.matchMedia("(max-width: 599px)").matches) sidebar.classList.add("collapsed");

// --- toast (for errors raised outside any modal dialog, e.g. drag-and-drop
// in the list/kanban views — a plain alert() there is jarring and blocks
// the tab; a dialog's own errors still use their inline status text) ---

const toastEl = document.getElementById("toast");
let toastTimer = null;

function showToast(message, type) {
  toastEl.textContent = message;
  toastEl.className = type === "error" ? "toast-error" : "";
  toastEl.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, 4000);
}

// --- project vault (encryption at rest, see vault.py) ---
//
// A locked project answers every request about its content with 423
// (see main.py's ProjectLocked handler), body {locked, slug, detail}. This
// wraps window.fetch itself, once, so no other call site in this file
// needs to know encryption exists: any fetch that comes back 423 prompts
// for that project's password inline (with a spinner while it unlocks),
// then transparently retries the exact same request. Concurrent 423s for
// the same project (e.g. tasks/notes/tags all loading at once when you
// open it) share one prompt instead of stacking dialogs.

const rawFetch = window.fetch.bind(window);

const unlockDialog = document.getElementById("unlock-dialog");
const unlockForm = document.getElementById("unlock-form");
const unlockDialogText = document.getElementById("unlock-dialog-text");
const unlockSecretInput = document.getElementById("unlock-secret-input");
const unlockStatus = document.getElementById("unlock-status");
const unlockSpinner = document.getElementById("unlock-spinner");
const unlockSubmitBtn = document.getElementById("unlock-submit");
const unlockCancelBtn = document.getElementById("unlock-cancel");

const pendingUnlocks = {}; // project slug -> in-flight Promise<boolean>

function unlockProject(slug) {
  if (!(slug in pendingUnlocks)) {
    pendingUnlocks[slug] = promptForUnlock(slug).finally(() => { delete pendingUnlocks[slug]; });
  }
  return pendingUnlocks[slug];
}

function promptForUnlock(slug) {
  return new Promise((resolve) => {
    unlockDialogText.textContent = `"${slug}" is locked — enter its password to open it.`;
    unlockSecretInput.value = "";
    unlockStatus.hidden = true;
    unlockSpinner.hidden = true;
    unlockSubmitBtn.disabled = false;
    unlockDialog.showModal();
    requestAnimationFrame(() => unlockSecretInput.focus());

    function cleanup(result) {
      unlockDialog.close();
      unlockForm.removeEventListener("submit", onSubmit);
      unlockCancelBtn.removeEventListener("click", onCancel);
      unlockDialog.removeEventListener("cancel", onCancel);
      resolve(result);
    }
    function onCancel(e) {
      if (e) e.preventDefault(); // we call .close() ourselves in cleanup, once the promise is settled
      cleanup(false);
    }
    async function onSubmit(e) {
      e.preventDefault();
      const secret = unlockSecretInput.value;
      if (!secret) return;
      unlockStatus.hidden = true;
      unlockSpinner.hidden = false;
      unlockSubmitBtn.disabled = true;
      try {
        const res = await rawFetch(`/api/projects/${encodeURIComponent(slug)}/unlock`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ secret }),
        });
        if (res.ok) {
          cleanup(true);
          return;
        }
        const body = await res.json().catch(() => ({}));
        unlockStatus.textContent = body.detail || "Could not unlock.";
      } catch {
        unlockStatus.textContent = "Network error — try again.";
      }
      unlockStatus.hidden = false;
      unlockSpinner.hidden = true;
      unlockSubmitBtn.disabled = false;
    }

    unlockForm.addEventListener("submit", onSubmit);
    unlockCancelBtn.addEventListener("click", onCancel);
    unlockDialog.addEventListener("cancel", onCancel);
  });
}

window.fetch = async function (input, init) {
  const res = await rawFetch(input, init);
  if (res.status !== 423) return res;
  let body;
  try {
    body = await res.clone().json();
  } catch {
    return res;
  }
  if (!body || !body.locked || !body.slug) return res;
  const unlocked = await unlockProject(body.slug);
  if (!unlocked) return res; // user cancelled — caller still sees the original 423
  return rawFetch(input, init); // retry once, now that it's open
};

// --- session / login (dropdown of known users, no password — see
// docs/design/schema.md#permissions for what this does and doesn't guarantee) ---

const loginDialog = document.getElementById("login-dialog");
const loginSelect = document.getElementById("login-select");
const loginKnownUsers = document.getElementById("login-known-users");
const loginSelectLabel = document.getElementById("login-select-label");
const loginSubmitBtn = document.getElementById("login-submit");
const loginOidcBtn = document.getElementById("login-oidc-btn");
const loginError = document.getElementById("login-error");
const currentUserLabel = document.getElementById("current-user-label");
const currentUserAvatar = document.getElementById("current-user-avatar");

let sessionUser = null;
let userProfiles = {}; // username -> {username, full_name, photo} — see loadUserProfiles
let authMode = "dev"; // "dev" (dropdown, no password) or "oidc" (real OAuth2/OIDC) — see /api/auth/config
let metroonProfileUrl = null; // set alongside authMode — see loadAuthConfig

// Fetched once up front (checkSession, below) rather than only inside
// showLoginScreen — a resumed session skips that path entirely, but the
// profile dialog still needs to know authMode/metroonProfileUrl even then.
async function loadAuthConfig() {
  const res = await fetch("/api/auth/config");
  if (!res.ok) return;
  const data = await res.json();
  authMode = data.mode;
  metroonProfileUrl = data.metroon_profile_url || null;
}

function currentUser() {
  return sessionUser || "";
}

// full_name if the user has set one (see the profile dialog below), else
// just the bare username — used anywhere a person's name is displayed
function displayNameFor(username) {
  const profile = userProfiles[username];
  return (profile && profile.full_name) || username;
}

async function loadUserProfiles() {
  const res = await fetch("/api/users");
  const users = res.ok ? await res.json() : [];
  userProfiles = {};
  for (const u of users) userProfiles[u.username] = u;
  return users;
}

loginDialog.addEventListener("cancel", (e) => e.preventDefault()); // mandatory gate, no Escape-to-dismiss

// A failed /auth/oidc/callback redirects here with ?auth_error=... (see
// main.py) rather than rendering its own error page, so the message shows
// up in the same login dialog the user was already looking at.
const authErrorFromUrl = new URLSearchParams(location.search).get("auth_error");
if (authErrorFromUrl) {
  history.replaceState(null, "", location.pathname); // don't keep re-showing it on refresh
}

async function showLoginScreen() {
  if (authMode === "oidc") {
    loginSelectLabel.hidden = true;
    loginSubmitBtn.hidden = true;
    loginOidcBtn.hidden = false;
  } else {
    loginSelectLabel.hidden = false;
    loginSubmitBtn.hidden = false;
    loginOidcBtn.hidden = true;
    const users = await loadUserProfiles();
    loginSelect.value = "";
    // a free-typed username, not a strict picklist — with no members
    // configured anywhere yet (the shipped workspace's starting state,
    // see docs/design/schema.md#permissions), list_known_users() is empty
    // and a <select> would have nothing to offer at all; known usernames
    // still show up as autocomplete suggestions via this <datalist>
    loginKnownUsers.innerHTML = users
      .map((u) => `<option value="${u.username}">${u.full_name ? `${u.full_name} (${u.username})` : ""}</option>`)
      .join("");
  }

  if (authErrorFromUrl) {
    loginError.textContent = authErrorFromUrl;
    loginError.hidden = false;
  }
  loginDialog.showModal();
}

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = loginSelect.value;
  if (!username) return;
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  if (res.ok) {
    sessionUser = username;
    loginDialog.close();
    onLoggedIn();
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  location.reload();
});

async function onLoggedIn() {
  await loadUserProfiles(); // showLoginScreen already loaded these once, but a resumed session (checkSession) skips that path
  currentUserLabel.textContent = displayNameFor(sessionUser);
  currentUserAvatar.innerHTML = avatarHTML(sessionUser);
  loadNav();
}

async function checkSession() {
  await loadAuthConfig();
  const res = await fetch("/api/session");
  const data = await res.json();
  if (data.user) {
    sessionUser = data.user;
    onLoggedIn();
  } else {
    await showLoginScreen();
  }
}

// --- profile dialog: full name + photo, for now just a data: URI kept
// small client-side (photos aren't stored as separate files) ---

const profileDialog = document.getElementById("profile-dialog");
const profileFullName = document.getElementById("profile-full-name");
const profilePhotoInput = document.getElementById("profile-photo-input");
const profilePhotoPreview = document.getElementById("profile-photo-preview");
const profileRemovePhotoBtn = document.getElementById("profile-remove-photo");
const profileEditableFields = document.getElementById("profile-editable-fields");
const profileMetroonNote = document.getElementById("profile-metroon-note");
const profileMetroonLink = document.getElementById("profile-metroon-link");
const profileSaveBtn = document.getElementById("profile-save-btn");
const MAX_PHOTO_BYTES = 2 * 1024 * 1024; // keeps each user's profile .yml small

let pendingPhotoDataUri = null; // full-replace, like the header form: preloaded with the current value, resent as-is unless changed

function renderPhotoPreview() {
  profilePhotoPreview.innerHTML = pendingPhotoDataUri ? `<img src="${pendingPhotoDataUri}" alt="" />` : "";
  profileRemovePhotoBtn.hidden = !pendingPhotoDataUri;
}

document.getElementById("edit-profile-btn").addEventListener("click", () => {
  // In oidc mode, name/photo are only editable in metroon (see
  // docs/METROON.md) — swap the editable fields for a link there instead
  // of pretending a local edit would stick (metroon's own push is what
  // actually updates userProfiles, via /api/internal/profile-sync).
  const managedByMetroon = authMode === "oidc" && metroonProfileUrl;
  profileEditableFields.hidden = managedByMetroon;
  profileMetroonNote.hidden = !managedByMetroon;
  profileSaveBtn.hidden = managedByMetroon;
  if (managedByMetroon) {
    profileMetroonLink.href = metroonProfileUrl;
  } else {
    const profile = userProfiles[currentUser()] || {};
    profileFullName.value = profile.full_name || "";
    pendingPhotoDataUri = profile.photo || null;
    profilePhotoInput.value = "";
    renderPhotoPreview();
  }
  profileDialog.showModal();
});

profilePhotoInput.addEventListener("change", () => {
  const file = profilePhotoInput.files[0];
  if (!file) return;
  if (file.size > MAX_PHOTO_BYTES) {
    alert("Please choose an image under 2MB.");
    profilePhotoInput.value = "";
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    pendingPhotoDataUri = reader.result;
    renderPhotoPreview();
  };
  reader.readAsDataURL(file);
});

profileRemovePhotoBtn.addEventListener("click", () => {
  pendingPhotoDataUri = null;
  profilePhotoInput.value = "";
  renderPhotoPreview();
});

document.getElementById("profile-cancel").addEventListener("click", () => profileDialog.close());
closeOnBackdropClick(profileDialog); // function declaration — hoisted, safe to call ahead of its own definition further down

document.getElementById("profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await fetch("/api/users/me/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full_name: profileFullName.value.trim() || null, photo: pendingPhotoDataUri }),
  });
  if (res.ok) {
    profileDialog.close();
    await loadUserProfiles();
    currentUserLabel.textContent = displayNameFor(sessionUser);
    currentUserAvatar.innerHTML = avatarHTML(sessionUser);
    renderCurrentView(); // refresh any avatars/names already on screen
  } else {
    alert((await res.json()).detail || "Could not save profile.");
  }
});

// --- shared state ---

const navHome = document.getElementById("nav-home");
const navProjects = document.getElementById("nav-projects");
const navKbs = document.getElementById("nav-kbs");
const newProjectBtn = document.getElementById("new-project-btn");
const newKbBtn = document.getElementById("new-kb-btn");
const viewToggle = document.getElementById("view-toggle");
const homeFilter = document.getElementById("home-filter");
const newBtn = document.getElementById("new-btn");
const mainTitle = document.getElementById("main-title");
const homeView = document.getElementById("home-view");
const listView = document.getElementById("list-view");
const kanbanView = document.getElementById("kanban-view");
const notesListView = document.getElementById("notes-list-view");
const notesGridView = document.getElementById("notes-grid-view");
const notesViewToggle = document.getElementById("notes-view-toggle");
const notesBreadcrumb = document.getElementById("notes-breadcrumb");
const newFolderBtn = document.getElementById("new-folder-btn");
const membersView = document.getElementById("members-view");
const membersList = document.getElementById("members-list");
const scopeSettingsFields = document.getElementById("scope-settings-fields");
const scopeNameInput = document.getElementById("scope-name-input");
const scopeIconSelect = document.getElementById("scope-icon-select");
const scopeIconPreview = document.getElementById("scope-icon-preview");
const scopeNameSaveBtn = document.getElementById("scope-name-save");
const scopeEncryptionFields = document.getElementById("scope-encryption-fields");
const encryptionStatusText = document.getElementById("encryption-status-text");
const encryptProjectBtn = document.getElementById("encrypt-project-btn");
const lockProjectBtn = document.getElementById("lock-project-btn");
const vaultSecretDialog = document.getElementById("vault-secret-dialog");
const vaultSecretProjectName = document.getElementById("vault-secret-project-name");
const vaultSecretValue = document.getElementById("vault-secret-value");

// A curated set, not a free-text icon name — keeps every project/note base
// icon in the same Material Symbols family as the rest of the app instead
// of risking a typo'd/nonexistent glyph name rendering as nothing.
const SCOPE_ICONS = [
  "folder", "menu_book", "science", "biotech", "psychology", "insights",
  "rocket_launch", "terminal", "dataset", "groups", "lightbulb", "public",
  "satellite_alt", "water_drop", "smart_toy", "memory", "functions",
  "timeline", "flag", "star", "eco", "hub",
];
scopeIconSelect.innerHTML = SCOPE_ICONS.map((i) => `<option value="${i}">${i.replace(/_/g, " ")}</option>`).join("");
scopeIconSelect.addEventListener("change", () => {
  scopeIconPreview.textContent = scopeIconSelect.value;
});

let scope = null; // { type: "project" | "kb", slug: string } — null while on Home
let contentFilter = null; // "tasks" | "knowledge" — which sub-section of the scope we're viewing
let activeNav = "home"; // "home" | "project-tasks:<slug>" | "project-knowledge:<slug>" | "kb-knowledge:<slug>" | "kb-settings:<slug>"
let itemsCache = [];
let currentId = null;
let currentVersion = null; // the body's blob version last seen from the server — sent back on save so a concurrent edit can be merged instead of overwritten, see saveCurrentItem
let currentPermissions = null; // the open item's server-computed permissions (see _get_item) — used by the history panel's Restore button
let currentView = "home"; // "home" | "list" | "kanban" | "notes-list" | "notes-grid" | "members"
let homeBucket = "today"; // "today" | "this_week" | "others" — Home due-date filter
let scopeConfig = null; // { statuses, members, role } for the current project scope — null for kb/home
let currentNoteFolder = ""; // relative path under notes root; "" = root — see selectScope/selectHome
let notesFoldersCache = []; // every folder path in the current scope, flat (see loadNoteFolders)

function itemsBaseUrl() {
  if (!scope) return null;
  return scope.type === "project"
    ? `/api/projects/${scope.slug}/items`
    : `/api/knowledge-bases/${scope.slug}/items`;
}

function foldersUrl() {
  if (!scope) return null;
  return scope.type === "project" ? `/api/projects/${scope.slug}/folders` : `/api/knowledge-bases/${scope.slug}/folders`;
}

// standalone note bases hold nothing but notes; a project's own "Notes" tab
// is contentFilter === "knowledge" — either way, folders/list-grid apply
function isNotesContext() {
  return !!scope && (scope.type === "kb" || contentFilter === "knowledge");
}

async function loadNoteFolders() {
  const res = await fetch(foldersUrl());
  notesFoldersCache = res.ok ? await res.json() : [];
}

// note bases now have real members/roles too (see kb_config in main.py),
// fetched into scopeConfig the same way a project's are — the "editor"
// fallback only covers the brief window before that fetch resolves, not a
// standing policy anymore
function currentRoleForScope() {
  if (!scope) return "guest";
  if (scopeConfig) return scopeConfig.role;
  return scope.type === "kb" ? "editor" : "guest";
}

// --- editor dialog (CodeMirror + math/markdown preview) ---

const editorPane = document.getElementById("editor-pane");
const preview = document.getElementById("preview");
const metaSummary = document.getElementById("meta-summary");
const saveStatus = document.getElementById("save-status");
const transferOwnerBtn = document.getElementById("transfer-owner-btn");
const renameFileBtn = document.getElementById("rename-file-btn");
const deleteItemBtn = document.getElementById("delete-item-btn");
const attachNoteBtn = document.getElementById("attach-note-btn");
const saveBtn = document.getElementById("save-btn");
const historyBtn = document.getElementById("history-btn");
const historyDialog = document.getElementById("history-dialog");
const historyList = document.getElementById("history-list");
const historyStatus = document.getElementById("history-status");
const historyCloseBtn = document.getElementById("history-close");

function renderMarkdownWithMath(source) {
  const mathBlocks = [];
  const stash = (expr, displayMode) => {
    mathBlocks.push({ expr, displayMode });
    return `@@MATH${mathBlocks.length - 1}@@`;
  };
  let text = source.replace(/\$\$([\s\S]+?)\$\$/g, (_, expr) => stash(expr, true));
  text = text.replace(/\$([^$\n]+?)\$/g, (_, expr) => stash(expr, false));

  let html = marked.parse(text);

  html = html.replace(/@@MATH(\d+)@@/g, (_, idx) => {
    const { expr, displayMode } = mathBlocks[Number(idx)];
    try {
      return katex.renderToString(expr, { displayMode, throwOnError: false });
    } catch (e) {
      return `<code>${expr}</code>`;
    }
  });
  return html;
}

// [[item-id]] in the body renders as a clickable link to that item — a
// citation you can navigate, not just a plain-text id (see the "insert
// reference" picker below for adding one without typing/remembering the id)
function linkifyReferences(html) {
  return html.replace(/\[\[([\w-]+)\]\]/g, (whole, id) => {
    const item = itemsCache.find((i) => i.id === id);
    if (!item) return `<span class="ref-broken" title="No item with this id in the current view">${whole}</span>`;
    return `<a href="javascript:void(0)" class="ref-link" data-ref-id="${id}">${item.title || id}</a>`;
  });
}

function renderPreview() {
  preview.innerHTML = linkifyReferences(renderMarkdownWithMath(cm.getValue() || ""));
}

preview.addEventListener("click", (e) => {
  const link = e.target.closest(".ref-link");
  if (link) openItem(link.dataset.refId);
});

// --- attach-a-note picker: search itemsCache by title/id, inserting
// [[id]] at the cursor instead of asking you to type/remember one. Notes
// only — attaching/citing another *task* goes through Main task/Subtasks
// instead (see renderParentControl), not through an inline reference. ---

const refPickerDialog = document.getElementById("ref-picker-dialog");
const refPickerSearch = document.getElementById("ref-picker-search");
const refPickerList = document.getElementById("ref-picker-list");

function renderRefPickerList(filterText) {
  const q = (filterText || "").trim().toLowerCase();
  const matches = itemsCache.filter((i) => {
    if (i.type !== "knowledge" || i.id === currentId) return false;
    if (!q) return true;
    return (i.title || "").toLowerCase().includes(q) || i.id.toLowerCase().includes(q);
  });
  refPickerList.innerHTML = "";
  for (const item of matches.slice(0, 50)) {
    const row = document.createElement("div");
    row.className = "ref-picker-row";
    row.innerHTML = `<span>${item.title || item.id}</span><span class="ref-picker-id">${item.id}</span>`;
    row.addEventListener("click", () => {
      cm.replaceSelection(`[[${item.id}]]`);
      refPickerDialog.close();
      cm.focus();
    });
    refPickerList.appendChild(row);
  }
  if (!matches.length) refPickerList.innerHTML = '<span class="header-empty-hint">No matching notes.</span>';
}

attachNoteBtn.addEventListener("click", () => {
  refPickerSearch.value = "";
  renderRefPickerList("");
  refPickerDialog.showModal();
  refPickerSearch.focus();
});
refPickerSearch.addEventListener("input", () => renderRefPickerList(refPickerSearch.value));
closeOnBackdropClick(refPickerDialog);

// --- autosave: debounced, with a hard cap so continuous typing still
// flushes periodically, and an immediate flush when the editor closes ---

const AUTOSAVE_DELAY = 1500; // quiet period after the last keystroke
const AUTOSAVE_MAX_WAIT = 8000; // upper bound even if typing never pauses

let autosaveTimer = null;
let autosavePendingSince = null;

function scheduleAutosave() {
  const now = Date.now();
  if (!autosavePendingSince) autosavePendingSince = now;
  if (autosaveTimer) clearTimeout(autosaveTimer);
  const wait = Math.min(AUTOSAVE_DELAY, Math.max(0, AUTOSAVE_MAX_WAIT - (now - autosavePendingSince)));
  autosaveTimer = setTimeout(saveCurrentItem, wait);
}

function cancelPendingAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer);
  autosaveTimer = null;
  autosavePendingSince = null;
}

let saveInFlight = false;

async function saveCurrentItem() {
  cancelPendingAutosave();
  if (!currentId) return;
  if (saveInFlight) {
    // still typing while the previous autosave is in transit — don't fire a
    // second overlapping PUT (could apply out of order); try again shortly
    scheduleAutosave();
    return;
  }
  saveInFlight = true;
  saveStatus.style.color = "var(--on-surface-variant)";
  saveStatus.textContent = "Saving…";
  try {
    const res = await fetch(`${itemsBaseUrl()}/${currentId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: cm.getValue(), base_version: currentVersion }),
    });
    if (res.ok) {
      const result = await res.json();
      currentVersion = result.version;
      if (result.merged) {
        // someone else saved this note while we were editing — git merged
        // the two edits automatically (no overlapping lines), so the
        // editor's content is replaced with the merged result rather than
        // silently diverging from what's now on disk
        cm.setValue(result.body);
        saveStatus.style.color = "var(--on-surface-variant)";
        saveStatus.textContent = "Saved.";
        showEditorBanner("Merged automatically with another edit made at the same time.", "info", [
          { label: "Dismiss", onClick: hideEditorBanner },
        ]);
      } else {
        saveStatus.style.color = "var(--success)";
        saveStatus.textContent = "Saved.";
      }
      await loadItems();
    } else if (res.status === 409) {
      const err = await res.json();
      const detail = err.detail;
      if (detail && detail.conflict) {
        // a real conflict: two overlapping edits — drop git's own conflict
        // markers into the editor for the user to resolve by hand, same as
        // resolving a merge conflict on the command line
        currentVersion = detail.version;
        cm.setValue(detail.merged_body || detail.current_body || cm.getValue());
        saveStatus.textContent = "";
        showEditorBanner(
          detail.reason || "Someone else edited this note at the same time.",
          "error",
          [{ label: "Dismiss", onClick: hideEditorBanner }],
        );
      } else {
        saveStatus.style.color = "var(--danger)";
        saveStatus.textContent = (detail && detail.reason) || detail || "Error while saving.";
      }
    } else {
      const err = await res.json();
      saveStatus.style.color = "var(--danger)";
      saveStatus.textContent = err.detail || "Error while saving.";
    }
  } finally {
    saveInFlight = false;
  }
}

// close via the × button, backdrop click, or Escape all end up here — flush
// whatever's still pending instead of silently dropping the last few
// keystrokes before the debounce would've fired
editorDialog.addEventListener("close", () => {
  if (autosaveTimer) saveCurrentItem();
  stopPresence();
});

// --- real-time: presence + live updates (SSE) ---
// One EventSource per open scope (project or note base) — every client
// looking at that scope's items gets pushed an event on every save, so
// lists refresh and the open item (if any) can offer a reload instead of
// the old status quo of only ever finding out on the next manual refresh.

const presenceIndicator = document.getElementById("presence-indicator");
const editorBanner = document.getElementById("editor-banner");
const editorBannerText = document.getElementById("editor-banner-text");
const editorBannerActions = document.getElementById("editor-banner-actions");

let scopeEventSource = null;
let presenceTimer = null;
let presenceItemId = null;

function scopeEventsUrl() {
  if (!scope) return null;
  return scope.type === "project" ? `/api/projects/${scope.slug}/events` : `/api/knowledge-bases/${scope.slug}/events`;
}

function connectScopeEvents() {
  disconnectScopeEvents();
  const url = scopeEventsUrl();
  if (!url) return;
  scopeEventSource = new EventSource(url);
  scopeEventSource.onmessage = (ev) => {
    let data;
    try {
      data = JSON.parse(ev.data);
    } catch {
      return; // a keep-alive comment line, or something unparseable — ignore either way
    }
    if (data.type === "item_changed") {
      if (data.by === currentUser()) return; // our own save — already handled by saveCurrentItem's own response
      loadItems();
      if (data.id && data.id === currentId) showRemoteUpdateBanner();
    } else if (data.type === "presence" && data.item_id === currentId) {
      renderPresence(data.users.filter((u) => u !== currentUser()));
    }
  };
}

function disconnectScopeEvents() {
  if (scopeEventSource) {
    scopeEventSource.close();
    scopeEventSource = null;
  }
}

function startPresence(id) {
  stopPresence();
  presenceItemId = id;
  const beat = async () => {
    if (presenceItemId !== id || !scope) return;
    try {
      const res = await fetch(`${itemsBaseUrl()}/${id}/presence`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (res.ok) {
        const { users } = await res.json();
        renderPresence(users);
      }
    } catch {
      // presence is a nice-to-have — a failed heartbeat never blocks editing
    }
  };
  beat();
  presenceTimer = setInterval(beat, 8000);
}

function stopPresence() {
  clearInterval(presenceTimer);
  presenceTimer = null;
  presenceItemId = null;
  renderPresence([]);
}

function renderPresence(users) {
  if (!users.length) {
    presenceIndicator.hidden = true;
    presenceIndicator.textContent = "";
    return;
  }
  presenceIndicator.hidden = false;
  presenceIndicator.textContent = users.length === 1 ? `${users[0]} is also here` : `${users.join(", ")} are also here`;
}

function showEditorBanner(message, variant, actions = []) {
  editorBanner.className = `banner-${variant}`;
  editorBanner.hidden = false;
  editorBannerText.textContent = message;
  editorBannerActions.innerHTML = "";
  for (const action of actions) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = action.label;
    btn.addEventListener("click", action.onClick);
    editorBannerActions.appendChild(btn);
  }
}

function hideEditorBanner() {
  editorBanner.hidden = true;
}

function showRemoteUpdateBanner() {
  showEditorBanner("Someone else just updated this item.", "info", [
    { label: "Reload", onClick: () => { hideEditorBanner(); openItem(currentId); } },
    { label: "Dismiss", onClick: hideEditorBanner },
  ]);
}

// --- version history / audit panel — every save is a git commit (see
// docs/design/schema.md#versioning); this just surfaces that history and
// lets an editor restore an older version instead of only reading about it ---

function formatHistoryDate(iso) {
  return new Date(iso).toLocaleString();
}

async function openHistoryDialog() {
  if (!currentId) return;
  historyStatus.textContent = "";
  historyList.innerHTML = "<p>Loading…</p>";
  historyDialog.showModal();
  const res = await fetch(`${itemsBaseUrl()}/${currentId}/history`);
  if (!res.ok) {
    historyList.innerHTML = "";
    historyStatus.textContent = "Could not load history.";
    return;
  }
  const commits = await res.json();
  historyList.innerHTML = "";
  if (!commits.length) {
    historyList.innerHTML = "<p>No history yet.</p>";
    return;
  }
  const canRestore = currentPermissions ? currentPermissions.can_edit_body : false;
  commits.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "history-entry";
    const meta = document.createElement("div");
    meta.className = "history-entry-meta";
    meta.innerHTML = `<div class="history-entry-message">${c.message}</div>` +
      `<div class="history-entry-sub">${c.author} · ${formatHistoryDate(c.date)}</div>`;
    row.appendChild(meta);
    if (i > 0 && canRestore) { // the newest entry is already what's on disk — nothing to restore to
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Restore";
      btn.addEventListener("click", async () => {
        historyStatus.textContent = "Restoring…";
        const r = await fetch(`${itemsBaseUrl()}/${currentId}/history/${c.sha}/restore`, { method: "POST" });
        if (r.ok) {
          historyDialog.close();
          await openItem(currentId);
          await loadItems();
        } else {
          historyStatus.textContent = (await r.json()).detail || "Could not restore this version.";
        }
      });
      row.appendChild(btn);
    }
    historyList.appendChild(row);
  });
}

historyBtn.addEventListener("click", openHistoryDialog);
historyCloseBtn.addEventListener("click", () => historyDialog.close());
closeOnBackdropClick(historyDialog);

cm.on("change", (instance, changeObj) => {
  renderPreview();
  if (changeObj.origin === "setValue") return; // openItem() loading content, not a real edit
  // reset color explicitly — otherwise this neutral status can render in
  // whatever color a *previous* save cycle left behind (e.g. still red from
  // an earlier failed save), misreporting the current state as an error
  saveStatus.style.color = "var(--on-surface-variant)";
  saveStatus.textContent = "Unsaved changes…";
  scheduleAutosave();
});

const toggleReadBtn = document.getElementById("toggle-read");
const headerPanel = document.getElementById("header-panel");

function setReadMode(isReadMode) {
  editorPane.classList.toggle("hidden", isReadMode);
  // the header (owner/status/tags/assigned_to/parent) is only ever shown —
  // and editable — while reading; it's hidden while editing the body, since
  // it's no longer part of the raw text you're editing there at all
  headerPanel.hidden = !isReadMode;
  // icon shows the destination of the click, same as the old label did
  // ("Edit" while reading, "Read mode" while editing)
  const label = isReadMode ? "Switch to edit mode" : "Switch to read mode";
  toggleReadBtn.querySelector(".material-symbols-outlined").textContent = isReadMode ? "edit" : "visibility";
  toggleReadBtn.title = label;
  toggleReadBtn.setAttribute("aria-label", label);
  cm.refresh();
}

toggleReadBtn.addEventListener("click", () => {
  setReadMode(!editorPane.classList.contains("hidden"));
});

const fullscreenBtn = document.getElementById("fullscreen-btn");

function setFullscreen(on) {
  editorDialog.classList.toggle("fullscreen", on);
  const label = on ? "Exit fullscreen" : "Enter fullscreen";
  fullscreenBtn.querySelector(".material-symbols-outlined").textContent = on ? "fullscreen_exit" : "fullscreen";
  fullscreenBtn.title = label;
  fullscreenBtn.setAttribute("aria-label", label);
  // dialog size just changed — CodeMirror caches its own layout and goes
  // blank/misaligned until told to remeasure
  requestAnimationFrame(() => cm.refresh());
}

fullscreenBtn.addEventListener("click", () => {
  setFullscreen(!editorDialog.classList.contains("fullscreen"));
});

document.getElementById("editor-close").addEventListener("click", () => editorDialog.close());

// --- header panel / content split: draggable, remembered per browser ---
// Same #editor-body layout for both tasks and notes, so one resizer covers
// both — there's nothing type-specific to wire up separately.
const editorBody = document.getElementById("editor-body");
const editorResizer = document.getElementById("editor-resizer");
const HEADER_PANEL_WIDTH_KEY = "praxis-header-panel-pct";
const DEFAULT_HEADER_PANEL_PCT = 25;

function applyHeaderPanelWidth(pct) {
  headerPanel.style.flexBasis = `${pct}%`;
}

function loadHeaderPanelWidth() {
  const stored = Number(localStorage.getItem(HEADER_PANEL_WIDTH_KEY));
  applyHeaderPanelWidth(stored > 0 && stored < 100 ? stored : DEFAULT_HEADER_PANEL_PCT);
}
loadHeaderPanelWidth();

editorResizer.addEventListener("mousedown", (e) => {
  e.preventDefault();
  editorResizer.classList.add("dragging");
  const bodyRect = editorBody.getBoundingClientRect();

  function onMove(moveEvent) {
    const pct = ((moveEvent.clientX - bodyRect.left) / bodyRect.width) * 100;
    applyHeaderPanelWidth(Math.min(60, Math.max(15, pct)));
    // dragging the split resizes #panes underneath CodeMirror's feet, same
    // as a fullscreen toggle — it needs telling to remeasure or it goes
    // blank/misaligned until something else forces a refresh
    cm.refresh();
  }
  function onUp() {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    editorResizer.classList.remove("dragging");
    const finalPct = parseFloat(headerPanel.style.flexBasis) || DEFAULT_HEADER_PANEL_PCT;
    localStorage.setItem(HEADER_PANEL_WIDTH_KEY, String(finalPct));
  }
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
});

function closeOnBackdropClick(dialogEl) {
  // A click event's target is the dialog element itself only when it lands
  // on the ::backdrop — any click on the dialog's own content lands on some
  // descendant instead, so this needs no rect math (which broke when a
  // click handler resized the dialog synchronously before this one ran,
  // e.g. toggling fullscreen off — the rect it read back was already the
  // new, smaller one, so the original click point looked like it fell
  // outside and closed the whole dialog instead of just shrinking it).
  dialogEl.addEventListener("click", (e) => {
    if (e.target === dialogEl) dialogEl.close();
  });
}
closeOnBackdropClick(editorDialog);

// just the identifying strip — status/tags/owner/assigned_to now live in the
// editable header panel below the topbar instead (see renderHeaderPanel)
function renderMetaSummary(meta) {
  if (!meta) {
    metaSummary.textContent = "Select an item";
    return;
  }
  metaSummary.innerHTML =
    `<span class="meta-title type-title-m">${meta.title || meta.id}</span>` +
    `<span class="meta-id">${meta.type} · ${meta.id}</span>`;
}

// --- header panel: status/due_date/assigned_to/tags/parent, structured —
// shown (and, for the owner, editable) only in read mode; see setReadMode.
// Editing through form fields instead of raw YAML means the server never
// has to compare two frontmatter blobs to guess what changed (see
// permissions.py) — it's a dedicated, owner-gated endpoint. ---

const headerOwner = document.getElementById("header-owner");
const headerStatus = document.getElementById("header-status");
const headerDueDate = document.getElementById("header-due-date");
const headerAssignedLabel = document.getElementById("header-assigned-label");
const headerAssignedChips = document.getElementById("header-assigned-chips");
const headerAssignedAdd = document.getElementById("header-assigned-add");
const headerTagsChips = document.getElementById("header-tags-chips");
const headerTagsInput = document.getElementById("header-tags-input");
const headerTagsDatalist = document.getElementById("header-tags-datalist");
const headerParentSelect = document.getElementById("header-parent-select");
const headerParentOpenBtn = document.getElementById("header-parent-open");
const headerChildrenList = document.getElementById("header-children-list");
const headerRelatedNotesList = document.getElementById("header-related-notes-list");
const addSubtaskBtn = document.getElementById("add-subtask-btn");
const headerSaveStatus = document.getElementById("header-save-status");
// headerParentOpenBtn is deliberately excluded — navigating to the parent
// isn't an edit, so it stays enabled even for non-owners
const headerFields = [headerStatus, headerDueDate, headerAssignedAdd, headerTagsInput, headerParentSelect];

let currentItemMeta = null; // working copy — tags/assigned_to are added/removed here before each save
let headerEditable = false;
let projectTagsCache = [];

function tagsUrl() {
  if (!scope) return null;
  return scope.type === "project" ? `/api/projects/${scope.slug}/tags` : `/api/knowledge-bases/${scope.slug}/tags`;
}

// tags are pick-or-create, never free-typed onto the item directly: chips
// with a remove button, plus an input that autocompletes from every tag
// already used in this project/KB (datalist, minus tags already applied)
// but still accepts a brand new one
function renderTagChips() {
  const tags = currentItemMeta.tags || [];
  headerTagsChips.innerHTML = tags
    .map((t) => `<span class="tag-chip">${t}${headerEditable ? `<button type="button" data-tag="${t}">&times;</button>` : ""}</span>`)
    .join("");
  for (const btn of headerTagsChips.querySelectorAll("button")) {
    btn.addEventListener("click", () => {
      currentItemMeta.tags = (currentItemMeta.tags || []).filter((t) => t !== btn.dataset.tag);
      renderTagChips();
      saveHeader();
    });
  }
  headerTagsDatalist.innerHTML = projectTagsCache
    .filter((t) => !tags.includes(t))
    .map((t) => `<option value="${t}"></option>`)
    .join("");
}

function addTagFromInput() {
  const val = headerTagsInput.value.trim();
  headerTagsInput.value = "";
  if (!val) return;
  const tags = currentItemMeta.tags || [];
  if (tags.includes(val)) return;
  currentItemMeta.tags = [...tags, val];
  renderTagChips();
  saveHeader();
}

headerTagsInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addTagFromInput();
  }
});
headerTagsInput.addEventListener("change", addTagFromInput); // picking a <datalist> suggestion fires change, not keydown

// assigned_to, same pick-from-a-list shape as tags, but the source is fixed
// (project/kb members, via the shared attachUserPicker — see below) rather
// than pick-or-create, plus chips to remove.
function renderAssignedChips() {
  const assigned = currentItemMeta.assigned_to || [];
  headerAssignedChips.innerHTML = assigned
    .map((u) => `<span class="tag-chip">${avatarHTML(u)} ${displayNameFor(u)}${headerEditable ? `<button type="button" data-user="${u}">&times;</button>` : ""}</span>`)
    .join("");
  for (const btn of headerAssignedChips.querySelectorAll("button")) {
    btn.addEventListener("click", () => {
      currentItemMeta.assigned_to = (currentItemMeta.assigned_to || []).filter((u) => u !== btn.dataset.user);
      renderAssignedChips();
      saveHeader();
    });
  }
  headerAssignedAdd.value = "";
}

// attachUserPicker(headerAssignedAdd, ...) is wired up further down, right
// after memberCandidates() is defined — attachUserPicker itself is a hoisted
// function declaration so calling it here would be fine, but it closes over
// the module-level `userPickerRegistry` (a `const` declared much later in
// the file), and calling it before that line runs throws a TDZ
// ReferenceError that kills the rest of this script's initial evaluation
// (confirmed live: nothing past this point ever ran, including
// checkSession() at the bottom — no login dialog, no personal info, no nav).

// Points at a TASK either way, but what that link *means* differs by who's
// holding it: a task pointing at a task is a hierarchy (Main task/Subtasks),
// a note pointing at a task is just an association (Related task/Related
// notes) — same `parent` field and picker, different label depending on
// meta's own type, so it doesn't read as "this note is a subtask."
const headerParentLabel = document.getElementById("header-parent-label");

function renderParentControl(meta) {
  headerParentLabel.textContent = meta.type === "task" ? "Main task" : "Related task";
  const candidates = itemsCache.filter((i) => i.type === "task" && i.id !== meta.id);
  headerParentSelect.innerHTML = '<option value="">— none —</option>' +
    candidates.map((i) => `<option value="${i.id}"${i.id === meta.parent ? " selected" : ""}>${i.title || i.id}</option>`).join("");
  headerParentOpenBtn.hidden = !meta.parent;
}

headerParentOpenBtn.addEventListener("click", () => {
  if (headerParentSelect.value) openItem(headerParentSelect.value);
});

function renderLinkList(container, items, emptyHint) {
  container.innerHTML = items.length
    ? items.map((c) => `<button type="button" class="child-link" data-id="${c.id}">${c.status ? `[${c.status}] ` : ""}${c.title || c.id}</button>`).join("")
    : `<span class="header-empty-hint">${emptyHint}</span>`;
  for (const btn of container.querySelectorAll(".child-link")) {
    btn.addEventListener("click", () => openItem(btn.dataset.id));
  }
}

// subtasks: other TASKS whose Main task points back at this one
function renderChildrenList(meta) {
  const children = itemsCache.filter((i) => i.type === "task" && i.parent === meta.id);
  renderLinkList(headerChildrenList, children, "No subtasks yet");
  addSubtaskBtn.hidden = currentRoleForScope() === "guest";
}

// the reverse of a note's own Related task field: every note that points at
// this task — only a task ever has these (a note can't be another note's
// Related task target), so this section only ever renders for a task
function renderRelatedNotesList(meta) {
  const related = itemsCache.filter((i) => i.type === "knowledge" && i.parent === meta.id);
  renderLinkList(headerRelatedNotesList, related, "No related notes yet");
}

addSubtaskBtn.addEventListener("click", () => {
  // force type: "task" — this button only shows for a task, but scope's
  // own contentFilter (tasks vs knowledge) may be stale/unrelated to what's
  // actually open right now (e.g. reached via Home or a [[reference]])
  if (currentItemMeta) openCreateDialog({ parent: currentItemMeta.id, type: "task" });
});

async function renderHeaderPanel(meta, canEditHeader) {
  currentItemMeta = { ...meta, tags: [...(meta.tags || [])], assigned_to: [...(meta.assigned_to || [])] };
  headerEditable = canEditHeader;
  const isTask = meta.type === "task";
  document.getElementById("header-status-field").hidden = !isTask;
  document.getElementById("header-due-date-field").hidden = !isTask;
  // only a task can ever be someone's Main task, so only a task can have
  // subtasks — this section is always empty (and un-addable) on a note
  document.getElementById("header-children-field").hidden = !isTask;
  // same reasoning in reverse: only a task ever has related notes pointing
  // at it (a note is never itself pointed at)
  document.getElementById("header-related-notes-field").hidden = !isTask;

  // "Assigned to" (a task) / "Users" (a note) — same field either way, see
  // check_body_save: a task with an empty list means "owner only"; a note
  // with an empty list means "anyone with access", same permissive-when-
  // unset default this whole app already uses for project/kb membership
  headerAssignedLabel.textContent = isTask ? "Assigned to" : "Users";
  renderAssignedChips();

  headerOwner.innerHTML = meta.owner ? avatarHTML(meta.owner, "avatar-owner") + ` ${displayNameFor(meta.owner)}` : "";

  if (isTask) {
    const statuses = scope.type === "project" && scopeConfig ? scopeConfig.statuses : [];
    headerStatus.innerHTML = statuses
      .map((s) => `<option value="${s}"${s === meta.status ? " selected" : ""}>${s}</option>`)
      .join("");
    headerDueDate.value = meta.due_date || "";
    renderChildrenList(meta);
    renderRelatedNotesList(meta);
  }
  renderParentControl(meta);
  headerSaveStatus.textContent = "";

  const tagsRes = await fetch(tagsUrl());
  projectTagsCache = tagsRes.ok ? await tagsRes.json() : [];
  renderTagChips();

  for (const el of headerFields) el.disabled = !canEditHeader;
}

let headerSaveInFlight = false;
let headerSavePending = false;

async function saveHeader() {
  if (!currentId || !currentItemMeta) return;
  if (headerSaveInFlight) {
    // a field changed again while the previous header save was still in
    // transit — don't fire a second overlapping PUT (the slower response
    // could land last and silently undo the newer change); flush once more
    // right after the in-flight one finishes instead
    headerSavePending = true;
    return;
  }
  headerSaveInFlight = true;
  const payload = {
    tags: currentItemMeta.tags || [],
    parent: headerParentSelect.value || null,
    assigned_to: currentItemMeta.assigned_to || [], // "Assigned to" or "Users" — see renderHeaderPanel
  };
  if (currentItemMeta.type === "task") {
    payload.status = headerStatus.value;
    payload.due_date = headerDueDate.value || null;
  }
  headerSaveStatus.style.color = "var(--on-surface-variant)";
  headerSaveStatus.textContent = "Saving…";
  try {
    const res = await fetch(`${itemsBaseUrl()}/${currentId}/header`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      headerSaveStatus.style.color = "var(--success)";
      headerSaveStatus.textContent = "Saved.";
      await loadItems();
      const meta = itemsCache.find((m) => m.id === currentId);
      if (meta) {
        renderMetaSummary(meta);
        renderParentControl(meta); // the parent picker excludes items by id — itemsCache may have just changed
      }
    } else {
      const err = await res.json();
      headerSaveStatus.style.color = "var(--danger)";
      headerSaveStatus.textContent = err.detail || "Could not save header.";
    }
  } finally {
    headerSaveInFlight = false;
    if (headerSavePending) {
      headerSavePending = false;
      saveHeader();
    }
  }
}

for (const el of headerFields) {
  if (el !== headerTagsInput && el !== headerAssignedAdd) el.addEventListener("change", saveHeader); // tags/assigned_to commit through their own add/remove handlers
}

async function openItem(id) {
  // switching to a different item while the dialog is already open (a
  // parent/subtask/reference link, all added after body-only editing
  // existed) would otherwise silently drop whatever hadn't autosaved yet
  if (currentId && currentId !== id && autosaveTimer) {
    await saveCurrentItem();
  }

  const res = await fetch(`${itemsBaseUrl()}/${id}`);
  if (!res.ok) return;
  const data = await res.json();
  currentId = id;
  currentVersion = data.version;
  cancelPendingAutosave(); // e.g. reopening the same item right after a transfer-ownership call
  cm.setValue(data.body);
  saveStatus.textContent = "";
  hideEditorBanner(); // a stale notice from whatever was open before this doesn't belong on a fresh item
  const meta = itemsCache.find((m) => m.id === id) || data.metadata;
  renderMetaSummary(meta);

  // server-computed, not re-derived client-side: combines the scope-wide
  // editor-or-above check with the item's own owner/assigned_to rules, so
  // a button here can never show something the API would then 403/409 —
  // see _get_item's docstring in main.py
  const perms = data.permissions;
  currentPermissions = perms;
  const canEditBody = perms.can_edit_body;
  transferOwnerBtn.hidden = !perms.can_transfer_owner;
  renameFileBtn.hidden = !perms.can_rename || data.metadata.type !== "knowledge";
  deleteItemBtn.hidden = !perms.can_delete;
  attachNoteBtn.hidden = !canEditBody;
  saveBtn.hidden = !canEditBody;
  toggleReadBtn.hidden = !canEditBody;
  await renderHeaderPanel(data.metadata, perms.can_edit_header);
  transferOwnerBtn.onclick = async () => {
    const picked = await pickUser({
      title: `Transfer ownership of ${id} to…`,
      getCandidates: () => memberCandidates([data.metadata.owner]),
    });
    if (!picked) return;
    const r = await fetch(`${itemsBaseUrl()}/${id}/owner`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_owner: picked.id }),
    });
    if (r.ok) {
      await loadItems();
      openItem(id);
    } else {
      // inline next to the Owner field, not a toast: this happens while
      // editor-dialog is itself a modal, so a toast (outside any dialog)
      // would render behind its backdrop and never actually be seen
      headerSaveStatus.style.color = "var(--danger)";
      headerSaveStatus.textContent = (await r.json()).detail || "Could not transfer ownership.";
    }
  };
  deleteItemBtn.onclick = async () => {
    if (!confirm(`Delete ${data.filename}? This can't be undone.`)) return;
    const r = await fetch(`${itemsBaseUrl()}/${id}`, { method: "DELETE" });
    if (r.ok) {
      cancelPendingAutosave(); // nothing left to flush on close — the file's gone
      editorDialog.close();
      loadItems();
    } else {
      headerSaveStatus.style.color = "var(--danger)";
      headerSaveStatus.textContent = (await r.json()).detail || "Could not delete.";
    }
  };
  renameFileBtn.onclick = async () => {
    const newName = prompt("Rename file to (no extension needed):", data.filename);
    if (!newName || !newName.trim() || newName.trim() === data.filename) return;
    const r = await fetch(`${itemsBaseUrl()}/${id}/filename`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: newName.trim() }),
    });
    if (r.ok) {
      headerSaveStatus.style.color = "var(--success)";
      headerSaveStatus.textContent = `Renamed to ${(await r.json()).filename}.md`;
    } else {
      headerSaveStatus.style.color = "var(--danger)";
      headerSaveStatus.textContent = (await r.json()).detail || "Could not rename file.";
    }
  };

  setFullscreen(false); // each item opens windowed; fullscreen isn't remembered across items
  editorDialog.showModal();
  // showModal() only flips the `open` attribute — the dialog isn't actually
  // laid out by the browser until the next frame. Calling cm.refresh() (or
  // rendering into panes that are still mid-transition) before that measures
  // a zero-size container and CodeMirror ends up rendering blank, so this
  // waits a frame first.
  requestAnimationFrame(() => {
    // tasks open read-only (glance at status/progress first); knowledge
    // opens ready to edit. Anyone without edit rights on this specific item
    // never gets an edit view at all, regardless of type.
    setReadMode(!canEditBody || data.metadata.type === "task");
    renderPreview();
  });

  startPresence(id);
}

saveBtn.addEventListener("click", saveCurrentItem);

// --- nav tree (Home / Projects / Note Bases, ClickUp-style) ---

// A group's own toggle row reads icon, then name, then the expand/collapse
// caret last — a fixed representative icon per *kind* of group (not yet a
// per-project custom one — that'd need its own picker/config field, not
// built here), in the same Material Symbols family as every other icon in
// the app rather than a raw emoji.
function groupToggleHTML(icon, name, expanded) {
  return `<span class="material-symbols-outlined icon-inline" aria-hidden="true">${icon}</span>` +
    `<span class="nav-item-label">${name}</span>` +
    `<span class="material-symbols-outlined icon-inline" aria-hidden="true">${expanded ? "expand_more" : "chevron_right"}</span>`;
}

function renderProjectsNav(projects) {
  navProjects.innerHTML = "";
  if (!projects.length) {
    navProjects.innerHTML = '<div class="nav-empty">none yet</div>';
    return;
  }
  for (const p of projects) {
    const group = document.createElement("div");
    group.className = "nav-group";
    const icon = p.icon || "folder";

    const header = document.createElement("div");
    header.className = "nav-group-header";

    const toggle = document.createElement("button");
    toggle.className = "nav-item nav-project-toggle";
    toggle.innerHTML = groupToggleHTML(icon, p.name || p.slug, true);

    const settingsBtn = document.createElement("button");
    settingsBtn.className = "nav-gear-btn";
    settingsBtn.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">settings</span>';
    settingsBtn.title = "Project settings";
    settingsBtn.setAttribute("aria-label", `${p.name || p.slug} settings`);
    settingsBtn.dataset.type = "project-settings";
    settingsBtn.dataset.slug = p.slug;
    settingsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      selectScope("project", p.slug, "settings");
    });

    header.append(toggle, settingsBtn);

    const children = document.createElement("div");
    children.className = "nav-children";

    const tasksBtn = document.createElement("button");
    tasksBtn.className = "nav-item nav-subitem";
    tasksBtn.textContent = "Tasks";
    tasksBtn.dataset.type = "project-tasks";
    tasksBtn.dataset.slug = p.slug;
    tasksBtn.addEventListener("click", () => selectScope("project", p.slug, "tasks"));

    const knowledgeBtn = document.createElement("button");
    knowledgeBtn.className = "nav-item nav-subitem";
    knowledgeBtn.textContent = "Notes";
    knowledgeBtn.dataset.type = "project-knowledge";
    knowledgeBtn.dataset.slug = p.slug;
    knowledgeBtn.addEventListener("click", () => selectScope("project", p.slug, "knowledge"));

    children.append(tasksBtn, knowledgeBtn);

    toggle.addEventListener("click", () => {
      children.hidden = !children.hidden;
      toggle.innerHTML = groupToggleHTML(icon, p.name || p.slug, !children.hidden);
    });

    group.append(header, children);
    navProjects.appendChild(group);
  }
}

function renderKbsNav(kbs) {
  navKbs.innerHTML = "";
  if (!kbs.length) {
    navKbs.innerHTML = '<div class="nav-empty">none yet</div>';
    return;
  }
  for (const k of kbs) {
    const group = document.createElement("div");
    group.className = "nav-group";
    const icon = k.icon || "menu_book";

    const header = document.createElement("div");
    header.className = "nav-group-header";

    const toggle = document.createElement("button");
    toggle.className = "nav-item nav-project-toggle";
    toggle.innerHTML = groupToggleHTML(icon, k.name, true);

    const settingsBtn = document.createElement("button");
    settingsBtn.className = "nav-gear-btn";
    settingsBtn.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">settings</span>';
    settingsBtn.title = "Note base settings";
    settingsBtn.setAttribute("aria-label", `${k.name} settings`);
    settingsBtn.dataset.type = "kb-settings";
    settingsBtn.dataset.slug = k.slug;
    settingsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      selectScope("kb", k.slug, "settings");
    });

    header.append(toggle, settingsBtn);

    const children = document.createElement("div");
    children.className = "nav-children";

    const notesBtn = document.createElement("button");
    notesBtn.className = "nav-item nav-subitem";
    notesBtn.textContent = "Notes";
    notesBtn.dataset.type = "kb-knowledge";
    notesBtn.dataset.slug = k.slug;
    notesBtn.addEventListener("click", () => selectScope("kb", k.slug, "knowledge"));

    children.append(notesBtn);

    toggle.addEventListener("click", () => {
      children.hidden = !children.hidden;
      toggle.innerHTML = groupToggleHTML(icon, k.name, !children.hidden);
    });

    group.append(header, children);
    navKbs.appendChild(group);
  }
}

// Refreshes just the nav tree's contents (e.g. after renaming a note base,
// so the new name shows up there) — unlike loadNav(), this never
// navigates anywhere, so it's safe to call while the user is looking at
// something other than Home.
async function refreshNavLists() {
  const user = currentUser();
  if (!user) {
    navProjects.innerHTML = "";
    navKbs.innerHTML = "";
    return;
  }
  const [projectsRes, kbsRes] = await Promise.all([
    fetch("/api/projects"),
    fetch("/api/knowledge-bases"),
  ]);
  const projects = projectsRes.ok ? await projectsRes.json() : [];
  const kbs = kbsRes.ok ? await kbsRes.json() : [];
  renderProjectsNav(projects);
  renderKbsNav(kbs);
  updateNavActive();
}

async function loadNav() {
  await refreshNavLists();
  selectHome();
}

newProjectBtn.addEventListener("click", async () => {
  const name = prompt("Project name:");
  if (!name || !name.trim()) return;
  const res = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  if (res.ok) {
    const created = await res.json();
    await refreshNavLists();
    selectScope("project", created.slug, "tasks");
  } else {
    alert((await res.json()).detail || "Could not create the project.");
  }
});

newKbBtn.addEventListener("click", async () => {
  const name = prompt("Note base name:");
  if (!name || !name.trim()) return;
  const res = await fetch("/api/knowledge-bases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name.trim() }),
  });
  if (res.ok) {
    const created = await res.json();
    await refreshNavLists();
    selectScope("kb", created.slug, "knowledge");
  } else {
    alert((await res.json()).detail || "Could not create the note base.");
  }
});

function updateNavActive() {
  document.querySelectorAll(".nav-item, .nav-gear-btn").forEach((el) => {
    const key = el.dataset.type ? `${el.dataset.type}:${el.dataset.slug}` : el.dataset.nav;
    el.classList.toggle("active", key === activeNav);
  });
}

navHome.addEventListener("click", selectHome);

function selectHome() {
  disconnectScopeEvents();
  scope = null;
  contentFilter = null;
  activeNav = "home";
  updateNavActive();
  mainTitle.textContent = "Home";
  homeFilter.hidden = false;
  viewToggle.hidden = true;
  notesViewToggle.hidden = true;
  notesBreadcrumb.hidden = true;
  newFolderBtn.hidden = true;
  newBtn.hidden = true;
  setView("home");
}

async function selectScope(type, slug, contentType) {
  scope = { type, slug };
  connectScopeEvents();
  const isSettings = contentType === "members" || contentType === "settings";
  contentFilter = isSettings ? null : contentType;
  activeNav = `${type === "project" ? `project-${contentType}` : `kb-${contentType}`}:${slug}`;
  currentId = null;
  currentVersion = null;
  currentNoteFolder = "";
  updateNavActive();
  homeFilter.hidden = true;
  const isNotes = contentType === "knowledge";
  viewToggle.hidden = contentType !== "tasks";
  notesViewToggle.hidden = !isNotes;
  notesBreadcrumb.hidden = !isNotes;

  scopeConfig = null;
  if (type === "project") {
    const res = await fetch(`/api/projects/${slug}/config`);
    scopeConfig = res.ok ? await res.json() : null;
  } else if (type === "kb") {
    const res = await fetch(`/api/knowledge-bases/${slug}/config`);
    scopeConfig = res.ok ? await res.json() : null;
  }
  const canEdit = currentRoleForScope() !== "guest";
  newFolderBtn.hidden = !isNotes || !canEdit;
  const displayName = scopeConfig && scopeConfig.name ? scopeConfig.name : slug;

  if (isSettings) {
    mainTitle.textContent = `${displayName} — ${contentType === "settings" ? "Settings" : "Members"}`;
    newBtn.hidden = true;
    setView("members");
  } else {
    mainTitle.textContent = `${displayName} — ${contentType === "tasks" ? "Tasks" : "Notes"}`;
    newBtn.hidden = !canEdit;
    setView(isNotes ? "notes-list" : "list");
    loadItems();
  }
}

const addMemberForm = document.getElementById("add-member-form");

// Members/Settings is shared between a project's own "Members" nav item
// and a note base's "Settings" (which also gets a name field — see
// scope-name-field) — same shape (a members dict of username -> role)
// either way now, just a different base URL and whether a name exists.
function scopeConfigUrl() {
  return scope.type === "project" ? `/api/projects/${scope.slug}/config` : `/api/knowledge-bases/${scope.slug}/config`;
}
function scopeMembersUrl() {
  return scope.type === "project" ? `/api/projects/${scope.slug}/members` : `/api/knowledge-bases/${scope.slug}/members`;
}

async function renderMembers() {
  membersList.innerHTML = "Loading…";
  const res = await fetch(scopeConfigUrl());
  if (!res.ok) {
    membersList.innerHTML = "Could not load members.";
    return;
  }
  const data = await res.json();
  scopeConfig = data; // keep the cached scope config (role, statuses) in sync
  membersList.innerHTML = "";
  const isAdmin = data.role === "admin";
  addMemberForm.hidden = !isAdmin;
  if (isAdmin) loadMemberDirectory();

  // both a project and a note base have a name/icon now (see kb.yml/
  // project.yml's own `name`/`icon`) — only the members list's shape
  // differed enough to need its own branch, above
  scopeSettingsFields.hidden = !isAdmin;
  if (isAdmin) {
    scopeNameInput.value = data.name;
    scopeIconSelect.value = data.icon;
    scopeIconPreview.textContent = data.icon;
  }

  // encryption is projects-only (see vault.py) — a note base's settings
  // panel never shows this section at all
  if (scope.type === "project") {
    await renderEncryptionSection(isAdmin);
  } else {
    scopeEncryptionFields.hidden = true;
  }

  const usernames = Object.keys(data.members);
  for (const username of usernames) {
    const role = data.members[username];
    const row = document.createElement("div");
    row.className = "member-row";
    row.innerHTML = avatarHTML(username) + `<span class="member-name">${displayNameFor(username)}</span>` +
      `<span class="badge badge-status">${role}</span>`;

    if (isAdmin) {
      const roleSelect = document.createElement("select");
      roleSelect.innerHTML = ["admin", "editor", "guest"]
        .map((r) => `<option value="${r}"${r === role ? " selected" : ""}>${r}</option>`)
        .join("");
      roleSelect.addEventListener("change", async () => {
        const r = await fetch(scopeMembersUrl(), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, role: roleSelect.value }),
        });
        if (r.ok) renderMembers();
        else alert((await r.json()).detail || "Could not update role.");
      });
      row.appendChild(roleSelect);

      const removeBtn = document.createElement("button");
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", async () => {
        const r = await fetch(`${scopeMembersUrl()}/${encodeURIComponent(username)}`, { method: "DELETE" });
        if (r.ok) renderMembers();
        else alert((await r.json()).detail || "Could not remove member.");
      });
      row.appendChild(removeBtn);
    }

    membersList.appendChild(row);
  }
  if (!usernames.length) {
    const label = scope.type === "kb" ? "note base" : "project";
    membersList.innerHTML = `<p>No members — this ${label} is open to anyone right now (see docs/design/schema.md#permissions).</p>`;
  }
}

// Whole-project encryption at rest (see vault.py's module docstring for the
// design this implements) — admin-only, shown inside the same panel as
// members/name/icon. None of lock-status/encrypt/lock ever touch a
// project's actual content (only project.yml and the vault registry), so
// none of them can come back 423 — but they go through the same wrapped
// fetch as everything else in the app for consistency.
async function renderEncryptionSection(isAdmin) {
  scopeEncryptionFields.hidden = !isAdmin;
  if (!isAdmin) return;
  const res = await fetch(`/api/projects/${scope.slug}/lock-status`);
  if (!res.ok) {
    encryptionStatusText.textContent = "Could not check encryption status.";
    encryptProjectBtn.hidden = true;
    lockProjectBtn.hidden = true;
    return;
  }
  const { encrypted, open } = await res.json();
  if (!encrypted) {
    encryptionStatusText.textContent = "This project's content isn't encrypted yet — anyone with server/disk access can read it directly.";
    encryptProjectBtn.hidden = false;
    lockProjectBtn.hidden = true;
  } else {
    encryptionStatusText.textContent = open
      ? "Encrypted — currently unlocked (it reseals itself after a period of inactivity, or you can lock it now)."
      : "Encrypted — currently locked. Opening its tasks/notes will ask for its password.";
    encryptProjectBtn.hidden = true;
    lockProjectBtn.hidden = !open;
  }
}

encryptProjectBtn.addEventListener("click", async () => {
  if (!confirm(`Encrypt "${scopeConfig.name || scope.slug}"? A password will be generated and shown once — save it, because there's no way to recover the content without it.`)) return;
  encryptProjectBtn.disabled = true;
  const res = await fetch(`/api/projects/${scope.slug}/encrypt`, { method: "POST" });
  encryptProjectBtn.disabled = false;
  if (!res.ok) {
    alert((await res.json()).detail || "Could not encrypt this project.");
    return;
  }
  const { secret } = await res.json();
  vaultSecretProjectName.textContent = scopeConfig.name || scope.slug;
  vaultSecretValue.textContent = secret;
  vaultSecretDialog.showModal();
  await renderEncryptionSection(true);
});

lockProjectBtn.addEventListener("click", async () => {
  lockProjectBtn.disabled = true;
  const res = await fetch(`/api/projects/${scope.slug}/lock`, { method: "POST" });
  lockProjectBtn.disabled = false;
  if (res.ok) {
    await renderEncryptionSection(true);
    showToast(`"${scopeConfig.name || scope.slug}" is locked.`);
  } else {
    alert((await res.json()).detail || "Could not lock this project.");
  }
});

document.getElementById("vault-secret-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(vaultSecretValue.textContent);
    showToast("Copied.");
  } catch {
    // clipboard API can be unavailable (e.g. non-HTTPS) — the value is
    // select-all styled (see #vault-secret-value in style.css) for a
    // manual copy either way
  }
});

document.getElementById("vault-secret-done").addEventListener("click", () => vaultSecretDialog.close());
closeOnBackdropClick(vaultSecretDialog);

scopeNameSaveBtn.addEventListener("click", async () => {
  const name = scopeNameInput.value.trim();
  if (!name || !scope) return;
  const r = await fetch(scopeConfigUrl(), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, icon: scopeIconSelect.value }),
  });
  if (r.ok) {
    const updated = await r.json();
    scopeConfig.name = updated.name; // renderMembers() re-fetches anyway, but the page title below needs it now
    mainTitle.textContent = `${updated.name} — Settings`;
    await refreshNavLists(); // the new name (and icon) show up in the nav tree too
    renderMembers();
  } else {
    alert((await r.json()).detail || "Could not rename.");
  }
});

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Same derivation as config.provision_user_from_email on the server, just
// so the username this suggests matches the account someone would already
// get auto-provisioned with on their first OIDC login (see PRAXIS.md) —
// purely a starting point, the admin can still edit it before submitting.
function suggestUsernameFromEmail(email) {
  return (email.split("@")[0] || "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "");
}

let memberDirectory = null;

// Everyone allowed through metroon (see GET /api/directory) — backs the
// add-member picker below so an admin can find someone instead of typing
// their email from memory. Loaded once per page load (the allowlist
// doesn't change often enough to justify refetching every time the
// Members panel opens).
async function loadMemberDirectory() {
  if (memberDirectory) return memberDirectory;
  const res = await fetch("/api/directory");
  memberDirectory = res.ok ? await res.json() : [];
  return memberDirectory;
}

// --- reusable "type to find a person" combobox ---------------------------
// One artifact behind every person-picker in the app: add-member,
// assigned-to, and transfer-ownership. It only ever renders/filters
// whatever candidate pool it's handed — project/kb membership
// (scopeConfig.members) and the metroon directory (GET /api/directory) are
// both already fetched from the server elsewhere, this doesn't re-fetch
// anything itself. A candidate is {id, primary, secondary?, avatarSeed}:
// `id` is what's handed to onSelect, `primary`/`secondary` are what's
// shown, `avatarSeed` is what avatarHTML() keys its photo/initials off of.
const userPickerRegistry = new WeakMap();

function attachUserPicker(input, { getCandidates, onSelect, maxResults = 8 }) {
  // pickUser() (see below) reattaches on the same <input> every time its
  // dialog opens — swap the live config instead of piling up a fresh menu
  // element and duplicate document-level listeners each time.
  const existing = userPickerRegistry.get(input);
  if (existing) {
    existing.getCandidates = getCandidates;
    existing.onSelect = onSelect;
    existing.maxResults = maxResults;
    return;
  }
  const config = { getCandidates, onSelect, maxResults };
  userPickerRegistry.set(input, config);

  const menu = document.createElement("div");
  menu.className = "user-picker-menu";
  menu.hidden = true;
  document.body.appendChild(menu);

  let activeIndex = -1;
  let currentMatches = [];

  function positionMenu() {
    const rect = input.getBoundingClientRect();
    menu.style.left = `${rect.left}px`;
    menu.style.top = `${rect.bottom + 4}px`;
    menu.style.width = `${Math.max(rect.width, 220)}px`;
  }

  function closeMenu() {
    menu.hidden = true;
    activeIndex = -1;
  }

  function renderMenu() {
    const query = input.value.trim().toLowerCase();
    const pool = config.getCandidates();
    currentMatches = (query
      ? pool.filter(
          (c) =>
            c.primary.toLowerCase().includes(query) ||
            (c.secondary || "").toLowerCase().includes(query) ||
            c.id.toLowerCase().includes(query)
        )
      : pool
    ).slice(0, config.maxResults);

    menu.innerHTML = currentMatches.length
      ? currentMatches
          .map(
            (c, i) =>
              `<div class="user-picker-option${i === activeIndex ? " active" : ""}" data-index="${i}">${avatarHTML(c.avatarSeed)}<span>${escapeAttr(c.primary)}${c.secondary ? ` <span class="muted">${escapeAttr(c.secondary)}</span>` : ""}</span></div>`
          )
          .join("")
      : '<div class="user-picker-empty">No matches</div>';
    for (const el of menu.querySelectorAll(".user-picker-option")) {
      el.addEventListener("mousedown", (e) => {
        e.preventDefault(); // keep the input from blurring (and the menu closing) before the click registers
        selectMatch(Number(el.dataset.index));
      });
    }
    positionMenu();
    menu.hidden = false;
  }

  function selectMatch(i) {
    const candidate = currentMatches[i];
    if (!candidate) return;
    closeMenu();
    config.onSelect(candidate);
  }

  input.addEventListener("input", () => {
    activeIndex = -1;
    renderMenu();
  });
  input.addEventListener("focus", renderMenu);
  input.addEventListener("keydown", (e) => {
    if (menu.hidden) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, currentMatches.length - 1);
      renderMenu();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      renderMenu();
    } else if (e.key === "Enter") {
      if (activeIndex >= 0) {
        e.preventDefault();
        selectMatch(activeIndex);
      }
    } else if (e.key === "Escape") {
      closeMenu();
    }
  });
  document.addEventListener("click", (e) => {
    if (e.target !== input && !menu.contains(e.target)) closeMenu();
  });
  window.addEventListener("resize", () => {
    if (!menu.hidden) positionMenu();
  });
}

// Current project/kb's members as picker candidates — the "is this person
// actually in that project" backend check the picker relies on is just
// scopeConfig itself, already fetched from GET .../config server-side.
function memberCandidates(excludeIds = []) {
  const members = scopeConfig ? Object.keys(scopeConfig.members) : [];
  return members
    .filter((u) => !excludeIds.includes(u))
    .map((u) => {
      const name = displayNameFor(u);
      return { id: u, primary: name, secondary: name !== u ? u : "", avatarSeed: u };
    });
}

attachUserPicker(headerAssignedAdd, {
  getCandidates: () => memberCandidates(currentItemMeta.assigned_to || []),
  onSelect: (candidate) => {
    currentItemMeta.assigned_to = [...(currentItemMeta.assigned_to || []), candidate.id];
    renderAssignedChips();
    saveHeader();
  },
});

// Everyone metroon allows, for picking someone to add as a new member (they
// may not have a praxis.md account — or even be a member of anything —
// yet, so this can't just be memberCandidates()).
function directoryCandidates() {
  return (memberDirectory || []).map((p) => ({
    id: p.email,
    primary: p.name || p.email,
    secondary: p.name ? p.email : "",
    avatarSeed: p.name || p.email,
  }));
}

// --- a small reusable dialog wrapper around attachUserPicker, for the
// cases (transfer ownership) that need an explicit confirm step rather
// than picking straight into an inline field ---
const userPickDialog = document.getElementById("user-pick-dialog");
const userPickForm = document.getElementById("user-pick-form");
const userPickInput = document.getElementById("user-pick-input");
const userPickTitle = document.getElementById("user-pick-title");
const userPickConfirm = document.getElementById("user-pick-confirm");
const userPickStatus = document.getElementById("user-pick-status");
document.getElementById("user-pick-cancel").addEventListener("click", () => userPickDialog.close());

function pickUser({ title, getCandidates }) {
  return new Promise((resolve) => {
    userPickTitle.textContent = title;
    userPickInput.value = "";
    userPickStatus.textContent = "";
    userPickConfirm.disabled = true;
    let picked = null;

    attachUserPicker(userPickInput, {
      getCandidates,
      onSelect: (candidate) => {
        picked = candidate;
        userPickInput.value = candidate.primary;
        userPickConfirm.disabled = false;
      },
    });
    // typing again after picking someone invalidates that pick — they have
    // to choose again (from the list) before Confirm re-enables
    function onInput() {
      picked = null;
      userPickConfirm.disabled = true;
    }

    function onSubmit(e) {
      e.preventDefault();
      if (!picked) return;
      cleanup();
      userPickDialog.close();
      resolve(picked);
    }
    function onClose() {
      cleanup();
      resolve(null);
    }
    function cleanup() {
      userPickInput.removeEventListener("input", onInput);
      userPickForm.removeEventListener("submit", onSubmit);
      userPickDialog.removeEventListener("close", onClose);
    }
    userPickInput.addEventListener("input", onInput);
    userPickForm.addEventListener("submit", onSubmit);
    userPickDialog.addEventListener("close", onClose);
    userPickDialog.showModal();
    userPickInput.focus();
  });
}

attachUserPicker(document.getElementById("add-member-email"), {
  getCandidates: directoryCandidates,
  onSelect: (candidate) => {
    const emailInput = document.getElementById("add-member-email");
    const usernameInput = document.getElementById("add-member-input");
    emailInput.value = candidate.id;
    if (!usernameInput.value.trim()) usernameInput.value = suggestUsernameFromEmail(candidate.id);
  },
});

addMemberForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("add-member-input");
  const emailInput = document.getElementById("add-member-email");
  const roleSelect = document.getElementById("add-member-role");
  const username = input.value.trim();
  const email = emailInput.value.trim();
  if (!username || !scope) return;
  const res = await fetch(scopeMembersUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, role: roleSelect.value, email: email || null }),
  });
  if (res.ok) {
    input.value = "";
    emailInput.value = "";
    renderMembers();
  } else {
    alert((await res.json()).detail || "Could not add member.");
  }
});

for (const btn of homeFilter.querySelectorAll("button")) {
  btn.addEventListener("click", () => {
    homeBucket = btn.dataset.bucket;
    for (const b of homeFilter.querySelectorAll("button")) b.classList.toggle("active", b === btn);
    renderHome();
  });
}

// today/this-week/others, based on the browser's local date — due_date is
// a plain YYYY-MM-DD with no time/timezone of its own
function dateBucket(dueDate) {
  if (!dueDate) return "others";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(dueDate + "T00:00:00");
  const diffDays = Math.round((due - today) / 86400000);
  if (diffDays === 0) return "today";
  if (diffDays > 0 && diffDays <= 6) return "this_week";
  return "others";
}

// Personal tasks live inside Home itself, not as a project in the sidebar
// (see config.is_personal_project) — this is the one button that creates
// one, since there's no project screen to reach a "+ New" button from.
async function addPersonalTask() {
  const res = await fetch("/api/me/personal-project");
  if (!res.ok) return;
  const personal = await res.json();
  scope = { type: "project", slug: personal.slug };
  contentFilter = "tasks";
  openCreateDialog({ type: "task" });
}

async function renderHome() {
  homeView.innerHTML = "<p>Loading…</p>";
  const res = await fetch("/api/me/tasks");
  if (!res.ok) {
    homeView.innerHTML = "<p>Could not load your tasks.</p>";
    return;
  }
  const groups = await res.json();
  homeView.innerHTML = "";

  const addRow = document.createElement("div");
  addRow.id = "home-add-personal-task";
  addRow.innerHTML = `<button type="button" class="btn-tonal" id="add-personal-task-btn">+ Add personal task</button>`;
  homeView.appendChild(addRow);
  document.getElementById("add-personal-task-btn").addEventListener("click", addPersonalTask);

  const filteredGroups = groups
    .map((g) => ({ project: g.project, name: g.name, tasks: g.tasks.filter((t) => dateBucket(t.due_date) === homeBucket) }))
    .filter((g) => g.tasks.length);
  if (!filteredGroups.length) {
    const empty = document.createElement("p");
    empty.textContent = "Nothing here.";
    homeView.appendChild(empty);
    return;
  }
  for (const group of filteredGroups) {
    const section = document.createElement("div");
    section.className = "home-project-group";
    section.innerHTML = `<h2>${group.name || group.project}</h2>`;
    for (const task of group.tasks) {
      const card = document.createElement("div");
      card.className = "item-card";
      const dueBit = task.due_date ? `<span class="item-meta">due ${task.due_date}</span>` : "";
      card.innerHTML = `<span class="item-title">${task.title || task.id}</span>${dueBit}` +
        badgesRowHTML(task) + peopleRowHTML(task);
      card.addEventListener("click", async () => {
        scope = { type: "project", slug: group.project };
        contentFilter = "tasks";
        const cfgRes = await fetch(`/api/projects/${group.project}/config`);
        scopeConfig = cfgRes.ok ? await cfgRes.json() : null;
        await loadItems();
        openItem(task.id);
      });
      section.appendChild(card);
    }
    homeView.appendChild(section);
  }
}

// --- list / kanban views ---

async function loadItems() {
  if (!scope) return;
  const res = await fetch(itemsBaseUrl());
  if (!res.ok) {
    itemsCache = [];
    listView.innerHTML = "";
    return;
  }
  itemsCache = await res.json();
  if (isNotesContext()) await loadNoteFolders();
  renderCurrentView();
}

function renderCurrentView() {
  if (currentView === "home") renderHome();
  else if (currentView === "kanban") renderKanban();
  else if (currentView === "members") renderMembers();
  else if (currentView === "notes-list") renderNotesList();
  else if (currentView === "notes-grid") renderNotesGrid();
  else renderListView();
}

// --- avatars: a real photo when the user has set one (see the profile
// dialog); otherwise the initials-in-a-circle placeholder ---

function avatarColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return `hsl(${Math.abs(hash) % 360}, 55%, 42%)`;
}

function initialsFor(username) {
  const displayName = displayNameFor(username);
  const parts = displayName.trim().split(/\s+/);
  return parts.length > 1
    ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    : displayName.slice(0, 2).toUpperCase();
}

function avatarHTML(name, extraClass) {
  const profile = userProfiles[name];
  const title = profile && profile.full_name ? `${profile.full_name} (${name})` : name;
  if (profile && profile.photo) {
    return `<span class="avatar avatar-photo ${extraClass || ""}" title="${title}"><img src="${profile.photo}" alt="${name}" /></span>`;
  }
  return `<span class="avatar ${extraClass || ""}" style="background:${avatarColor(name)}" title="${title}">${initialsFor(name)}</span>`;
}

function peopleHTML(item) {
  let html = "";
  if (item.owner) html += avatarHTML(item.owner, "avatar-owner");
  if (item.assigned_to && item.assigned_to.length) {
    html += `<span class="avatar-stack">${item.assigned_to.map((n) => avatarHTML(n)).join("")}</span>`;
  }
  return html;
}

function peopleRowHTML(item) {
  const html = peopleHTML(item);
  return html ? `<div class="card-people-row">${html}</div>` : "";
}

// Per-status color (config.status_color server-side) — null for a kb
// scope, which has no statuses/status_colors at all, or before scopeConfig
// has loaded; either way the caller falls back to the plain CSS class.
function statusColorFor(status) {
  return (scopeConfig && scopeConfig.status_colors && scopeConfig.status_colors[status]) || null;
}

// Picks readable text (this app's own on-surface dark, or white) for an
// arbitrary background hex — covers both the curated pastel defaults and
// whatever a project admin picks via the color input, without needing a
// paired "on-color" stored alongside every custom color.
function readableTextOn(hexColor) {
  const hex = hexColor.replace("#", "");
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.6 ? "#1c1b1f" : "#ffffff";
}

function badgesHTML(item) {
  const badges = [];
  if (item.status) {
    const bg = statusColorFor(item.status);
    const style = bg ? ` style="background:${bg};color:${readableTextOn(bg)}"` : "";
    badges.push(`<span class="badge badge-status"${style}>${item.status}</span>`);
  }
  for (const tag of item.tags || []) badges.push(`<span class="badge">${tag}</span>`);
  return badges.join("");
}

function badgesRowHTML(item) {
  const html = badgesHTML(item);
  return html ? `<div class="card-meta-row">${html}</div>` : "";
}

function makeItemCard(item, canEdit) {
  const card = document.createElement("div");
  card.className = "item-card";
  const dueBit = item.due_date ? `<span class="item-meta">due ${item.due_date}</span>` : "";
  card.innerHTML = `<span class="item-title">${item.title || item.id}</span>${dueBit}` +
    badgesRowHTML(item) + peopleRowHTML(item);
  card.addEventListener("click", () => openItem(item.id));
  if (item.type === "task" && canEdit) {
    card.draggable = true;
    card.addEventListener("dragstart", (e) => {
      card.classList.add("dragging");
      e.dataTransfer.setData("text/plain", item.id);
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
  }
  return card;
}

async function renderListView() {
  listView.innerHTML = "";
  const canEdit = currentRoleForScope() !== "guest";

  const statuses = scope.type === "project" && scopeConfig ? scopeConfig.statuses : [];
  const tasks = itemsCache.filter((i) => i.type === "task");
  for (const status of statuses) {
    const section = document.createElement("div");
    section.className = "status-section";
    const inStatus = tasks.filter((t) => t.status === status);
    section.innerHTML = `<h2>${status} (${inStatus.length})</h2>`;
    for (const task of inStatus) section.appendChild(makeItemCard(task, canEdit));

    if (canEdit) {
      const addRow = document.createElement("div");
      addRow.className = "status-add-row";
      addRow.textContent = "+ Add task";
      addRow.addEventListener("click", () => openCreateDialog({ status }));
      section.appendChild(addRow);

      section.addEventListener("dragover", (e) => {
        e.preventDefault();
        section.classList.add("drag-over");
      });
      section.addEventListener("dragleave", () => section.classList.remove("drag-over"));
      section.addEventListener("drop", async (e) => {
        e.preventDefault();
        section.classList.remove("drag-over");
        const taskId = e.dataTransfer.getData("text/plain");
        await moveTaskToStatus(taskId, status);
      });
    }

    listView.appendChild(section);
  }
}

// --- notes: List (Drive-style columns) / Grid (Drive-style tiles),
// organized into real folders on disk under knowledge/ (see _list_folders
// server-side) — folders are navigated in-place, not a separate route ---

function directSubfolders() {
  const prefix = currentNoteFolder ? `${currentNoteFolder}/` : "";
  return notesFoldersCache
    .filter((f) => (currentNoteFolder ? f.startsWith(prefix) && !f.slice(prefix.length).includes("/") : !f.includes("/")))
    .sort();
}

function notesInCurrentFolder() {
  return itemsCache
    .filter((i) => i.type === "knowledge" && (i.folder || "") === currentNoteFolder)
    .sort((a, b) => (a.title || a.id).localeCompare(b.title || b.id));
}

function renderNotesBreadcrumb() {
  const parts = currentNoteFolder ? currentNoteFolder.split("/") : [];
  let acc = "";
  const segments = [{ label: "All notes", path: "" }];
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    segments.push({ label: part, path: acc });
  }

  // one level up from wherever we are now — "" (root) if already at a
  // top-level folder; hidden entirely at the root, where there's no "up"
  const parentPath = parts.slice(0, -1).join("/");
  const backHTML = currentNoteFolder
    ? '<button type="button" id="notes-back-btn" class="btn-icon" title="Back" aria-label="Back to parent folder">' +
      '<span class="material-symbols-outlined" aria-hidden="true">arrow_back</span></button>'
    : "";

  notesBreadcrumb.innerHTML = backHTML + segments
    .map((s) => `<button type="button" class="breadcrumb-item" data-path="${s.path}">${s.label}</button>`)
    .join(" / ");

  const backBtn = document.getElementById("notes-back-btn");
  if (backBtn) {
    backBtn.addEventListener("click", () => {
      currentNoteFolder = parentPath;
      renderCurrentView();
    });
    makeFolderDropTarget(backBtn, parentPath); // drop-to-move-up-a-level too
  }
  for (const btn of notesBreadcrumb.querySelectorAll(".breadcrumb-item")) {
    btn.addEventListener("click", () => {
      currentNoteFolder = btn.dataset.path;
      renderCurrentView();
    });
    // dropping a note on a breadcrumb segment moves it there directly —
    // the one way to move something back up to a parent folder (or to the
    // root) while browsing a nested one, without leaving the current view
    makeFolderDropTarget(btn, btn.dataset.path);
  }
}

function folderTileClick(folderPath) {
  return () => {
    currentNoteFolder = folderPath;
    renderCurrentView();
  };
}

// --- notes drag-and-drop: move a note into a folder (List and Grid both
// call these on their own row/tile elements) — the server-side endpoint
// (PUT .../items/{id}/folder) already existed; this is its first UI
// trigger, mirroring the same dragstart/dragover/drop shape moveTaskToStatus
// already established for the Kanban/status-section drop targets ---

function makeNoteDraggable(el, note) {
  // mirrors _move_item's own owner check server-side — dragging is just the
  // affordance, not a second place to decide who's allowed to move a note
  if (note.owner !== currentUser()) return;
  el.draggable = true;
  el.addEventListener("dragstart", (e) => {
    el.classList.add("dragging");
    e.dataTransfer.setData("text/plain", note.id);
  });
  el.addEventListener("dragend", () => el.classList.remove("dragging"));
}

function makeFolderDropTarget(el, folderPath) {
  el.addEventListener("dragover", (e) => {
    e.preventDefault();
    el.classList.add("drag-over");
  });
  el.addEventListener("dragleave", () => el.classList.remove("drag-over"));
  el.addEventListener("drop", async (e) => {
    e.preventDefault();
    el.classList.remove("drag-over");
    const noteId = e.dataTransfer.getData("text/plain");
    await moveNoteToFolder(noteId, folderPath);
  });
}

async function moveNoteToFolder(noteId, folderPath) {
  const note = itemsCache.find((i) => i.id === noteId);
  if (!note || (note.folder || "") === folderPath) return; // dropped on its own folder — nothing to do
  const res = await fetch(`${itemsBaseUrl()}/${noteId}/folder`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder: folderPath }),
  });
  if (res.ok) {
    await loadItems();
  } else {
    const err = await res.json();
    showToast(err.detail || "Could not move note.", "error");
  }
}

// Server enforces "owner of every note inside" (see main.py's
// _delete_folder) — this just surfaces whatever it says rather than
// re-deriving the same rule client-side, same spirit as item permissions.
async function deleteFolder(folderPath, e) {
  e.stopPropagation();
  const name = folderPath.split("/").pop();
  if (!confirm(`Delete folder "${name}" and everything inside it? This can't be undone.`)) return;
  const res = await fetch(`${foldersUrl()}/${folderPath}`, { method: "DELETE" });
  if (res.ok) {
    if (currentNoteFolder === folderPath || currentNoteFolder.startsWith(`${folderPath}/`)) {
      currentNoteFolder = folderPath.split("/").slice(0, -1).join("/");
    }
    await loadNoteFolders();
    await loadItems();
  } else {
    showToast((await res.json()).detail || "Could not delete folder.", "error");
  }
}

function rowDeleteButtonHTML(label) {
  return `<button type="button" class="btn-icon row-delete-btn" title="${label}" aria-label="${label}">` +
    '<span class="material-symbols-outlined" aria-hidden="true">delete</span></button>';
}

// Mirrors deleteFolder's shape — server enforces "owner only" (see
// main.py's _delete_item), this just surfaces whatever it says. Same
// DELETE route the single-item editor's own delete button already uses.
async function deleteNoteItem(note, e) {
  e.stopPropagation();
  if (!confirm(`Delete "${note.title || note.id}"? This can't be undone.`)) return;
  const res = await fetch(`${itemsBaseUrl()}/${note.id}`, { method: "DELETE" });
  if (res.ok) {
    await loadItems();
  } else {
    showToast((await res.json()).detail || "Could not delete note.", "error");
  }
}

async function renderNotesList() {
  notesListView.innerHTML = "";
  renderNotesBreadcrumb();

  const table = document.createElement("div");
  table.className = "notes-table";
  table.innerHTML = '<div class="notes-table-header"><span>Name</span><span>Owner</span><span>Modified</span></div>';

  for (const folderPath of directSubfolders()) {
    const row = document.createElement("div");
    row.className = "notes-table-row";
    row.innerHTML = `<span class="notes-name-cell"><span class="material-symbols-outlined icon-inline" aria-hidden="true">folder</span> ${folderPath.split("/").pop()}${rowDeleteButtonHTML("Delete folder")}</span><span></span><span></span>`;
    row.addEventListener("click", folderTileClick(folderPath));
    row.querySelector(".row-delete-btn").addEventListener("click", (e) => deleteFolder(folderPath, e));
    makeFolderDropTarget(row, folderPath);
    table.appendChild(row);
  }
  for (const note of notesInCurrentFolder()) {
    const row = document.createElement("div");
    row.className = "notes-table-row";
    const isOwner = note.owner === currentUser();
    row.innerHTML =
      `<span class="notes-name-cell"><span class="material-symbols-outlined icon-inline" aria-hidden="true">description</span> ${note.title || note.id}${isOwner ? rowDeleteButtonHTML("Delete note") : ""}</span>` +
      `<span>${note.owner ? avatarHTML(note.owner, "avatar-owner") + " " + displayNameFor(note.owner) : ""}</span>` +
      `<span>${note.updated || ""}</span>`;
    row.addEventListener("click", () => openItem(note.id));
    if (isOwner) row.querySelector(".row-delete-btn").addEventListener("click", (e) => deleteNoteItem(note, e));
    makeNoteDraggable(row, note);
    table.appendChild(row);
  }
  notesListView.appendChild(table);
}

async function renderNotesGrid() {
  notesGridView.innerHTML = "";
  renderNotesBreadcrumb();

  const grid = document.createElement("div");
  grid.className = "notes-grid";

  for (const folderPath of directSubfolders()) {
    const tile = document.createElement("div");
    tile.className = "notes-tile notes-tile-folder";
    tile.innerHTML =
      `<div class="notes-tile-icon"><span class="material-symbols-outlined" aria-hidden="true">folder</span></div>` +
      `<div class="notes-tile-name">${folderPath.split("/").pop()}</div>` +
      rowDeleteButtonHTML("Delete folder");
    tile.addEventListener("click", folderTileClick(folderPath));
    tile.querySelector(".row-delete-btn").addEventListener("click", (e) => deleteFolder(folderPath, e));
    makeFolderDropTarget(tile, folderPath);
    grid.appendChild(tile);
  }
  for (const note of notesInCurrentFolder()) {
    const tile = document.createElement("div");
    tile.className = "notes-tile";
    const isOwner = note.owner === currentUser();
    tile.innerHTML =
      `<div class="notes-tile-titlebar">` +
      `<span class="notes-tile-icon"><span class="material-symbols-outlined" aria-hidden="true">description</span></span>` +
      `<span class="notes-tile-name">${note.title || note.id}</span>` +
      `</div>` +
      `<div class="notes-tile-preview">${note.excerpt || ""}</div>` +
      `<div class="notes-tile-footer">${note.owner ? avatarHTML(note.owner, "avatar-owner") + `<span class="notes-tile-owner-name">${displayNameFor(note.owner)}</span>` : ""}</div>` +
      (isOwner ? rowDeleteButtonHTML("Delete note") : "");
    tile.addEventListener("click", () => openItem(note.id));
    if (isOwner) tile.querySelector(".row-delete-btn").addEventListener("click", (e) => deleteNoteItem(note, e));
    makeNoteDraggable(tile, note);
    grid.appendChild(tile);
  }
  notesGridView.appendChild(grid);
}

newFolderBtn.addEventListener("click", async () => {
  const name = prompt("New folder name:");
  if (!name || !name.trim()) return;
  const path = currentNoteFolder ? `${currentNoteFolder}/${name.trim()}` : name.trim();
  const res = await fetch(foldersUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (res.ok) {
    await loadNoteFolders();
    renderCurrentView();
  } else {
    showToast((await res.json()).detail || "Could not create folder.", "error");
  }
});

// Admin-only, matching the server's own require_project_admin gate on
// PUT .../statuses/{status}/color — not worth letting an editor/guest see
// a control the API would just 403 anyway.
async function setStatusColor(status, color) {
  const res = await fetch(`/api/projects/${scope.slug}/statuses/${encodeURIComponent(status)}/color`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ color }),
  });
  if (res.ok) {
    scopeConfig.status_colors = (await res.json()).status_colors;
    renderCurrentView();
  } else {
    showToast((await res.json()).detail || "Could not save that color.", "error");
  }
}

async function renderKanban() {
  kanbanView.innerHTML = "";
  const canEdit = currentRoleForScope() !== "guest";
  const canManageColors = currentRoleForScope() === "admin";
  const statuses = scope.type === "project" && scopeConfig ? scopeConfig.statuses : [];
  const tasks = itemsCache.filter((i) => i.type === "task");
  for (const status of statuses) {
    const column = document.createElement("div");
    column.className = "kanban-column";
    column.dataset.status = status;
    const cardsWrap = document.createElement("div");
    cardsWrap.className = "kanban-cards";
    const inColumn = tasks.filter((t) => t.status === status);

    const bg = statusColorFor(status) || "transparent";
    const header = document.createElement("div");
    header.className = "kanban-column-header";
    header.style.background = bg;
    header.style.color = statusColorFor(status) ? readableTextOn(bg) : "var(--on-surface-variant)";
    header.innerHTML = `<h3>${status} (${inColumn.length})</h3>`;
    if (canManageColors) {
      const colorInput = document.createElement("input");
      colorInput.type = "color";
      colorInput.className = "status-color-input";
      colorInput.value = statusColorFor(status) || "#ffffff";
      colorInput.title = `Color for "${status}"`;
      colorInput.addEventListener("input", () => { header.style.background = colorInput.value; header.style.color = readableTextOn(colorInput.value); });
      colorInput.addEventListener("change", () => setStatusColor(status, colorInput.value));
      header.appendChild(colorInput);
    }
    column.appendChild(header);

    for (const task of inColumn) {
      const card = document.createElement("div");
      card.className = "kanban-card";
      card.draggable = canEdit;
      card.dataset.id = task.id;
      const dueBit = task.due_date ? `<span class="card-meta">due ${task.due_date}</span>` : "";
      card.innerHTML = `${task.title || task.id}${dueBit}` + badgesRowHTML(task) + peopleRowHTML(task);
      card.addEventListener("click", () => openItem(task.id));
      if (canEdit) {
        card.addEventListener("dragstart", (e) => {
          card.classList.add("dragging");
          e.dataTransfer.setData("text/plain", task.id);
        });
        card.addEventListener("dragend", () => card.classList.remove("dragging"));
      }
      cardsWrap.appendChild(card);
    }

    if (canEdit) {
      const addRow = document.createElement("div");
      addRow.className = "status-add-row";
      addRow.textContent = "+ Add";
      addRow.addEventListener("click", () => openCreateDialog({ status }));
      cardsWrap.appendChild(addRow);
    }

    column.appendChild(cardsWrap);

    if (canEdit) {
      column.addEventListener("dragover", (e) => {
        e.preventDefault();
        column.classList.add("drag-over");
      });
      column.addEventListener("dragleave", () => column.classList.remove("drag-over"));
      column.addEventListener("drop", async (e) => {
        e.preventDefault();
        column.classList.remove("drag-over");
        const taskId = e.dataTransfer.getData("text/plain");
        await moveTaskToStatus(taskId, status);
      });
    }

    kanbanView.appendChild(column);
  }
}

async function moveTaskToStatus(taskId, newStatus) {
  // itemsCache already carries the full frontmatter (see _list_items server
  // side), so the header endpoint's full-replace contract just needs that
  // plus the one field being dragged-and-dropped into a new value
  const item = itemsCache.find((i) => i.id === taskId);
  if (!item || item.status === newStatus) return;

  const saveRes = await fetch(`${itemsBaseUrl()}/${taskId}/header`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      status: newStatus,
      due_date: item.due_date || null,
      tags: item.tags || [],
      assigned_to: item.assigned_to || [],
      parent: item.parent || null,
    }),
  });
  if (saveRes.ok) {
    await loadItems();
  } else {
    const err = await saveRes.json();
    showToast(err.detail || "Could not change status.", "error");
  }
}

function setView(view) {
  currentView = view;
  homeView.hidden = view !== "home";
  listView.hidden = view !== "list";
  kanbanView.hidden = view !== "kanban";
  membersView.hidden = view !== "members";
  notesListView.hidden = view !== "notes-list";
  notesGridView.hidden = view !== "notes-grid";
  for (const btn of viewToggle.querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.view === view);
  }
  for (const btn of notesViewToggle.querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.view === view);
  }
  renderCurrentView();
}

for (const btn of viewToggle.querySelectorAll("button")) {
  btn.addEventListener("click", () => setView(btn.dataset.view));
}
for (const btn of notesViewToggle.querySelectorAll("button")) {
  btn.addEventListener("click", () => setView(btn.dataset.view));
}

// --- creating a new item ---

const dialog = document.getElementById("new-dialog");
const newTemplate = document.getElementById("new-template");

let pendingCreateType = "task";
let pendingCreateStatus = null;
let pendingCreateParent = null;
let pendingCreateFolder = null;

// --- template picker + its create/edit dialog (a template is global, not
// scoped to a project/kb — see docs/design/schema.md#templates) ---

async function refreshTemplateOptions(selectName) {
  const res = await fetch("/api/templates");
  const data = await res.json();
  const options = data[pendingCreateType] || [];
  newTemplate.innerHTML = '<option value="">— none —</option>' +
    options.map((name) => `<option value="${name}">${name}</option>`).join("");
  if (selectName) newTemplate.value = selectName;
  templateEditBtn.disabled = !newTemplate.value;
}

async function openCreateDialog(opts) {
  if (!scope) return;
  opts = opts || {};
  document.getElementById("new-form-status").textContent = "";
  pendingCreateType = opts.type || (scope.type === "kb" ? "knowledge" : (contentFilter === "tasks" ? "task" : "knowledge"));
  pendingCreateStatus = opts.status || null;
  pendingCreateParent = opts.parent || null;
  pendingCreateFolder = opts.folder !== undefined ? opts.folder : (pendingCreateType === "knowledge" ? currentNoteFolder : null);

  await refreshTemplateOptions();

  dialog.showModal();
}

const templateEditorDialog = document.getElementById("template-editor-dialog");
const templateEditorTitle = document.getElementById("template-editor-title");
const templateEditorName = document.getElementById("template-editor-name");
const templateEditorBody = document.getElementById("template-editor-body");
const templateEditorStatus = document.getElementById("template-editor-status");
const templateEditBtn = document.getElementById("template-edit-btn");

newTemplate.addEventListener("change", () => { templateEditBtn.disabled = !newTemplate.value; });

document.getElementById("template-new-btn").addEventListener("click", () => {
  templateEditorTitle.textContent = `New ${pendingCreateType} template`;
  templateEditorName.value = "";
  templateEditorName.disabled = false;
  templateEditorBody.value = "";
  templateEditorStatus.textContent = "";
  templateEditorDialog.showModal();
  templateEditorName.focus();
});

templateEditBtn.addEventListener("click", async () => {
  if (!newTemplate.value) return;
  const name = newTemplate.value;
  templateEditorTitle.textContent = `Edit ${name}`;
  templateEditorName.value = name;
  // renaming here would just create a second file under the new name (the
  // server has no separate rename op — see config.set_template) rather
  // than rename the existing one, so this stays disabled to avoid that
  // surprise; delete + re-create under a new name if that's really wanted.
  templateEditorName.disabled = true;
  templateEditorBody.value = "";
  templateEditorStatus.textContent = "Loading…";
  templateEditorDialog.showModal();
  const res = await fetch(`/api/templates/${pendingCreateType}/${encodeURIComponent(name)}`);
  if (res.ok) {
    templateEditorBody.value = (await res.json()).body;
    templateEditorStatus.textContent = "";
  } else {
    templateEditorStatus.textContent = "Could not load this template.";
  }
});

document.getElementById("template-editor-cancel").addEventListener("click", () => templateEditorDialog.close());
closeOnBackdropClick(templateEditorDialog);

document.getElementById("template-editor-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = templateEditorName.value.trim();
  if (!name) {
    templateEditorStatus.textContent = "Name can't be empty.";
    return;
  }
  const res = await fetch(`/api/templates/${pendingCreateType}/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body: templateEditorBody.value }),
  });
  if (res.ok) {
    const saved = await res.json();
    templateEditorDialog.close();
    await refreshTemplateOptions(saved.name);
  } else {
    templateEditorStatus.textContent = (await res.json()).detail || "Could not save template.";
  }
});

newBtn.addEventListener("click", () => openCreateDialog());

document.getElementById("new-cancel").addEventListener("click", () => dialog.close());
closeOnBackdropClick(dialog);

document.getElementById("new-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    type: pendingCreateType,
    template: newTemplate.value || null,
  };
  if (pendingCreateStatus) body.status = pendingCreateStatus;
  if (pendingCreateParent) body.parent = pendingCreateParent;
  if (pendingCreateFolder) body.folder = pendingCreateFolder;
  const res = await fetch(itemsBaseUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.ok) {
    const created = await res.json();
    dialog.close();
    await loadItems();
    openItem(created.id);
  } else {
    const err = await res.json();
    // inline, not a toast: this dialog can now open nested inside the
    // editor dialog (creating a subtask) — a toast would render behind
    // both dialogs' backdrops (see closeOnBackdropClick's comment on
    // <dialog> stacking), same reason transfer-ownership's error stayed
    // as an inline/native prompt instead
    document.getElementById("new-form-status").textContent = err.detail || "Error while creating the item.";
  }
});

checkSession();
