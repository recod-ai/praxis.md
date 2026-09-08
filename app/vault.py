"""Whole-project encryption at rest (docs/design/schema.md#encryption).

The threat model here isn't an external attacker — it's whoever administers
this server (or its git remote/backups) casually browsing a student's
project just because it's sitting right there, in the one place they
already have full access to for other reasons. So the unit of protection is
a whole project, not a file or a field: sealed, a project is one opaque
`vault.enc` blob next to its (still plaintext, non-sensitive) `project.yml`
— nothing to grep, diff, or stumble into. Unsealed, it's an ordinary
directory (tasks/, knowledge/, its own .git) sitting in a non-obvious,
per-open, RAM-backed location, and every existing piece of the app
(storage.py, index.py, git_store.py, permissions.py) reads and writes it
exactly like it always has — none of them know encryption is involved.

A project's secret is a random token generated once (see generate_secret)
and shown to whoever unlocks/creates it — the server never stores it, only
a salt and a scrypt-derived key check it back against project.yml. Losing
the secret means losing the project's content; there's no recovery path,
by design (see the design conversation this implements).

Compression is deliberately skipped (tarfile mode "w", not "w:gz") — these
are small text files, and paying CPU for a few saved KB isn't worth the
latency on every seal/unseal.
"""

import io
import os
import secrets
import shutil
import tarfile
import threading
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import config

VAULT_FILENAME = "vault.enc"
KEY_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12
# Interactive scrypt cost (RFC 7914's "interactive" params) — sub-second to
# derive, which matters here since it happens synchronously on every unlock.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

IDLE_TIMEOUT_SECONDS = int(os.environ.get("PRAXIS_PROJECT_IDLE_TIMEOUT", str(30 * 60)))

_SCRATCH_ROOT = Path(os.environ.get("PRAXIS_SCRATCH_DIR", "/dev/shm/praxis" if Path("/dev/shm").is_dir() else ""))
if not _SCRATCH_ROOT.parts:
    import tempfile

    _SCRATCH_ROOT = Path(tempfile.gettempdir()) / "praxis-scratch"


class ProjectLocked(Exception):
    """Raised when something tries to read/write a project's content while
    it has no open, decrypted copy — caught once, at the API layer (see
    main.py's exception handler), and turned into a 423 the frontend reads
    as "prompt for this project's password"."""

    def __init__(self, slug: str):
        self.slug = slug
        super().__init__(f"Project '{slug}' is locked.")


class WrongSecret(Exception):
    pass


_registry: dict[str, dict] = {}  # slug -> {"path": Path, "last_used": float}
_registry_lock = threading.Lock()


def generate_secret() -> str:
    """A random per-project passphrase — never chosen by a person, never
    stored server-side, shown exactly once for whoever unlocks/creates the
    project to save somewhere of their own (a password manager, etc.)."""
    return secrets.token_urlsafe(24)


