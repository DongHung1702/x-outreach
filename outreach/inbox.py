"""Inbox sync: pull new DM events, mark replies, honour opt-outs.

X keeps DM events for 30 days and allows 15 reads / 15 min per user, billed per event, so we
page only until the newest event we already stored.
"""

from __future__ import annotations

import re
from typing import Any

from . import db, xapi

OPT_OUT = re.compile(
    r"\b(unsubscribe|not interested|remove me|"
    r"(don'?t|do not|stop|please stop) (message|messaging|contact|contacting|dm|dming) me)\b",
    re.I,
)
SHORT_REFUSAL = re.compile(r"^\W*(stop|no thanks|no thank you|no)\W*$", re.I)


def is_opt_out(text: str) -> bool:
    """Clear refusals only. A bare 'stop' counts only as the whole message."""
    return bool(OPT_OUT.search(text) or SHORT_REFUSAL.match(text.strip()))


def peer_of(ev: dict[str, Any], me_id: str) -> str:
    """The other person in a one-to-one conversation ('<id>-<id>')."""
    for pid in ev.get("participant_ids") or []:
        if pid != me_id:
            return str(pid)
    conv = str(ev.get("dm_conversation_id", ""))
    parts = conv.split("-")
    if len(parts) == 2:
        return parts[1] if parts[0] == me_id else parts[0]
    return str(ev.get("sender_id", "")) if ev.get("sender_id") != me_id else ""


def _link_reply(con: Any, peer: str, ev: dict[str, Any]) -> bool:
    """Attach an incoming event to our latest message to that person. A reply also proves a
    'needs_check' message was delivered."""
    m = con.execute(
        "SELECT id, status, replied_at, approved_at FROM messages WHERE x_user_id=?"
        " AND status IN ('sent','needs_check') ORDER BY approved_at DESC LIMIT 1",
        (peer,),
    ).fetchone()
    when = ev.get("created_at") or db.now()
    if not m or (m["approved_at"] and when < m["approved_at"]):
        return False
    new_reply = not m["replied_at"]
    con.execute(
        "UPDATE messages SET status='sent', sent_at=COALESCE(sent_at, ?),"
        " error=CASE WHEN status='needs_check' THEN 'delivery confirmed by their reply'"
        " ELSE error END, replied_at=COALESCE(replied_at, ?),"
        " reply_seen=CASE WHEN replied_at IS NULL THEN 0 ELSE reply_seen END, updated_at=?"
        " WHERE id=?",
        (when, when, db.now(), m["id"]),
    )
    con.execute(
        "UPDATE prospects SET stage='replied'"
        " WHERE x_user_id=? AND stage IN ('messaged','drafted')",
        (peer,),
    )
    if is_opt_out(ev.get("text", "")):
        con.execute(
            "INSERT OR IGNORE INTO do_not_contact(x_user_id, reason, at) VALUES(?,?,?)",
            (peer, f"replied: {ev.get('text', '')[:80]}", db.now()),
        )
    return new_reply


def _confirm_outgoing(con: Any, peer: str, ev: dict[str, Any]) -> None:
    """Our own message showing up in the inbox resolves an uncertain send."""
    when = ev.get("created_at") or db.now()
    con.execute(
        "UPDATE messages SET status='sent', sent_at=?, x_event_id=COALESCE(x_event_id, ?),"
        " error='delivery confirmed by inbox sync', updated_at=?"
        " WHERE x_user_id=? AND status='needs_check' AND (approved_at IS NULL OR approved_at<=?)",
        (when, ev["id"], db.now(), peer, when),
    )
    con.execute(
        "UPDATE prospects SET stage='messaged' WHERE x_user_id=? AND stage='drafted'", (peer,)
    )


def store_events(events: list[dict[str, Any]], me_id: str) -> int:
    """Insert events (idempotent), confirm deliveries, mark replies and opt-outs.
    Returns the number of new replies."""
    replies = 0
    for ev in sorted(events, key=lambda e: e.get("created_at", "")):
        peer = peer_of(ev, me_id)
        with db.tx() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO dm_events(id, conversation_id, sender_id, peer_id, text,"
                " created_at) VALUES(?,?,?,?,?,?)",
                (
                    ev["id"],
                    ev.get("dm_conversation_id"),
                    ev.get("sender_id"),
                    peer,
                    ev.get("text", ""),
                    ev.get("created_at"),
                ),
            )
            if not cur.rowcount or not peer:
                continue
            if ev.get("sender_id") == me_id:
                _confirm_outgoing(con, peer, ev)
            elif _link_reply(con, peer, ev):
                replies += 1
    return replies


def relink_replies(peer: str) -> None:
    """After a human confirms delivery, attach replies that arrived while it was unclear."""
    rows = db.q(
        "SELECT * FROM dm_events WHERE peer_id=? AND sender_id=? ORDER BY created_at", peer, peer
    )
    for r in rows:
        with db.tx() as con:
            _link_reply(con, peer, dict(r))


def sync(x: xapi.XClient) -> str:
    me_id = db.kv_get("me_id")
    if not me_id:
        return "X account not connected"
    newest = db.kv_get("newest_dm_event_id") or None
    events, complete = x.dm_events(newest)
    n = store_events(events, me_id)  # save first; only then move the cursor
    if events and complete:
        db.kv_set("newest_dm_event_id", str(events[0]["id"]))
    db.kv_set("last_inbox_sync", db.now())
    msg = f"inbox synced: {len(events)} new events, {n} new replies"
    if not complete:
        msg += " (more than one batch of new messages; the next sync continues)"
    if n:
        db.log("reply", msg)
    return msg
