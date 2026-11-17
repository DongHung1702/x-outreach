"""Regression tests for issues found in the independent code review (2026-11-15)."""

from __future__ import annotations

import pytest
from conftest import EVIDENCE, GOOD_CITES, make_draft

from outreach import db, inbox, sender, validator, xapi

NO_LIMITS = {"daily_cap": 100, "min_gap": 0}


def status(mid: int) -> str:
    return str(db.q1("SELECT status FROM messages WHERE id=?", mid)["status"])


# 1. CSRF -------------------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from outreach import main, worker
    from outreach.config import settings

    monkeypatch.setattr(settings, "mode", "demo")
    monkeypatch.setattr(settings, "db_path", tmp_path / "w.db")
    monkeypatch.setattr(worker, "start", lambda: None)
    with TestClient(main.app) as c:
        yield c


def test_cross_site_post_is_rejected(client):
    r = client.post(
        "/settings/pause",
        data={"paused": "0"},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    r = client.post(
        "/settings/pause",
        data={"paused": "0"},
        headers={"Sec-Fetch-Site": "cross-site"},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_same_origin_post_is_allowed(client):
    r = client.post(
        "/settings/pause",
        data={"paused": "1"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_open_redirect_blocked(client):
    mid = make_draft("u1")
    r = client.post(
        f"/messages/{mid}/seen", data={"next": "https://evil.example/"}, follow_redirects=False
    )
    assert r.headers["location"].startswith("/")


# 2 / 6 / 7. Unexpected errors never strand or wedge the queue -----------------------------


def test_unexpected_send_exception_goes_to_needs_check(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = RuntimeError("boom")  # type: ignore[assignment]
    sender.send_next(fakex, **NO_LIMITS)
    assert status(mid) == "needs_check"


def test_precheck_unexpected_exception_releases(tmpdb, fakex, monkeypatch):
    mid = make_draft("u1")
    sender.approve(mid)

    def bad(_uid):
        raise RuntimeError("token refresh failed")

    monkeypatch.setattr(fakex, "dm_history_with", bad)
    sender.send_next(fakex, **NO_LIMITS)
    assert status(mid) == "approved"
    assert db.q1("SELECT 1 FROM contacted") is None


def test_auth_failure_pauses_sending(tmpdb, fakex, monkeypatch):
    mid = make_draft("u1")
    sender.approve(mid)

    def unauth(_uid):
        raise xapi.XError("X 401", status=401)

    monkeypatch.setattr(fakex, "dm_history_with", unauth)
    sender.send_next(fakex, **NO_LIMITS)
    assert sender.is_paused()
    assert status(mid) == "approved"


def test_definitive_precheck_error_fails_message_and_queue_moves_on(tmpdb, fakex, monkeypatch):
    a = make_draft("gone")
    b = make_draft("u2")
    sender.approve(a)
    sender.approve(b)
    real = fakex.dm_history_with

    def hist(uid):
        if uid == "gone":
            raise xapi.XError("X 400: user not found", status=400)
        return real(uid)

    monkeypatch.setattr(fakex, "dm_history_with", hist)
    sender.send_next(fakex, **NO_LIMITS)
    sender.send_next(fakex, **NO_LIMITS)
    assert status(a) == "failed"
    assert status(b) == "sent"


def test_generic_403_is_failed_and_pauses_not_blocked(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError(
        "X 403: app lacks DM permission",
        status=403,
        problem="https://api.x.com/2/problems/client-forbidden",
    )
    sender.send_next(fakex, **NO_LIMITS)
    assert status(mid) == "failed"
    assert sender.is_paused()


# 3 / 4. Inbox paging and cursor -------------------------------------------------------------


class PagedX:
    live = True

    def __init__(self, events, fail_store=False):
        self.events = events
        self.calls: list[int] = []

    def dm_events(self, stop_at_id):
        out = []
        for e in self.events:
            if e["id"] == stop_at_id:
                return out, True
            out.append(e)
        return out, stop_at_id is None


def test_cursor_not_advanced_if_store_fails(tmpdb, monkeypatch):
    ev = [
        {
            "id": "e2",
            "sender_id": "u1",
            "dm_conversation_id": "u1-me",
            "text": "hi",
            "created_at": "2026",
        }
    ]

    def boom(*_a):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(inbox, "store_events", boom)
    with pytest.raises(RuntimeError):
        inbox.sync(PagedX(ev))
    assert db.kv_get("newest_dm_event_id") == ""


def test_cursor_kept_when_stop_id_not_reached(tmpdb):
    db.kv_set("newest_dm_event_id", "old")
    ev = [
        {
            "id": f"e{i}",
            "sender_id": "u1",
            "dm_conversation_id": "u1-me",
            "text": "x",
            "created_at": "2026",
        }
        for i in range(3)
    ]
    inbox.sync(PagedX(ev))  # 'old' never appears -> incomplete
    assert db.kv_get("newest_dm_event_id") == "old"


def test_live_dm_events_starts_with_small_page(monkeypatch):
    seen = []

    def fake_request(self, method, path, **kw):
        seen.append(kw["params"]["max_results"])
        return {"data": [{"id": "top"}], "meta": {}}

    monkeypatch.setattr(xapi.LiveX, "_request", fake_request)
    events, complete = xapi.LiveX().dm_events("top")
    assert seen == [5] and events == [] and complete


# 5. Replies on needs_check messages ---------------------------------------------------------


def test_outgoing_event_auto_resolves_needs_check_and_reply_counts(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError("timeout", definitive=False)
    sender.send_next(fakex, **NO_LIMITS)
    assert status(mid) == "needs_check"
    inbox.store_events(
        [
            {
                "id": "o1",
                "sender_id": "me",
                "dm_conversation_id": "u1-me",
                "text": "Hi Ana",
                "created_at": "2099-01-01T00:00:00Z",
            },
            {
                "id": "r1",
                "sender_id": "u1",
                "dm_conversation_id": "u1-me",
                "text": "Sure!",
                "created_at": "2099-01-01T00:05:00Z",
            },
        ],
        "me",
    )
    row = db.q1("SELECT status, replied_at FROM messages WHERE id=?", mid)
    assert row["status"] == "sent" and row["replied_at"]


def test_reply_alone_proves_delivery_of_needs_check(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError("timeout", definitive=False)
    sender.send_next(fakex, **NO_LIMITS)
    inbox.store_events(
        [
            {
                "id": "r1",
                "sender_id": "u1",
                "dm_conversation_id": "u1-me",
                "text": "Sure!",
                "created_at": "2099-01-01T00:05:00Z",
            }
        ],
        "me",
    )
    row = db.q1("SELECT status, replied_at FROM messages WHERE id=?", mid)
    assert row["status"] == "sent" and row["replied_at"]


def test_human_resolve_relinks_stored_reply(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError("timeout", definitive=False)
    sender.send_next(fakex, **NO_LIMITS)
    # reply stored by an older version / before linking was possible
    db.ex(
        "INSERT INTO dm_events(id, conversation_id, sender_id, peer_id, text, created_at)"
        " VALUES('r1','u1-me','u1','u1','Sure!','2099-01-01T00:05:00Z')"
    )
    sender.resolve_needs_check(mid, was_sent=True)
    assert db.q1("SELECT replied_at FROM messages WHERE id=?", mid)["replied_at"]


# 8. Citations must be used by the message ---------------------------------------------------


def test_message_must_use_cited_evidence():
    text = "Hi Ana, loved your NeurIPS talk on world models. Open to a quick chat about it?"
    issues = validator.validate_dm(text, GOOD_CITES, EVIDENCE, [])
    assert any(i["level"] == "block" and "use" in i["msg"] for i in issues)


def test_paraphrase_of_cited_evidence_passes():
    text = (
        "Hi Ana, saw your team open-sourced the humanoid control stack today. "
        "Open to a quick chat about what's next?"
    )
    issues = validator.validate_dm(text, GOOD_CITES, EVIDENCE, [])
    assert not validator.blocking(issues)


# 10 / 11 / 12 -------------------------------------------------------------------------------


def test_old_needs_check_does_not_eat_daily_cap(tmpdb):
    mid = make_draft("u1")
    db.ex(
        "UPDATE messages SET status='needs_check', updated_at='2020-01-01T00:00:00Z' WHERE id=?",
        mid,
    )
    assert sender.sends_last_24h() == 0


def test_ex_count_returns_rowcount(tmpdb):
    make_draft("u1")
    make_draft("u2")
    assert db.ex_count("UPDATE messages SET error='x'") == 2


@pytest.mark.parametrize(
    "text,opt_out",
    [
        ("Can't stop building robots, happy to chat!", False),
        ("stop", True),
        ("Please stop messaging me", True),
        ("Not interested, thanks", True),
    ],
)
def test_opt_out_detection(text, opt_out):
    assert bool(inbox.is_opt_out(text)) is opt_out
