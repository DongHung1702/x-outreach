"""Discovery and qualification.

Two-stage funnel to keep X read costs low:
  1. Discover + screen: search posts / seed followings give a bio and 1+ matching post per
     person for free (already paid by the search). The LLM screens every candidate on that.
  2. Deep qualify: only the best-screened people (capped) get their recent posts fetched
     (~$0.05 each) and a full, evidence-cited assessment plus a DM draft.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import db, llm, validator, xapi

Log = Callable[[str], None]


def _evidence_from_user(u: dict[str, Any]) -> list[dict[str, Any]]:
    bio = (u.get("description") or "").strip()
    return [{"id": "bio", "kind": "bio", "text": bio, "created_at": ""}] if bio else []


def _upsert_prospect(
    campaign_id: int, u: dict[str, Any], source: str, posts: list[dict[str, Any]]
) -> None:
    rdm = u.get("receives_your_dm")
    evidence = _evidence_from_user(u) + [
        {"id": p["id"], "kind": "post", "text": p["text"], "created_at": p.get("created_at", "")}
        for p in posts
    ]
    with db.tx() as con:
        row = con.execute(
            "SELECT id, evidence FROM prospects WHERE campaign_id=? AND x_user_id=?",
            (campaign_id, u["id"]),
        ).fetchone()
        if row:
            old = db.loads(row["evidence"])
            seen = {e["id"] for e in old}
            merged = old + [e for e in evidence if e["id"] not in seen]
            con.execute(
                "UPDATE prospects SET evidence=? WHERE id=?", (json.dumps(merged), row["id"])
            )
            return
        con.execute(
            "INSERT INTO prospects(campaign_id, x_user_id, username, name, bio, location, url,"
            " followers, receives_dm, source, evidence, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                campaign_id,
                u["id"],
                u["username"],
                u.get("name", ""),
                u.get("description", ""),
                u.get("location", ""),
                u.get("url", ""),
                int(u.get("public_metrics", {}).get("followers_count", 0)),
                None if rdm is None else int(bool(rdm)),
                source,
                json.dumps(evidence),
                db.now(),
            ),
        )


def exclusion_reason(x_user_id: str) -> str:
    """Why we must not message this person, or '' if allowed. Shared with the sender."""
    if x_user_id == db.kv_get("me_id"):
        return "this is your own account"
    if db.q1("SELECT 1 FROM do_not_contact WHERE x_user_id=?", x_user_id):
        return "on do-not-contact list"
    if db.q1("SELECT 1 FROM contacted WHERE x_user_id=?", x_user_id):
        return "already messaged by this tool"
    if db.q1("SELECT 1 FROM dm_events WHERE peer_id=?", x_user_id):
        return "existing DM conversation with your account"
    return ""


def discover(campaign_id: int, log: Log) -> None:
    c = db.q1("SELECT * FROM campaigns WHERE id=?", campaign_id)
    assert c is not None
    x = xapi.client()
    model = llm.get()
    me_id = db.kv_get("me_id")

    if c["query"].strip():
        log(f"Searching recent posts (last 7 days): {c['query']}")
        posts, users = x.search_recent(c["query"], c["max_posts"])
        by_author: dict[str, list[dict[str, Any]]] = {}
        for p in posts:
            by_author.setdefault(p["author_id"], []).append(p)
        for uid, plist in by_author.items():
            u = users.get(uid)
            if u and not u.get("protected") and uid != me_id:
                _upsert_prospect(campaign_id, u, "search", plist[:3])
        log(f"Search returned {len(posts)} posts from {len(by_author)} people.")

    seeds = [s.lstrip("@") for s in re.split(r"[\s,]+", c["seeds"]) if s.strip()]
    if seeds:
        for seed in x.users_by_usernames(seeds):
            follows = x.following(seed["id"], c["max_seed_following"])
            n = 0
            for u in follows:
                if not u.get("protected") and u["id"] != me_id:
                    _upsert_prospect(campaign_id, u, f"seed:@{seed['username']}", [])
                    n += 1
            log(f"Seed @{seed['username']}: {n} accounts they follow added.")

    todo = db.q("SELECT * FROM prospects WHERE campaign_id=? AND stage='found'", campaign_id)
    log(f"Screening {len(todo)} new candidates with {model.name}...")

    def screen(row: Any) -> None:
        ev = db.loads(row["evidence"])
        if not ev:
            db.ex(
                "UPDATE prospects SET stage='rejected', score=0, red_flags=? WHERE id=?",
                json.dumps(["empty profile"]),
                row["id"],
            )
            return
        try:
            r = model.screen(c["segment"], dict(row), ev)
        except Exception as e:  # keep going; one bad candidate must not kill the batch
            log(f"  screen failed for @{row['username']}: {e}")
            return
        db.ex(
            "UPDATE prospects SET score=?, role=?, focus=?, sea_signal=?, reasons=?, stage=?"
            " WHERE id=?",
            int(r["score"]),
            r["role"],
            r["focus"],
            int(bool(r["sea_signal"])),
            json.dumps([{"claim": r["verdict"], "source_id": "", "quote": ""}]),
            "screened",
            row["id"],
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(screen, todo))
    log("Screening done.")


def deep_qualify(campaign_id: int, limit: int, log: Log) -> None:
    c = db.q1("SELECT * FROM campaigns WHERE id=?", campaign_id)
    assert c is not None
    floor = max(0, c["min_score"] - 15)
    rows = db.q(
        "SELECT * FROM prospects WHERE campaign_id=? AND stage='screened' AND score>=?"
        " ORDER BY sea_signal DESC, score DESC LIMIT ?",
        campaign_id,
        floor,
        limit,
    )
    log(f"Deep-qualifying top {len(rows)} candidates (screen score >= {floor}).")
    for row in rows:
        try:
            qualify_one(row["id"], refetch=True)
            p = db.q1("SELECT username, stage, score FROM prospects WHERE id=?", row["id"])
            assert p is not None
            log(f"  @{p['username']}: {p['stage']} (score {p['score']})")
        except xapi.XError as e:
            log(f"  @{row['username']}: X error, stopping batch: {e}")
            if e.rate_limited or e.status == 401:
                break
        except Exception as e:
            log(f"  @{row['username']}: failed: {e}")


def qualify_one(prospect_id: int, refetch: bool) -> None:
    """Deep assessment + DM draft for one prospect. Also used by 'Regenerate draft'."""
    row = db.q1("SELECT * FROM prospects WHERE id=?", prospect_id)
    assert row is not None
    c = db.q1("SELECT * FROM campaigns WHERE id=?", row["campaign_id"])
    assert c is not None

    reason = exclusion_reason(row["x_user_id"])
    if reason:
        db.ex(
            "UPDATE prospects SET stage='excluded', red_flags=? WHERE id=?",
            json.dumps([reason]),
            prospect_id,
        )
        return
    if row["receives_dm"] == 0:
        db.ex(
            "UPDATE prospects SET stage='dm_closed', red_flags=? WHERE id=?",
            json.dumps(["X reports this account does not accept DMs from you"]),
            prospect_id,
        )
        return

    evidence = db.loads(row["evidence"])
    if refetch:
        seen = {e["id"] for e in evidence}
        for t in xapi.client().user_tweets(row["x_user_id"], 10):
            if t["id"] not in seen:
                evidence.append(
                    {
                        "id": t["id"],
                        "kind": "post",
                        "text": t["text"],
                        "created_at": t.get("created_at", ""),
                    }
                )
        db.ex("UPDATE prospects SET evidence=? WHERE id=?", json.dumps(evidence), prospect_id)

    r = llm.get().qualify_and_draft(c["segment"], c["pitch"], dict(row), evidence)
    reasons, _ = validator.check_citations(r.get("reasons", []), evidence)
    flags = list(r.get("red_flags", []))
    dropped = len(r.get("reasons", [])) - len(reasons)
    if dropped:
        flags.append(f"{dropped} AI reason(s) dropped: quote not found in evidence")
    score = int(r["score"])
    stage = "qualified" if score >= c["min_score"] else "rejected"
    db.ex(
        "UPDATE prospects SET score=?, role=?, focus=?, sea_signal=?, reasons=?, red_flags=?,"
        " stage=? WHERE id=?",
        score,
        r["role"],
        r["focus"],
        int(bool(r["sea_signal"])),
        json.dumps(reasons),
        json.dumps(flags),
        stage,
        prospect_id,
    )
    if stage != "qualified":
        return
    save_draft(prospect_id, r["dm_text"], r.get("dm_citations", []))


def allowed_urls(pitch: str) -> list[str]:
    return validator.URL.findall(pitch)


def save_draft(prospect_id: int, text: str, citations: list[dict[str, Any]]) -> None:
    row = db.q1("SELECT * FROM prospects WHERE id=?", prospect_id)
    assert row is not None
    c = db.q1("SELECT pitch FROM campaigns WHERE id=?", row["campaign_id"])
    assert c is not None
    evidence = db.loads(row["evidence"])
    issues = validator.validate_dm(text, citations, evidence, allowed_urls(c["pitch"]))
    with db.tx() as con:
        existing = con.execute(
            "SELECT id, status FROM messages WHERE prospect_id=? AND status!='rejected'",
            (prospect_id,),
        ).fetchone()
        if existing and existing["status"] not in ("draft",):
            return  # never overwrite an approved/sent message
        if existing:
            con.execute(
                "UPDATE messages SET text=?, citations=?, issues=?, updated_at=? WHERE id=?",
                (text, json.dumps(citations), json.dumps(issues), db.now(), existing["id"]),
            )
        else:
            con.execute(
                "INSERT INTO messages(prospect_id, campaign_id, x_user_id, username, text,"
                " citations, issues, status, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,'draft',?,?)",
                (
                    prospect_id,
                    row["campaign_id"],
                    row["x_user_id"],
                    row["username"],
                    text,
                    json.dumps(citations),
                    json.dumps(issues),
                    db.now(),
                    db.now(),
                ),
            )
        con.execute("UPDATE prospects SET stage='drafted' WHERE id=?", (prospect_id,))
