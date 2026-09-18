"""Thin Forgejo (Gitea-compatible) HTTP client shared by archeion_mirror.py
(read-only project mirrors) and submodules.py (real, writable submodule
repos) — each configures its own base URL, org, and credentials; this
module has no opinion on which, or on how many Forgejo instances/orgs a
deployment ends up using.
"""

from urllib.parse import urlsplit, urlunsplit

import httpx


def api(base_url: str, method: str, path: str, auth: tuple[str, str], **kw) -> httpx.Response:
    return httpx.request(method, f"{base_url}/api/v1{path}", auth=auth, timeout=10, **kw)


def authenticated_clone_url(base_url: str, org: str, repo: str, username: str, password: str) -> str:
    parts = urlsplit(base_url)
    netloc = f"{username}:{password}@{parts.netloc}"
    return urlunsplit((parts.scheme, netloc, f"/{org}/{repo}.git", "", ""))


def login_for_email(email: str) -> str:
    return email.split("@")[0].strip().lower()
