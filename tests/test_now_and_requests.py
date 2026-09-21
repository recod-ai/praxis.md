"""Now list and status-change requests (app/state_db.py + main.py's
/api/me/now and /api/me/requests routes). Run with:
    uv run --with pytest pytest
"""

import pytest
from fastapi.testclient import TestClient

from app import main, state_db


@pytest.fixture(scope="module")
def world():
    with TestClient(main.app) as owen:
        owen.post("/api/login", json={"username": "owen"})
        project = owen.post("/api/projects", json={"name": "Lab"}).json()["slug"]
        for name in ("ana", "bob"):
            owen.post(f"/api/projects/{project}/members", json={"username": name, "role": "editor"})
        ana, bob = TestClient(main.app), TestClient(main.app)
        ana.post("/api/login", json={"username": "ana"})
        bob.post("/api/login", json={"username": "bob"})
        yield {"project": project, "owen": owen, "ana": ana, "bob": bob}


def make_task(world, title, assigned=("ana",), status=None):
    body = {"type": "task", "title": title, "name": title, "assigned_to": list(assigned)}
    if status:
        body["status"] = status
    r = world["owen"].post(f"/api/projects/{world['project']}/items", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def mine(client, key, task_id):
    """This task's requests in one of /api/me/requests' lists."""
    return [r for r in client.get("/api/me/requests").json()[key] if r["task"]["id"] == task_id]


def status_of(world, task_id):
    return world["owen"].get(f"/api/projects/{world['project']}/items/{task_id}").json()["metadata"]["status"]


# --- Now ---


def test_now_any_visible_task_and_privacy(world):
    task = make_task(world, "Something", assigned=())
    p = world["project"]
    r = world["bob"].post("/api/me/now", json={"project": p, "task_id": task})  # not assigned to bob — allowed
    assert r.status_code == 200 and len(r.json()["items"]) == 1
    assert world["ana"].get("/api/me/now").json()["items"] == []  # private
    world["bob"].delete(f"/api/me/now/{p}/{task}")
    assert world["bob"].get("/api/me/now").json()["items"] == []


def test_now_is_idempotent_ordered_and_capped(world):
    p = world["project"]
    ids = [make_task(world, f"n{i}", assigned=()) for i in range(state_db.NOW_HARD_LIMIT + 1)]
    for t in ids[:2]:
        world["ana"].post("/api/me/now", json={"project": p, "task_id": t})
    world["ana"].post("/api/me/now", json={"project": p, "task_id": ids[0]})
    assert [i["task"]["id"] for i in world["ana"].get("/api/me/now").json()["items"]] == ids[:2]
    r = world["ana"].put("/api/me/now/order", json={"items": [{"project": p, "task_id": ids[1]}]})
    assert [i["task"]["id"] for i in r.json()["items"]] == [ids[1], ids[0]]
    for t in ids[2:state_db.NOW_HARD_LIMIT]:
        assert world["ana"].post("/api/me/now", json={"project": p, "task_id": t}).status_code == 200
    assert world["ana"].post("/api/me/now", json={"project": p, "task_id": ids[-1]}).status_code == 409
    for t in ids:
        world["ana"].delete(f"/api/me/now/{p}/{t}")


def test_now_drops_deleted_tasks(world):
    p = world["project"]
    task = make_task(world, "Temp", assigned=())
    world["ana"].post("/api/me/now", json={"project": p, "task_id": task})
    world["owen"].delete(f"/api/projects/{p}/items/{task}")
    assert world["ana"].get("/api/me/now").json()["items"] == []


def test_now_rejects_unknown_task_and_project(world):
    assert world["ana"].post("/api/me/now", json={"project": world["project"], "task_id": "nope"}).status_code == 404
    assert world["ana"].post("/api/me/now", json={"project": "nope", "task_id": "x"}).status_code == 404


# --- requests ---


def test_only_assigned_non_owner_can_request(world):
    p, task = world["project"], make_task(world, "Guarded")
    ask = lambda c, to="in_progress": c.post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": to})
    assert ask(world["owen"]).status_code == 400  # owner: change it directly
    assert ask(world["bob"]).status_code == 403  # not assigned
    assert ask(world["ana"], "nonsense").status_code == 400
    assert ask(world["ana"], status_of(world, task)).status_code == 400  # already there
    assert ask(world["ana"]).status_code == 200


def test_visibility_is_owner_and_requester_only(world):
    p, task = world["project"], make_task(world, "Visible")
    world["ana"].post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": "review", "note": "please"})
    got = mine(world["owen"], "received", task)
    assert len(got) == 1 and world["owen"].get("/api/me/requests").json()["attention"] >= 1
    assert len(mine(world["ana"], "sent", task)) == 1
    other = world["bob"].get("/api/me/requests").json()
    assert other["received"] == [] and other["sent"] == []
    rid = got[0]["id"]
    assert world["bob"].post(f"/api/me/requests/{rid}/accept").status_code == 404  # not a hint that it exists
    assert world["ana"].post(f"/api/me/requests/{rid}/accept").status_code == 404  # requester can't self-approve
    assert status_of(world, task) != "review"


def test_accept_moves_the_task_and_clears_the_request(world):
    p, task = world["project"], make_task(world, "Accepted")
    world["ana"].post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": "review"})
    rid = mine(world["owen"], "received", task)[0]["id"]
    r = world["owen"].post(f"/api/me/requests/{rid}/accept")
    assert r.status_code == 200 and mine(world["owen"], "received", task) == []
    assert status_of(world, task) == "review"
    assert mine(world["ana"], "sent", task) == []


def test_requester_never_writes_the_header(world):
    p, task = world["project"], make_task(world, "Header")
    before = status_of(world, task)
    world["ana"].post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": "done"})
    r = world["ana"].put(f"/api/projects/{p}/items/{task}/header", json={"status": "done", "assigned_to": ["ana"]})
    assert r.status_code == 403
    assert status_of(world, task) == before


def test_reject_keeps_it_for_the_requester_until_dismissed(world):
    p, task = world["project"], make_task(world, "Rejected")
    world["ana"].post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": "done"})
    rid = mine(world["owen"], "received", task)[0]["id"]
    after = world["owen"].post(f"/api/me/requests/{rid}/reject", json={"note": "not yet"}).json()
    assert [r for r in after["received"] if r["task"]["id"] == task] == []
    sent = mine(world["ana"], "sent", task)
    assert sent[0]["state"] == "rejected" and sent[0]["decision_note"] == "not yet"
    assert world["owen"].delete(f"/api/me/requests/{rid}").status_code == 404  # only the requester dismisses
    world["ana"].delete(f"/api/me/requests/{rid}")
    assert mine(world["ana"], "sent", task) == []


def test_new_request_replaces_the_old_one(world):
    p, task = world["project"], make_task(world, "Replace")
    url = f"/api/projects/{p}/items/{task}/status-requests"
    world["ana"].post(url, json={"to_status": "review"})
    world["ana"].post(url, json={"to_status": "done"})
    sent = mine(world["ana"], "sent", task)
    assert len(sent) == 1 and sent[0]["to_status"] == "done"
    world["ana"].delete(f"/api/me/requests/{sent[0]['id']}")


def test_request_goes_stale_when_the_owner_moves_it_anyway(world):
    p, task = world["project"], make_task(world, "Stale")
    world["ana"].post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": "review"})
    world["owen"].put(f"/api/projects/{p}/items/{task}/header", json={"status": "review", "assigned_to": ["ana"]})
    assert mine(world["owen"], "received", task) == []
    assert mine(world["ana"], "sent", task) == []


def test_transferred_owner_receives_the_request(world):
    p, task = world["project"], make_task(world, "Transfer")
    world["ana"].post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": "review"})
    world["owen"].put(f"/api/projects/{p}/items/{task}/owner", json={"new_owner": "bob"})
    assert len(mine(world["bob"], "received", task)) == 1
    assert mine(world["owen"], "received", task) == []


def test_status_edit_renames_or_drops_pending_requests(world):
    owen, ana = world["owen"], world["ana"]
    p = owen.post("/api/projects", json={"name": "Statuses lab"}).json()["slug"]
    owen.post(f"/api/projects/{p}/members", json={"username": "ana", "role": "editor"})
    task = owen.post(f"/api/projects/{p}/items", json={"type": "task", "title": "t", "name": "t", "assigned_to": ["ana"], "status": "proposal"}).json()["id"]
    ana.post(f"/api/projects/{p}/items/{task}/status-requests", json={"to_status": "review"})
    entries = [{"name": s_, "original": s_} for s_ in owen.get(f"/api/projects/{p}/config").json()["statuses"]]
    for e in entries:
        if e["name"] == "review":
            e["name"] = "checking"
    assert owen.put(f"/api/projects/{p}/statuses", json={"statuses": entries}).status_code == 200
    assert mine(owen, "received", task)[0]["to_status"] == "checking"
    entries = [e for e in entries if e["name"] != "checking"]
    assert owen.put(f"/api/projects/{p}/statuses", json={"statuses": entries}).status_code == 200
    assert mine(owen, "received", task) == []
