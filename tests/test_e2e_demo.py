"""End-to-end in demo mode through the real web app and background worker."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from outreach import db, main
from outreach.config import settings


def wait(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "mode", "demo")
    monkeypatch.setattr(settings, "db_path", tmp_path / "e2e.db")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "min_seconds_between_sends", 0)
    with TestClient(main.app) as client:
        yield client
    main.worker.stop()
    main.worker._stop.clear()


def job_done(cid):
    j = db.q1("SELECT status FROM jobs WHERE campaign_id=? ORDER BY id DESC LIMIT 1", cid)
    return j is not None and j["status"] in ("done", "error")


def test_full_flow(app):
    r = app.post(
        "/campaigns",
        data={
            "action": "create",
            "name": "SEA builders",
            "segment": "Robotics and AI founders and researchers in Southeast Asia",
            "seeds": "@demo_sea_ai_hub",
            "query": "robotics OR LLM",
            "pitch": "I'm Hung. I run a small community for AI builders in Southeast Asia.",
            "max_posts": 50,
            "max_seed_following": 20,
            "min_score": 55,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    cid = int(r.headers["location"].split("/campaigns/")[1].split("?")[0])

    app.post(f"/campaigns/{cid}/run")
    assert wait(lambda: job_done(cid))
    assert db.q1("SELECT status FROM jobs WHERE campaign_id=?", cid)["status"] == "done"

    stages = {r["username"]: r["stage"] for r in db.q("SELECT username, stage FROM prospects")}
    # junk accounts must not get drafts; closed DMs and prior conversations are caught
    assert stages["demo_ai_news_daily"] not in ("drafted",)
    assert stages["demo_moonshot_crypto"] not in ("drafted",)
    drafts = db.q("SELECT id, username FROM messages WHERE status='draft'")
    assert len(drafts) >= 3
    assert "demo_closed_dm_dev" not in {d["username"] for d in drafts}

    assert app.get("/review").status_code == 200
    for d in drafts:
        app.post(f"/messages/{d['id']}/approve", data={"text": ""})
    assert wait(lambda: not db.q1("SELECT 1 FROM messages WHERE status IN ('approved','sending')"))

    sent = db.q("SELECT username FROM messages WHERE status='sent'")
    assert len(sent) >= 2
    assert len({s["username"] for s in sent}) == len(sent)

    app.post("/inbox/sync")
    assert wait(lambda: db.q1("SELECT 1 FROM messages WHERE replied_at IS NOT NULL"))

    # Running discovery again must never produce a second message to anyone already contacted
    app.post(f"/campaigns/{cid}/run")
    assert wait(
        lambda: (
            job_done(cid)
            and db.q1("SELECT COUNT(*) n FROM jobs WHERE campaign_id=?", cid)["n"] == 2
        )
    )
    dupes = db.q(
        "SELECT x_user_id, COUNT(*) n FROM messages WHERE status='sent'"
        " GROUP BY x_user_id HAVING n > 1"
    )
    assert dupes == []
    for page in ("/", "/outbox", "/settings", f"/campaigns/{cid}"):
        assert app.get(page).status_code == 200
