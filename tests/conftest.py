from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from outreach import db, xapi


class FakeX:
    """Records sends; lets tests inject failures."""

    live = False

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.fail_next: xapi.XError | None = None
        self.lock = threading.Lock()

    def dm_history_with(self, user_id: str) -> list[dict[str, Any]]:
        return self.history.get(user_id, [])

    def send_dm(self, user_id: str, text: str) -> xapi.SendResult:
        with self.lock:
            if self.fail_next:
                err, self.fail_next = self.fail_next, None
                raise err
            self.sent.append((user_id, text))
            return xapi.SendResult(f"ev{len(self.sent)}", f"conv-{user_id}")


@pytest.fixture()
def tmpdb(tmp_path):
    db.init(tmp_path / "t.db")
    db.kv_set("me_id", "me")
    return tmp_path


@pytest.fixture()
def fakex():
    return FakeX()


EVIDENCE = [{"id": "p1", "kind": "post", "text": "We open-sourced a humanoid control stack today"}]
GOOD_TEXT = (
    "Hi Ana, saw you open-sourced a humanoid control stack. I run a small community for "
    "AI and robotics builders in Southeast Asia. Open to a quick chat about it?"
)
GOOD_CITES = [{"source_id": "p1", "quote": "open-sourced a humanoid control stack"}]


def make_draft(user_id: str, campaign_id: int | None = None, text: str = GOOD_TEXT) -> int:
    from outreach import pipeline

    if campaign_id is None:
        campaign_id = db.ex(
            "INSERT INTO campaigns(name, segment, pitch, created_at) VALUES('c','s','pitch',?)",
            db.now(),
        )
    pid = db.ex(
        "INSERT INTO prospects(campaign_id, x_user_id, username, evidence, stage, created_at)"
        " VALUES(?,?,?,?, 'qualified', ?)",
        campaign_id,
        user_id,
        f"user{user_id}",
        json.dumps(EVIDENCE),
        db.now(),
    )
    pipeline.save_draft(pid, text, GOOD_CITES)
    row = db.q1("SELECT id FROM messages WHERE prospect_id=?", pid)
    assert row is not None
    return int(row["id"])
