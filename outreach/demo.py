"""Demo mode: an offline stand-in for the X API with FICTIONAL accounts.

Lets a reviewer click through the whole flow (discover -> qualify -> review -> send -> reply)
without X credentials or spend. Nothing here talks to X. All people below are invented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import db, xapi

ME = {"id": "9000", "username": "demo_sender", "name": "Demo sender"}


def _u(
    uid: str, username: str, name: str, bio: str, location: str, followers: int, **extra: Any
) -> dict[str, Any]:
    return {
        "id": uid,
        "username": username,
        "name": name,
        "description": bio,
        "location": location,
        "public_metrics": {"followers_count": followers},
        "receives_your_dm": extra.pop("receives_your_dm", True),
        **extra,
    }


USERS: list[dict[str, Any]] = [
    _u(
        "101",
        "demo_linh_robotics",
        "Linh Tran",
        "Co-founder @ a Hanoi startup building low-cost robot arms for electronics assembly. "
        "Ex-research engineer. Embodied AI, sim2real.",
        "Hanoi, Vietnam",
        2300,
    ),
    _u(
        "102",
        "demo_arif_llm",
        "Arif Rahman",
        "Building open-source Bahasa Indonesia LLMs. PhD student, NLP. Previously ML infra.",
        "Jakarta",
        5400,
    ),
    _u(
        "103",
        "demo_mei_infra",
        "Mei Chen",
        "Founder, GPU inference platform for SEA startups. Writing about serving costs.",
        "Singapore",
        8100,
    ),
    _u(
        "104",
        "demo_somchai_agents",
        "Somchai P.",
        "Engineer shipping AI agents for Thai SMEs. Devtools nerd. Opinions are my own.",
        "Bangkok",
        950,
    ),
    _u(
        "105",
        "demo_paolo_humanoid",
        "Paolo Reyes",
        "Robotics researcher (humanoid locomotion). Postdoc. Building a legged robot in my garage.",
        "Manila, Philippines",
        3100,
    ),
    _u(
        "106",
        "demo_sara_vision",
        "Sara Okafor",
        "ML engineer, computer vision for warehouse robots. Shipping a dataset tool on weekends.",
        "London",
        1200,
    ),
    _u(
        "107",
        "demo_ai_news_daily",
        "AI News Daily",
        "Your daily AI news. Breaking updates, top stories. DM for promo.",
        "",
        120000,
    ),
    _u(
        "108",
        "demo_moonshot_crypto",
        "Moon AI",
        "AI x crypto. Airdrop live now. Giveaway every week. NFA.",
        "",
        45000,
    ),
    _u(
        "109",
        "demo_closed_dm_dev",
        "Huy Nguyen",
        "Founder building speech models for Vietnamese. Hiring ML engineers.",
        "Ho Chi Minh City",
        2600,
        receives_your_dm=False,
    ),
    _u(
        "110",
        "demo_rina_edgeai",
        "Rina Wijaya",
        "Edge AI on microcontrollers. Co-founder of a drone inspection startup.",
        "Bandung, Indonesia",
        1700,
    ),
    _u(
        "111",
        "demo_kenji_old_friend",
        "Kenji Sato",
        "Research scientist, robot learning. Tokyo <-> Singapore.",
        "Singapore",
        6400,
    ),
    _u(
        "112",
        "demo_recruiter_ai",
        "AI Talent Hub",
        "Hiring now! AI jobs across APAC. Send your CV.",
        "Singapore",
        9000,
    ),
]
SEED = _u(
    "100",
    "demo_sea_ai_hub",
    "SEA AI Builders (demo seed)",
    "Community of AI builders in Southeast Asia",
    "Singapore",
    15000,
)

POSTS: dict[str, list[str]] = {
    "101": [
        "We just open-sourced our sim2real grasping dataset: 40k episodes from a $900 arm.",
        "Hot take: most factory robot demos fail on cable routing, not vision.",
        "Hiring a controls engineer in Hanoi. Real robots, real lines, no slides.",
    ],
    "102": [
        "Released v0.3 of our Bahasa Indonesia 7B model. Big jump on local reading tasks.",
        "Tokenizers built for English waste 30% more tokens on Indonesian text.",
    ],
    "103": [
        "We cut inference cost for a customer by 4x by batching across tenants.",
        "SEA founders pay US prices for GPUs with worse latency. Fixing that.",
    ],
    "104": [
        "Shipped an agent that reconciles invoices for Thai SMEs. 3 pilots live.",
        "Agents need boring evals: we test on 200 real invoices every deploy.",
    ],
    "105": [
        "Our humanoid walked 50m outdoors on a $3k budget. Video + code next week.",
        "Paper accepted: learning robust locomotion from 2 hours of real data.",
    ],
    "106": [
        "Annotating warehouse pallets 10x faster with our new labeling tool.",
        "Vision models still struggle with shrink wrap reflections.",
    ],
    "107": ["BREAKING: Top 10 AI tools you must try today!", "AI is changing everything."],
    "108": ["AI agent token airdrop is LIVE. Retweet to win!"],
    "109": ["Our Vietnamese speech model now handles three regional accents."],
    "110": ["Running a 1M-param defect detector on a $4 microcontroller for drone inspections."],
    "111": ["New paper on robot learning from human video. Code is open."],
    "112": ["Hiring now: 20 AI roles in Singapore. Apply today!"],
}

REPLIES = {
    "101": "Thanks for reaching out! Happy to chat, we're actually exploring a raise. Next week?",
    "103": "Not interested, thanks.",
    "105": "Sure, what did you have in mind?",
}
DM_ERRORS = {"104"}  # simulates "recipient does not accept DMs" at send time
PRIOR_CONVERSATION = {"111"}  # simulates someone you already talk to on X


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class DemoX:
    live = False

    def __init__(self) -> None:
        self.base = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)

    def _post(self, uid: str, i: int, text: str) -> dict[str, Any]:
        return {
            "id": f"{uid}{i:03d}",
            "author_id": uid,
            "text": text,
            "created_at": _iso(self.base - timedelta(hours=int(uid) % 50 + 6 * i)),
        }

    def me(self) -> dict[str, Any]:
        return ME

    def search_recent(self, query: str, max_posts: int):
        posts = [self._post(uid, i, t) for uid, ts in POSTS.items() for i, t in enumerate(ts[:1])]
        xapi.record("demo", "tweets/search/recent", len(posts), 0)
        return posts[:max_posts], {u["id"]: u for u in USERS}

    def users_by_usernames(self, usernames: list[str]) -> list[dict[str, Any]]:
        wanted = {u.lower() for u in usernames}
        return [u for u in [SEED, *USERS] if u["username"].lower() in wanted]

    def following(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return USERS[:limit] if user_id == SEED["id"] else []

    def user_tweets(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return [self._post(user_id, i, t) for i, t in enumerate(POSTS.get(user_id, [])[:limit])]

    def dm_history_with(self, user_id: str) -> list[dict[str, Any]]:
        if user_id in PRIOR_CONVERSATION:
            return [
                {
                    "id": f"hist{user_id}",
                    "sender_id": user_id,
                    "text": "see you at the meetup",
                    "dm_conversation_id": f"{user_id}-{ME['id']}",
                    "created_at": "2026-10-20",
                }
            ]
        return []

    def dm_events(self, stop_at_id: str | None) -> tuple[list[dict[str, Any]], bool]:
        events = [
            {
                "id": f"hist{u}",
                "sender_id": u,
                "dm_conversation_id": f"{u}-{ME['id']}",
                "text": "see you at the meetup",
                "created_at": "2026-10-20T10:00:00.000Z",
            }
            for u in PRIOR_CONVERSATION
        ]
        for m in db.q(
            "SELECT * FROM messages WHERE status='sent' AND x_user_id IN ({})".format(
                ",".join("?" * len(REPLIES))
            ),
            *REPLIES,
        ):
            sent = datetime.strptime(m["sent_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            events.append(
                {
                    "id": f"out{m['id']}",
                    "sender_id": ME["id"],
                    "text": m["text"],
                    "dm_conversation_id": f"{m['x_user_id']}-{ME['id']}",
                    "created_at": _iso(sent),
                }
            )
            events.append(
                {
                    "id": f"reply{m['id']}",
                    "sender_id": m["x_user_id"],
                    "text": REPLIES[m["x_user_id"]],
                    "dm_conversation_id": f"{m['x_user_id']}-{ME['id']}",
                    "created_at": _iso(sent + timedelta(seconds=1)),
                }
            )
        events.sort(key=lambda e: e["created_at"], reverse=True)
        out = []
        for e in events:
            if e["id"] == stop_at_id:
                break
            out.append(e)
        return out, True

    def send_dm(self, user_id: str, text: str) -> xapi.SendResult:
        if user_id in DM_ERRORS:
            raise xapi.XError(
                "X 403 (demo): recipient does not accept DMs from you",
                status=403,
                problem="https://api.x.com/2/problems/dm-recipient-not-accepting",
            )
        xapi.record("demo", "dm send", 1, 0)
        return xapi.SendResult(f"demo-ev-{user_id}", f"{user_id}-{ME['id']}")
