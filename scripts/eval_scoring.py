"""Check the scorer and drafter against hand labels before trusting them.

Runs the configured LLM (Claude if ANTHROPIC_API_KEY is set, else the rule-based fallback)
on the fictional demo profiles, which were labelled by hand, and reports:
  - precision / recall of "fit" at the threshold
  - how many drafts pass the deterministic validator on the first try
No X calls are made.  Usage:  uv run python scripts/eval_scoring.py [threshold]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from outreach import db, demo, llm, validator

# Hand labels: would we want a conversation with this (fictional) person?
LABELS = {
    "demo_linh_robotics": True,
    "demo_arif_llm": True,
    "demo_mei_infra": True,
    "demo_somchai_agents": True,
    "demo_paolo_humanoid": True,
    "demo_sara_vision": True,  # good builder, not SEA: fit but lower priority
    "demo_ai_news_daily": False,
    "demo_moonshot_crypto": False,
    "demo_closed_dm_dev": True,
    "demo_rina_edgeai": True,
    "demo_kenji_old_friend": True,
    "demo_recruiter_ai": False,
}
SEGMENT = (
    "Founders, engineers and researchers building robotics, embodied AI, LLMs or AI "
    "infrastructure, ideally in or connected to Southeast Asia. Individuals who ship, "
    "not news, promo or recruiting accounts."
)
PITCH = "I'm Hung. I run a small community for AI and robotics builders in Southeast Asia."


def main() -> None:
    threshold = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    db.init(Path(tempfile.mkdtemp()) / "eval.db")
    model = llm.get()
    x = demo.DemoX()
    tp = fp = fn = tn = 0
    drafts_ok = drafts = 0
    print(f"model: {model.name}\n")
    for u in demo.USERS:
        p = {
            "username": u["username"],
            "name": u["name"],
            "bio": u["description"],
            "location": u["location"],
            "followers": u["public_metrics"]["followers_count"],
        }
        ev = [{"id": "bio", "kind": "bio", "text": u["description"], "created_at": ""}] + [
            {"id": t["id"], "kind": "post", "text": t["text"], "created_at": t["created_at"]}
            for t in x.user_tweets(u["id"], 10)
        ]
        r = model.qualify_and_draft(SEGMENT, PITCH, p, ev)
        fit = int(r["score"]) >= threshold
        want = LABELS[u["username"]]
        tp += fit and want
        fp += fit and not want
        fn += (not fit) and want
        tn += (not fit) and not want
        verdict = "ok " if fit == want else "MISS"
        line = (
            f"{verdict} {u['username']:<24} score {r['score']:>3}  label {'fit' if want else 'no'}"
        )
        if fit:
            drafts += 1
            issues = validator.validate_dm(r["dm_text"], r["dm_citations"], ev, [])
            ok = not validator.blocking(issues)
            drafts_ok += ok
            line += "  draft " + ("passes" if ok else "BLOCKED: " + issues[0]["msg"][:60])
        print(line)
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    print(f"\nprecision {prec:.2f}  recall {rec:.2f}  (tp {tp}, fp {fp}, fn {fn}, tn {tn})")
    print(f"drafts passing validator first time: {drafts_ok}/{drafts}")


if __name__ == "__main__":
    main()
