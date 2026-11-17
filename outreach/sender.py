"""Approval and sending, with a no-double-message guarantee.

Invariant: a row in `contacted` exists for every X user whose message is in
'sending', 'sent' or 'needs_check'. The row is inserted in the same IMMEDIATE transaction
that claims the message, and its PRIMARY KEY makes a second claim for the same person fail.
If X's answer is ambiguous (timeout/5xx/crash) the message goes to 'needs_check' and is
never retried automatically: a human confirms on X, then marks it sent or re-queues it.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta

from . import db, pipeline, validator, xapi


class NotAllowed(ValueError):
    pass


def approve(message_id: int) -> None:
    with db.tx() as con:
        m = con.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not m or m["status"] != "draft":
            raise NotAllowed("only drafts can be approved")
        if validator.blocking(db.loads(m["issues"])):
            raise NotAllowed("fix the blocking issues first")
        con.execute(
            "UPDATE messages SET status='approved', approved_at=?, updated_at=? WHERE id=?",
            (db.now(), db.now(), message_id),
        )
    db.log("approve", f"message {message_id} to @{m['username']}")


def unapprove(message_id: int) -> None:
    db.ex(
        "UPDATE messages SET status='draft', approved_at=NULL, updated_at=? "
        "WHERE id=? AND status='approved'",
        db.now(),
        message_id,
    )


def reject(message_id: int) -> None:
    with db.tx() as con:
        m = con.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not m or m["status"] not in ("draft", "approved"):
            raise NotAllowed("only drafts or approved messages can be rejected")
        con.execute(
            "UPDATE messages SET status='rejected', updated_at=? WHERE id=?", (db.now(), message_id)
        )
        con.execute("UPDATE prospects SET stage='rejected' WHERE id=?", (m["prospect_id"],))


def edit(message_id: int, text: str) -> None:
    m = db.q1("SELECT * FROM messages WHERE id=?", message_id)
    if not m or m["status"] != "draft":
        raise NotAllowed("only drafts can be edited")
    pipeline.save_draft(m["prospect_id"], text, db.loads(m["citations"]))


def set_paused(paused: bool) -> None:
    db.kv_set("sending_paused", "1" if paused else "")


def _pause_for(reason: str) -> None:
    set_paused(True)
    db.kv_set("last_send_outcome", reason)
    db.log("paused", reason)


def is_paused() -> bool:
    return db.kv_get("sending_paused") == "1"


def sends_last_24h() -> int:
    since = (datetime.now(UTC) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = db.q1(
        "SELECT COUNT(*) n FROM messages WHERE sent_at >= ?"
        " OR (status IN ('sending','needs_check') AND updated_at >= ?)",
        since,
        since,
    )
    return int(row["n"]) if row else 0


def recover_after_crash() -> None:
    """Called at startup: anything left in 'sending' may or may not have reached X."""
    n = db.ex_count(
        "UPDATE messages SET status='needs_check', error='app stopped while sending;"
        " check on X whether it arrived', updated_at=? WHERE status='sending'",
        db.now(),
    )
    if n:
        db.log("recover", "moved interrupted sends to needs_check")


def _finish(
    message_id: int,
    status: str,
    error: str = "",
    release: bool = False,
    event_id: str = "",
    conversation_id: str = "",
) -> None:
    with db.tx() as con:
        m = con.execute("SELECT x_user_id FROM messages WHERE id=?", (message_id,)).fetchone()
        con.execute(
            "UPDATE messages SET status=?, error=?, updated_at=?,"
            " sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END,"
            " x_event_id=COALESCE(NULLIF(?,''), x_event_id),"
            " x_conversation_id=COALESCE(NULLIF(?,''), x_conversation_id) WHERE id=?",
            (status, error, db.now(), status, db.now(), event_id, conversation_id, message_id),
        )
        if release:
            con.execute(
                "DELETE FROM contacted WHERE x_user_id=? AND message_id=?",
                (m["x_user_id"], message_id),
            )


def send_next(x: xapi.XClient, daily_cap: int, min_gap: int) -> str:
    """Send at most one approved message. Returns a short human-readable outcome."""
    if is_paused():
        return "paused by user"
    paused_until = float(db.kv_get("rate_limited_until", "0") or 0)
    if time.time() < paused_until:
        return f"paused: X rate limit until {datetime.fromtimestamp(paused_until, UTC):%H:%M} UTC"
    if sends_last_24h() >= daily_cap:
        return f"daily cap reached ({daily_cap}/24h)"
    last = float(db.kv_get("last_send_attempt", "0") or 0)
    if time.time() - last < min_gap:
        return f"wait {int(min_gap - (time.time() - last))}s before next send"

    # 1) Claim one message atomically and write the ledger row.
    with db.tx() as con:
        m = con.execute(
            "SELECT * FROM messages WHERE status='approved' ORDER BY approved_at, id LIMIT 1"
        ).fetchone()
        if not m:
            return "nothing approved"
        reason = pipeline.exclusion_reason(m["x_user_id"])
        if not reason:
            try:
                con.execute(
                    "INSERT INTO contacted(x_user_id, message_id, at) VALUES(?,?,?)",
                    (m["x_user_id"], m["id"], db.now()),
                )
            except sqlite3.IntegrityError:
                reason = "already messaged by this tool"
        if reason:
            con.execute(
                "UPDATE messages SET status='blocked', error=?, updated_at=? WHERE id=?",
                (reason, db.now(), m["id"]),
            )
            return f"blocked @{m['username']}: {reason}"
        con.execute(
            "UPDATE messages SET status='sending', updated_at=? WHERE id=?", (db.now(), m["id"])
        )
    db.kv_set("last_send_attempt", str(time.time()))
    mid, uid, handle = int(m["id"]), str(m["x_user_id"]), str(m["username"])

    # 2) Ask X whether a conversation already exists (catches DMs sent outside this tool).
    #    Nothing has been sent yet, so every failure here releases the ledger row.
    try:
        history = x.dm_history_with(uid)
    except xapi.XError as e:
        if e.rate_limited:
            db.kv_set("rate_limited_until", str(e.reset_at or time.time() + 900))
            _finish(mid, "approved", "", release=True)
            return f"rate limited; @{handle} stays queued"
        if e.status in (401, 403):
            # account/app problem: stop everything and tell the user
            _pause_for(f"Sending paused: {e}. Fix it (Settings) and resume.")
            _finish(mid, "approved", "", release=True)
            return f"paused: {e}"
        if not e.definitive:  # X unreachable: try again next tick
            _finish(mid, "approved", "", release=True)
            return f"X unreachable, will retry: {e}"
        _finish(mid, "failed", f"pre-send check failed: {e}", release=True)
        db.log("failed", f"@{handle}: {e}")
        return f"failed @{handle}: {e}"
    except Exception as e:  # token refresh failure, bad JSON, ...
        _pause_for(f"Sending paused after an unexpected error: {e}")
        _finish(mid, "approved", "", release=True)
        return f"paused: {e}"
    if history:
        for ev in history:
            db.ex(
                "INSERT OR IGNORE INTO dm_events(id, conversation_id, sender_id, peer_id, text,"
                " created_at) VALUES(?,?,?,?,?,?)",
                ev["id"],
                ev.get("dm_conversation_id"),
                ev.get("sender_id"),
                uid,
                ev.get("text", ""),
                ev.get("created_at"),
            )
        _finish(mid, "blocked", "existing DM conversation found on X", release=True)
        return f"blocked @{handle}: existing conversation"

    # 3) Send.
    try:
        res = x.send_dm(uid, m["text"])
    except Exception as e:
        if not isinstance(e, xapi.XError):
            # We cannot know whether X received it: park it for a human, never retry blindly.
            _finish(mid, "needs_check", f"unexpected error while sending: {e}")
            _pause_for(f"Sending paused after an unexpected error: {e}")
            return f"needs check @{handle}: {e}"
        if not e.definitive:
            _finish(mid, "needs_check", f"{e}. Open X and check whether it arrived.")
            db.log("needs_check", f"@{handle}: {e}")
            return f"needs check @{handle}: {e}"
        if e.rate_limited:
            db.kv_set("rate_limited_until", str(e.reset_at or time.time() + 900))
            _finish(mid, "approved", "", release=True)
            return f"rate limited; @{handle} stays queued"
        status = "blocked" if e.recipient_blocked else "failed"
        _finish(mid, status, str(e), release=True)
        if status == "failed" and e.status in (401, 403):
            _pause_for(f"Sending paused: {e}. Check the app's DM permission / account status.")
        db.log(status, f"@{handle}: {e}")
        return f"{status} @{handle}: {e}"

    _finish(mid, "sent", event_id=res.event_id, conversation_id=res.conversation_id)
    db.ex("UPDATE prospects SET stage='messaged' WHERE id=?", m["prospect_id"])
    db.log("sent", f"DM to @{handle}")
    return f"Last sent to @{handle}"


def resolve_needs_check(message_id: int, was_sent: bool) -> None:
    m = db.q1("SELECT * FROM messages WHERE id=?", message_id)
    if not m or m["status"] != "needs_check":
        raise NotAllowed("message is not waiting for a check")
    if was_sent:
        _finish(message_id, "sent", "confirmed sent by a human")
        db.ex("UPDATE prospects SET stage='messaged' WHERE id=?", m["prospect_id"])
        from . import inbox

        inbox.relink_replies(str(m["x_user_id"]))
    else:
        _finish(message_id, "approved", "", release=True)
    db.log("resolve", f"@{m['username']}: {'confirmed sent' if was_sent else 're-queued'}")


def retry(message_id: int) -> None:
    """Re-queue a definitively failed/blocked message (X confirmed nothing was delivered)."""
    m = db.q1("SELECT * FROM messages WHERE id=?", message_id)
    if not m or m["status"] not in ("failed", "blocked"):
        raise NotAllowed("only failed or blocked messages can be retried")
    db.ex(
        "UPDATE messages SET status='approved', error='', updated_at=? WHERE id=?",
        db.now(),
        message_id,
    )
