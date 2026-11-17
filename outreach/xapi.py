"""Thin X API v2 client (official endpoints only) plus usage/cost metering.

Prices are the pay-per-use rates published at docs.x.com/x-api/getting-started/pricing
(checked 2026-11-15). They only drive the cost meter; X bills you, not this app.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from . import auth, db

API = "https://api.x.com/2"

PRICE = {
    "post_read": 0.005,
    "user_read": 0.010,
    "following_read": 0.010,
    "dm_event_read": 0.010,
    "dm_send": 0.015,
    "owned_read": 0.001,
}

DM_FIELDS = "id,text,event_type,created_at,dm_conversation_id"
USER_FIELDS = "description,location,public_metrics,receives_your_dm,url,verified,protected"


class XError(Exception):
    """An X API failure, classified for the sender's state machine."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        problem: str = "",
        definitive: bool = True,
        reset_at: float | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.problem = problem
        # definitive=True means X certainly did NOT perform the action (safe to mark failed).
        # definitive=False means we cannot know (timeout / 5xx): never auto-retry a send.
        self.definitive = definitive
        self.reset_at = reset_at

    @property
    def rate_limited(self) -> bool:
        return self.status == 429

    @property
    def recipient_blocked(self) -> bool:
        # Only X's DM problem types mean "this recipient can't get your DM". A bare 403 can
        # also be the app/account (missing DM permission, locked account) -> treat as failure.
        return "/problems/dm-" in self.problem


@dataclass
class SendResult:
    event_id: str
    conversation_id: str


class XClient(Protocol):
    live: bool

    def me(self) -> dict[str, Any]: ...
    def search_recent(
        self, query: str, max_posts: int
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]: ...
    def users_by_usernames(self, usernames: list[str]) -> list[dict[str, Any]]: ...
    def following(self, user_id: str, limit: int) -> list[dict[str, Any]]: ...
    def user_tweets(self, user_id: str, limit: int) -> list[dict[str, Any]]: ...
    def dm_history_with(self, user_id: str) -> list[dict[str, Any]]: ...
    def dm_events(self, stop_at_id: str | None) -> tuple[list[dict[str, Any]], bool]: ...
    def send_dm(self, user_id: str, text: str) -> SendResult: ...


def record(service: str, endpoint: str, units: int, cost: float, status: int = 200) -> None:
    db.ex(
        "INSERT INTO usage(at, service, endpoint, units, cost, status) VALUES(?,?,?,?,?,?)",
        db.now(),
        service,
        endpoint,
        units,
        cost,
        status,
    )