def _derive_key(secret: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_LEN, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(secret.encode("utf-8"))


def vault_path(slug: str) -> Path:
    return config.project_dir(slug) / VAULT_FILENAME


def has_vault(slug: str) -> bool:
    return vault_path(slug).exists()


def is_open(slug: str) -> bool:
    with _registry_lock:
        return slug in _registry


def active_dir(slug: str) -> Path | None:
    """The live, decrypted directory for an unlocked project, or None if
    it's currently sealed. Bumps its idle clock — any request that actually
    touches a project's content counts as activity, keeping it open."""
    with _registry_lock:
        entry = _registry.get(slug)
        if entry is None:
            return None
        entry["last_used"] = time.monotonic()
        return entry["path"]


def _new_scratch_dir(slug: str) -> Path:
    _SCRATCH_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _SCRATCH_ROOT / secrets.token_hex(16)  # unpredictable name — not the slug
    path.mkdir(mode=0o700)
    return path


def _seal_dir_into(source: Path, archive_path: Path, secret: str, salt: bytes) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:  # no compression — see module docstring
        for child in sorted(source.iterdir()):
            tar.add(child, arcname=child.name)
    key = _derive_key(secret, salt)
    nonce = secrets.token_bytes(NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, buf.getvalue(), None)
    tmp = archive_path.with_suffix(".tmp")
    tmp.write_bytes(nonce + ciphertext)
    tmp.replace(archive_path)


def _unseal_into(archive_path: Path, dest: Path, secret: str, salt: bytes) -> None:
    raw = archive_path.read_bytes()
    nonce, ciphertext = raw[:NONCE_LEN], raw[NONCE_LEN:]
    key = _derive_key(secret, salt)
    try:
        data = AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise WrongSecret("Wrong project password.")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        tar.extractall(dest, filter="data")


def encrypt_project(slug: str, git_init) -> str:
    """First-time setup: turn an existing plaintext project directory into a
    sealed vault. `git_init(scope_dir)` is called first so tasks/knowledge/
    get their own git history before being sealed away — see git_store's
    init_standalone_repo. Returns the generated secret; the caller (an admin
    action) shows it once and never sees it again."""
    scope_dir = config.project_dir(slug)
    if has_vault(slug):
        raise ValueError(f"Project '{slug}' is already encrypted.")

    git_init(scope_dir)

    secret = generate_secret()
    salt = secrets.token_bytes(SALT_LEN)
    to_seal = [p for p in ("tasks", "knowledge", ".git") if (scope_dir / p).exists()]
    staging = scope_dir / f".seal-staging-{secrets.token_hex(4)}"
    staging.mkdir()
    try:
        for name in to_seal:
            (scope_dir / name).rename(staging / name)
        _seal_dir_into(staging, vault_path(slug), secret, salt)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for name in to_seal:
        path = scope_dir / name
        if path.exists():
            shutil.rmtree(path)

    config.set_project_vault_salt(slug, salt.hex())
    return secret


def unlock_project(slug: str, secret: str) -> Path:
    """Decrypt the project into a fresh scratch directory and register it
    as open. Idempotent — unlocking an already-open project just returns
    its existing live path without re-checking the secret, so a second tab
    doesn't need to re-enter a password someone already provided."""
    existing = active_dir(slug)
    if existing is not None:
        return existing

    salt_hex = config.get_project_vault_salt(slug)
    if not salt_hex:
        raise ValueError(f"Project '{slug}' has no vault salt on record.")
    dest = _new_scratch_dir(slug)
    try:
        _unseal_into(vault_path(slug), dest, secret, bytes.fromhex(salt_hex))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    with _registry_lock:
        # storing the secret here (not just the decrypted path) is what
        # lets sweep_idle()/lock_project() reseal without asking again —
        # this only runs once per open (active_dir() above short-circuits
        # a second unlock of an already-open project), so it can't clobber
        # a live entry with a stale secret.
        _registry[slug] = {"path": dest, "last_used": time.monotonic(), "secret": secret}
    return dest


def lock_project(slug: str, on_before_wipe=None) -> None:
    """Reseal an open project (idle timeout, explicit lock, or logout) —
    re-encrypts its current state (including whatever new git commits were
    made while it was open) back into vault.enc, then wipes the scratch
    copy. A no-op if it isn't currently open. `on_before_wipe(path)`, if
    given, runs right before the scratch directory is removed — main.py
    uses it to drop that path's cached git.Repo handle (see
    git_store.forget_repo) so nothing keeps a reference into a directory
    that's about to stop existing."""
    with _registry_lock:
        entry = _registry.pop(slug, None)
    if entry is None:
        return
    salt_hex = config.get_project_vault_salt(slug)
    secret = entry.get("secret")
    path = entry["path"]
    try:
        if salt_hex and secret:
            _seal_dir_into(path, vault_path(slug), secret, bytes.fromhex(salt_hex))
    finally:
        if on_before_wipe:
            on_before_wipe(path)
        shutil.rmtree(path, ignore_errors=True)


def sweep_idle(on_before_wipe=None) -> list[str]:
    """Reseal every project that's been open longer than the idle timeout
    with no activity — called periodically from main.py's lifespan.
    Returns the slugs it closed, purely for logging."""
    now = time.monotonic()
    with _registry_lock:
        stale = [slug for slug, entry in _registry.items() if now - entry["last_used"] > IDLE_TIMEOUT_SECONDS]
    for slug in stale:
        lock_project(slug, on_before_wipe=on_before_wipe)
    return stale


def lock_all(on_before_wipe=None) -> None:
    with _registry_lock:
        slugs = list(_registry.keys())
    for slug in slugs:
        lock_project(slug, on_before_wipe=on_before_wipe)
