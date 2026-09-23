"""Archiving projects/note bases (app/config.py's is_project_archived/
set_project_archived + kb equivalents, main.py's /archive routes and
list_projects/list_knowledge_bases/my_tasks). Run with:
    uv run --with pytest pytest
"""

from fastapi.testclient import TestClient

from app import main


def _client():
    client = TestClient(main.app)
    client.__enter__()
    client.post("/api/login", json={"username": "owen"})
    return client


def test_archiving_a_project_moves_it_out_of_the_main_list_and_drops_its_tasks():
    client = _client()
    with client:
        slug = client.post("/api/projects", json={"name": "Old lab"}).json()["slug"]
        task_id = client.post(f"/api/projects/{slug}/items", json={"type": "task", "title": "Leftover", "assigned_to": ["owen"]}).json()["id"]

        listed = client.get("/api/projects").json()
        assert any(p["slug"] == slug and p["archived"] is False for p in listed)
        my_tasks = client.get("/api/me/tasks").json()
        assert any(g["project"] == slug for g in my_tasks)

        r = client.post(f"/api/projects/{slug}/archive", json={"archived": True})
        assert r.status_code == 200, r.text
        assert r.json()["archived"] is True

        listed = client.get("/api/projects").json()
        assert any(p["slug"] == slug and p["archived"] is True for p in listed)
        my_tasks = client.get("/api/me/tasks").json()
        assert not any(g["project"] == slug for g in my_tasks)

        # the task itself is untouched — archiving is purely a nav/Home thing
        item = client.get(f"/api/projects/{slug}/items/{task_id}").json()
        assert item["metadata"]["status"]


def test_unarchiving_a_project_brings_it_back():
    client = _client()
    with client:
        slug = client.post("/api/projects", json={"name": "Revived lab"}).json()["slug"]
        client.post(f"/api/projects/{slug}/archive", json={"archived": True})
        client.post(f"/api/projects/{slug}/archive", json={"archived": False})
        listed = client.get("/api/projects").json()
        assert any(p["slug"] == slug and p["archived"] is False for p in listed)


def test_only_an_admin_can_archive_a_project():
    client = _client()
    with client:
        slug = client.post("/api/projects", json={"name": "Guarded lab"}).json()["slug"]
        client.post(f"/api/projects/{slug}/members", json={"username": "ana", "role": "editor"})
        ana = TestClient(main.app)
        with ana:
            ana.post("/api/login", json={"username": "ana"})
            r = ana.post(f"/api/projects/{slug}/archive", json={"archived": True})
            assert r.status_code == 403


def test_a_personal_project_cannot_be_archived():
    client = _client()
    with client:
        slug = client.get("/api/me/personal-project").json()["slug"]
        r = client.post(f"/api/projects/{slug}/archive", json={"archived": True})
        assert r.status_code == 400


def test_archiving_a_kb_moves_it_out_of_the_main_list():
    client = _client()
    with client:
        slug = client.post("/api/knowledge-bases", json={"name": "Old wiki"}).json()["slug"]
        r = client.post(f"/api/knowledge-bases/{slug}/archive", json={"archived": True})
        assert r.status_code == 200, r.text
        assert r.json()["archived"] is True
        listed = client.get("/api/knowledge-bases").json()
        assert any(k["slug"] == slug and k["archived"] is True for k in listed)