class LiveX:
    live = True

    def __init__(self) -> None:
        self.http = httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))

    # -- plumbing ---------------------------------------------------------------------------

    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            token = auth.access_token()
        except Exception as e:
            raise XError(
                f"Could not refresh X login: {e}. Reconnect in Settings.", status=401
            ) from e
        if not token:
            raise XError("X account not connected. Open Settings and connect it.", status=401)
        try:
            r = self.http.request(
                method, API + path, headers={"Authorization": f"Bearer {token}"}, **kw
            )
        except httpx.TimeoutException as e:
            raise XError(f"Timeout calling X {path}", definitive=False) from e
        except httpx.TransportError as e:
            # Connection never established -> request not sent; anything else is ambiguous.
            definitive = isinstance(e, httpx.ConnectError)
            raise XError(f"Network error calling X {path}: {e}", definitive=definitive) from e

        if r.status_code == 429:
            reset = r.headers.get("x-rate-limit-reset")
            raise XError(
                f"X rate limit hit on {path}",
                status=429,
                reset_at=float(reset) if reset else time.time() + 900,
            )
        if r.status_code >= 500:
            raise XError(
                f"X server error {r.status_code} on {path}", status=r.status_code, definitive=False
            )
        try:
            body: dict[str, Any] = r.json() if r.content else {}
        except ValueError:
            body = {"detail": r.text[:300]}
        if r.status_code >= 400:
            problem = str(body.get("type", ""))
            detail = body.get("detail") or body.get("title") or r.text[:300]
            raise XError(
                f"X {r.status_code} on {path}: {detail}", status=r.status_code, problem=problem
            )
        return body

    @staticmethod
    def _users(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {u["id"]: u for u in body.get("includes", {}).get("users", [])}

    # -- reads ------------------------------------------------------------------------------

    def me(self) -> dict[str, Any]:
        body = self._request("GET", "/users/me")
        record("x", "users/me", 1, PRICE["owned_read"])
        return dict(body["data"])

    def search_recent(
        self, query: str, max_posts: int
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        posts: list[dict[str, Any]] = []
        users: dict[str, dict[str, Any]] = {}
        token: str | None = None
        while len(posts) < max_posts:
            params: dict[str, Any] = {
                "query": query,
                "max_results": max(10, min(100, max_posts - len(posts))),
                "tweet.fields": "created_at,author_id,public_metrics,lang",
                "expansions": "author_id",
                "user.fields": USER_FIELDS,
            }
            if token:
                params["next_token"] = token
            body = self._request("GET", "/tweets/search/recent", params=params)
            batch = body.get("data", [])
            new_users = self._users(body)
            posts.extend(batch)
            users.update(new_users)
            record(
                "x",
                "tweets/search/recent",
                len(batch),
                len(batch) * PRICE["post_read"] + len(new_users) * PRICE["user_read"],
            )
            token = body.get("meta", {}).get("next_token")
            if not token or not batch:
                break
        return posts[:max_posts], users

    def users_by_usernames(self, usernames: list[str]) -> list[dict[str, Any]]:
        if not usernames:
            return []
        body = self._request(
            "GET",
            "/users/by",
            params={"usernames": ",".join(usernames[:100]), "user.fields": USER_FIELDS},
        )
        data = body.get("data", [])
        record("x", "users/by", len(data), len(data) * PRICE["user_read"])
        return list(data)

    def following(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        body = self._request(
            "GET",
            f"/users/{user_id}/following",
            params={"max_results": max(1, min(1000, limit)), "user.fields": USER_FIELDS},
        )
        data = body.get("data", [])[:limit]
        record("x", "users/following", len(data), len(data) * PRICE["following_read"])
        return list(data)

    def user_tweets(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        body = self._request(
            "GET",
            f"/users/{user_id}/tweets",
            params={
                "max_results": max(5, min(100, limit)),
                "exclude": "retweets,replies",
                "tweet.fields": "created_at,public_metrics",
            },
        )
        data = body.get("data", [])
        record("x", "users/tweets", len(data), len(data) * PRICE["post_read"])
        return list(data)

    # -- DMs --------------------------------------------------------------------------------

    def dm_history_with(self, user_id: str) -> list[dict[str, Any]]:
        body = self._request(
            "GET",
            f"/dm_conversations/with/{user_id}/dm_events",
            params={
                "max_results": 1,
                "dm_event.fields": DM_FIELDS,
                "expansions": "sender_id",
            },
        )
        data = body.get("data", [])
        record(
            "x", "dm_conversations/with/dm_events", len(data), len(data) * PRICE["dm_event_read"]
        )
        return list(data)

    def dm_events(self, stop_at_id: str | None) -> tuple[list[dict[str, Any]], bool]:
        """Newest-first DM events until we reach one we already stored.

        X bills every returned event, so the first page is tiny (an idle inbox costs ~$0.05)
        and later pages grow. Returns (events, complete); complete=False means we hit the
        page cap before reaching stop_at_id, so the caller must not move its cursor.
        """
        out: list[dict[str, Any]] = []
        token: str | None = None
        for size in (5, 25, 100, 100, 100):
            params: dict[str, Any] = {
                "max_results": size,
                "event_types": "MessageCreate",
                "dm_event.fields": DM_FIELDS,
                "expansions": "sender_id,participant_ids",
            }
            if token:
                params["pagination_token"] = token
            body = self._request("GET", "/dm_events", params=params)
            data = body.get("data", [])
            record("x", "dm_events", len(data), len(data) * PRICE["dm_event_read"])
            for ev in data:
                if stop_at_id and ev["id"] == stop_at_id:
                    return out, True
                out.append(ev)
            token = body.get("meta", {}).get("next_token")
            if not token:
                return out, True  # reached the end of the 30-day history
        return out, stop_at_id is None

    def send_dm(self, user_id: str, text: str) -> SendResult:
        body = self._request(
            "POST", f"/dm_conversations/with/{user_id}/messages", json={"text": text}
        )
        record("x", "dm_conversations/with/messages", 1, PRICE["dm_send"], 201)
        data = body.get("data", {})
        return SendResult(str(data.get("dm_event_id", "")), str(data.get("dm_conversation_id", "")))


def client() -> XClient:
    from .config import settings

    if settings.live:
        return LiveX()
    from .demo import DemoX

    return DemoX()
