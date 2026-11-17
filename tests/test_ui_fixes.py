"""Regression tests for issues found in the persona usability pass (2026-11-15)."""

from __future__ import annotations

import pytest
from conftest import make_draft
from fastapi.testclient import TestClient

from outreach import db, main, worker
from outreach.config import settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mode", "demo")
    monkeypatch.setattr(settings, "db_path", tmp_path / "ui.db")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(worker, "start", lambda: None)
    with TestClient(main.app) as c:
        yield c


def test_flash_message_survives_anchor_redirect(client):
    mid = make_draft("u1")
    r = client.post(
        f"/messages/{mid}/save",
        data={"text": "x" * 50, "next": f"/review#m{mid}"},
        follow_redirects=False,
    )
    loc = r.headers["location"]
    assert "?msg=" in loc and loc.index("?msg=") < loc.index("#")


def test_suggest_with_empty_form_is_friendly(client):
    r = client.post("/campaigns", data={"action": "suggest"})
    assert r.status_code == 200
    assert "Describe who you want to meet first" in r.text


def test_all_form_errors_shown_at_once(client):
    r = client.post(
        "/campaigns",
        data={
            "action": "create",
            "name": "",
            "segment": "",
            "pitch": "I'm <your name>",
            "query": "",
            "seeds": "",
        },
    )
    assert r.status_code == 200
    for needle in (
        "Give it a name",
        "Describe who you want to meet",
        "Add a search, a seed account, or both",
        "Replace the parts in",
    ):
        assert needle in r.text
    assert 'aria-invalid="true"' in r.text


def test_qualify_message_matches_result(client):
    cid = db.ex(
        "INSERT INTO campaigns(name, segment, pitch, min_score, created_at)"
        " VALUES('c','robotics founders','I run a community.',90,?)",
        db.now(),
    )
    pid = db.ex(
        "INSERT INTO prospects(campaign_id, x_user_id, username, name, bio, evidence, stage,"
        " score, created_at)"
        " VALUES(?, '101', 'demo_linh_robotics', 'Linh', 'robots', ?, 'screened', 60, ?)",
        cid,
        '[{"id":"bio","kind":"bio","text":"Co-founder building robot arms","created_at":""}]',
        db.now(),
    )
    r = client.post(f"/prospects/{pid}/qualify", follow_redirects=False)
    stage = db.q1("SELECT stage FROM prospects WHERE id=?", pid)["stage"]
    msg = r.headers["location"]
    if stage == "drafted":
        assert "draft" in msg.lower()
    else:
        assert "below" in msg.lower() or "skipped" in msg.lower()
        assert "Qualified" not in msg


def test_demo_mode_does_not_link_to_real_x(client):
    make_draft("u1")
    html = client.get("/review").text
    assert "https://x.com/user" not in html


def test_empty_review_after_finishing_says_all_done(client):
    mid = make_draft("u1")
    client.post(f"/messages/{mid}/approve", data={"text": ""})
    assert "All done" in client.get("/review").text
