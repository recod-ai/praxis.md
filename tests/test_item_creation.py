"""Title-at-creation and the no-template default body (app/main.py's
_create_item/_apply_title, app/config.py's DEFAULT_ITEM_BODY). Run with:
    uv run --with pytest pytest
"""

from fastapi.testclient import TestClient

from app import config, main


def _project():
    client = TestClient(main.app)
    with client:
        client.post("/api/login", json={"username": "owen"})
        slug = client.post("/api/projects", json={"name": "Creation lab"}).json()["slug"]
        return client, slug


def test_title_becomes_the_item_heading():
    client, slug = _project()
    with client:
        item_id = client.post(f"/api/projects/{slug}/items", json={"type": "task", "title": "Buy milk"}).json()["id"]
        item = client.get(f"/api/projects/{slug}/items/{item_id}").json()
        assert item["body"].startswith("# Buy milk\n")


def test_no_template_gets_the_generic_default_body():
    client, slug = _project()
    with client:
        item_id = client.post(f"/api/projects/{slug}/items", json={"type": "task", "title": "Plan the trip"}).json()["id"]
        body = client.get(f"/api/projects/{slug}/items/{item_id}").json()["body"]
        assert "## Summary" in body
        assert "## Notes" in body


def test_title_replaces_a_template_placeholder_heading():
    client, slug = _project()
    with client:
        item_id = client.post(
            f"/api/projects/{slug}/items",
            json={"type": "task", "title": "Does X cause Y?", "template": "research_question.md"},
        ).json()["id"]
        item = client.get(f"/api/projects/{slug}/items/{item_id}").json()
        assert item["body"].startswith("# Does X cause Y?\n")
        assert index_title(client, slug, item_id) == "Does X cause Y?"
        assert "## Validation Methodology" in item["body"]


def index_title(client, slug, item_id):
    items = client.get(f"/api/projects/{slug}/items").json()
    return next(i["title"] for i in items if i["id"] == item_id)


def test_missing_title_falls_back_to_the_id_like_before():
    # the UI's "Title" field is required — this only covers a raw API call
    # that skips it.
    client, slug = _project()
    with client:
        item_id = client.post(f"/api/projects/{slug}/items", json={"type": "task"}).json()["id"]
        assert index_title(client, slug, item_id) == item_id


def test_default_item_body_starts_with_a_heading():
    # _apply_title only overwrites a leading "#" line — if this stops being
    # a heading, a given title would get prepended above it instead of
    # replacing it, changing the shape asserted above.
    assert config.DEFAULT_ITEM_BODY.startswith("#")
