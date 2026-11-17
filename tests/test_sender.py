"""The guarantees that matter most: never double-message, never lie about what was sent."""

from __future__ import annotations

import contextlib
import threading
import time

from conftest import make_draft

from outreach import db, sender, xapi

NO_LIMITS = {"daily_cap": 100, "min_gap": 0}


def status(mid: int) -> str:
    row = db.q1("SELECT status FROM messages WHERE id=?", mid)
    assert row is not None
    return str(row["status"])


def test_approve_then_send(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    out = sender.send_next(fakex, **NO_LIMITS)
    assert out.startswith("Last sent")
    assert status(mid) == "sent"
    assert fakex.sent == [("u1", db.q1("SELECT text FROM messages WHERE id=?", mid)["text"])]


def test_same_person_in_two_campaigns_gets_one_message(tmpdb, fakex):
    a = make_draft("u1")
    b = make_draft("u1")
    sender.approve(a)
    sender.send_next(fakex, **NO_LIMITS)
    sender.approve(b)  # approving is allowed to fail silently or succeed; sending must not
    sender.send_next(fakex, **NO_LIMITS)
    assert len(fakex.sent) == 1
    assert status(b) in ("blocked", "draft")


def test_concurrent_workers_send_once(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    threads = [
        threading.Thread(target=sender.send_next, args=(fakex,), kwargs=NO_LIMITS) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(fakex.sent) == 1


def test_timeout_goes_to_needs_check_and_is_never_auto_retried(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError("timeout", definitive=False)
    sender.send_next(fakex, **NO_LIMITS)
    assert status(mid) == "needs_check"
    for _ in range(3):
        sender.send_next(fakex, **NO_LIMITS)
    assert fakex.sent == []


def test_crash_while_sending_recovers_to_needs_check(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    # simulate a process crash after the ledger insert but before X answered
    with db.tx() as con:
        con.execute("UPDATE messages SET status='sending' WHERE id=?", (mid,))
        con.execute(
            "INSERT INTO contacted(x_user_id, message_id, at) VALUES('u1', ?, ?)", (mid, db.now())
        )
    sender.recover_after_crash()
    assert status(mid) == "needs_check"
    sender.send_next(fakex, **NO_LIMITS)
    assert fakex.sent == []


def test_needs_check_requeue_by_human_sends_once(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError("timeout", definitive=False)
    sender.send_next(fakex, **NO_LIMITS)
    sender.resolve_needs_check(mid, was_sent=False)
    sender.send_next(fakex, **NO_LIMITS)
    assert len(fakex.sent) == 1 and status(mid) == "sent"


def test_recipient_not_accepting_dms_is_blocked_with_reason(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError(
        "X 403", status=403, problem="https://api.x.com/2/problems/dm-recipient-not-accepting"
    )
    sender.send_next(fakex, **NO_LIMITS)
    row = db.q1("SELECT status, error FROM messages WHERE id=?", mid)
    assert row["status"] == "blocked" and "403" in row["error"]
    assert db.q1("SELECT 1 FROM contacted WHERE x_user_id='u1'") is None


def test_rate_limit_requeues_and_pauses(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.fail_next = xapi.XError("429", status=429, reset_at=time.time() + 600)
    sender.send_next(fakex, **NO_LIMITS)
    assert status(mid) == "approved"
    assert "paused" in sender.send_next(fakex, **NO_LIMITS)
    assert fakex.sent == []


def test_existing_conversation_on_x_blocks_send(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    fakex.history["u1"] = [{"id": "e1", "sender_id": "u1", "text": "hey", "created_at": ""}]
    sender.send_next(fakex, **NO_LIMITS)
    assert status(mid) == "blocked" and fakex.sent == []


def test_daily_cap(tmpdb, fakex):
    for uid in ("u1", "u2", "u3"):
        sender.approve(make_draft(uid))
    for _ in range(3):
        sender.send_next(fakex, daily_cap=2, min_gap=0)
    assert len(fakex.sent) == 2


def test_min_gap_between_sends(tmpdb, fakex):
    for uid in ("u1", "u2"):
        sender.approve(make_draft(uid))
    sender.send_next(fakex, daily_cap=10, min_gap=3600)
    out = sender.send_next(fakex, daily_cap=10, min_gap=3600)
    assert len(fakex.sent) == 1 and "wait" in out


def test_pause_switch(tmpdb, fakex):
    sender.approve(make_draft("u1"))
    sender.set_paused(True)
    sender.send_next(fakex, **NO_LIMITS)
    assert fakex.sent == []


def test_cannot_approve_draft_with_blocking_issues(tmpdb):
    mid = make_draft("u1", text="As we discussed, let's chat about your humanoid stack?")
    with contextlib.suppress(ValueError):
        sender.approve(mid)
    assert status(mid) == "draft"


def test_do_not_contact_blocks(tmpdb, fakex):
    mid = make_draft("u1")
    sender.approve(mid)
    db.ex("INSERT INTO do_not_contact(x_user_id, reason, at) VALUES('u1','asked',?)", db.now())
    sender.send_next(fakex, **NO_LIMITS)
    assert fakex.sent == [] and status(mid) == "blocked"
