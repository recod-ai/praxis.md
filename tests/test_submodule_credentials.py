import subprocess
from pathlib import Path

from app import git_store


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_credentials_in_the_clone_url_never_stay_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    for key, value in (("user.name", "t"), ("user.email", "t@t")):
        monkeypatch.setenv("GIT_AUTHOR_NAME" if key == "user.name" else "GIT_AUTHOR_EMAIL", value)
        monkeypatch.setenv("GIT_COMMITTER_NAME" if key == "user.name" else "GIT_COMMITTER_EMAIL", value)

    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q")
    (remote / "a.txt").write_text("x")
    _git(remote, "add", "-A")
    _git(remote, "commit", "-q", "-m", "init")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    (work / "keep.txt").write_text("y")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")

    # the URL git actually clones from carries a "password"; what must stay recorded is the clean one
    secret_url = f"file://{remote}"
    clean = "https://forge.example/org/repo.git"
    git_store.add_submodule(work, "knowledge/Teste", secret_url, clean_url=clean)
    _git(work, "commit", "-q", "-m", "convert")

    assert clean in (work / ".gitmodules").read_text()
    assert str(remote) not in (work / ".gitmodules").read_text()
    assert _git(work, "config", "--get", "submodule.knowledge/Teste.url").strip() == clean
    assert _git(work / "knowledge/Teste", "config", "--get", "remote.origin.url").strip() == clean
    assert "a.txt" in _git(work, "ls-files", "--recurse-submodules")
    assert str(remote) not in _git(work, "log", "-p", "--all")
