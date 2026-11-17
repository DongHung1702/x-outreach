"""ClaudeLLM output handling (2026-11-17: thinking tokens truncated JSON output)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from outreach import llm


class FakeMessages:
    def __init__(self, text: str, stop_reason: str) -> None:
        self.text, self.stop_reason = text, stop_reason
        self.calls: list[dict[str, Any]] = []

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=10, output_tokens=kw["max_tokens"]),
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=self.text),
            ],
        )


def fake_claude(text: str, stop_reason: str = "end_turn") -> tuple[llm.ClaudeLLM, FakeMessages]:
    c = object.__new__(llm.ClaudeLLM)
    msgs = FakeMessages(text, stop_reason)
    c.client = SimpleNamespace(messages=msgs)
    c.name = "Claude (fake)"
    return c, msgs


def test_truncated_output_gives_clear_error(tmpdb):
    c, _ = fake_claude('{"query": "(robotics OR', stop_reason="max_tokens")
    with pytest.raises(RuntimeError, match="cut off"):
        c.suggest_query("Robotics founders in Vietnam")


def test_token_budget_leaves_room_for_thinking(tmpdb):
    c, msgs = fake_claude('{"query": "robotics -is:retweet"}')
    assert c.suggest_query("Robotics founders in Vietnam") == "robotics -is:retweet"
    assert msgs.calls[0]["max_tokens"] >= 2000
