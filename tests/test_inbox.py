from conftest import make_draft

from outreach import db, inbox, pipeline, sender


def _send(fakex, uid="u1"):
    mid = make_draft(uid)
    sender.approve(mid)
    sender.send_next(fakex, daily_cap=10, min_gap=0)
    return mid


def test_peer_of_conversation_id():
    assert inbox.peer_of({"dm_conversation_id": "me-42", "sender_id": "me"}, "me") == "42"
    assert inbox.peer_of({"dm_conversation_id": "42-me", "sender_id": "42"}, "me") == "42"


def test_reply_marks_message_and_needs_attention(tmpdb, fakex):
    mid = _send(fakex)
    n = inbox.store_events(
        [
            {
                "id": "r1",
                "sender_id": "u1",
                "dm_conversation_id": "u1-me",
                "text": "Happy to chat!",
                "created_at": "2099-01-01T00:00:00Z",
            }
        ],
        "me",
    )
    row = db.q1("SELECT replied_at, reply_seen FROM messages WHERE id=?", mid)
    assert n == 1 and row["replied_at"] and row["reply_seen"] == 0


def test_own_outgoing_event_is_not_a_reply(tmpdb, fakex):
    mid = _send(fakex)
    inbox.store_events(
        [
            {
                "id": "o1",
                "sender_id": "me",
                "dm_conversation_id": "u1-me",
                "text": "hello",
                "created_at": "2099-01-01T00:00:00Z",
            }
        ],
        "me",
    )
    assert db.q1("SELECT replied_at FROM messages WHERE id=?", mid)["replied_at"] is None


def test_opt_out_reply_adds_do_not_contact(tmpdb, fakex):
    _send(fakex)
    inbox.store_events(
        [
            {
                "id": "r1",
                "sender_id": "u1",
                "dm_conversation_id": "u1-me",
                "text": "Not interested, thanks",
                "created_at": "2099",
            }
        ],
        "me",
    )
    assert pipeline.exclusion_reason("u1")


def test_synced_conversation_excludes_new_outreach(tmpdb):
    inbox.store_events(
        [
            {
                "id": "x1",
                "sender_id": "u9",
                "dm_conversation_id": "u9-me",
                "text": "hi",
                "created_at": "2026",
            }
        ],
        "me",
    )
    assert pipeline.exclusion_reason("u9") == "existing DM conversation with your account"
