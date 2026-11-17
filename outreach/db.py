"""SQLite storage. One file, no server. Every write goes through short transactions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT NOT NULL,
    seeds TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    pitch TEXT NOT NULL,
    max_posts INTEGER NOT NULL DEFAULT 50,
    max_seed_following INTEGER NOT NULL DEFAULT 50,
    min_score INTEGER NOT NULL DEFAULT 70,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    x_user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    bio TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    followers INTEGER NOT NULL DEFAULT 0,
    receives_dm INTEGER,            -- 1 yes, 0 no, NULL unknown (X omits when undetermined)
    source TEXT NOT NULL DEFAULT '', -- how we found them, e.g. "search" or "seed:@handle"
    evidence TEXT NOT NULL DEFAULT '[]', -- JSON list of {id, kind, text, created_at}
    score INTEGER,
    role TEXT NOT NULL DEFAULT '',
    focus TEXT NOT NULL DEFAULT '',
    sea_signal INTEGER NOT NULL DEFAULT 0,
    reasons TEXT NOT NULL DEFAULT '[]',   -- JSON list of {claim, quote, source_id}
    red_flags TEXT NOT NULL DEFAULT '[]',
    stage TEXT NOT NULL DEFAULT 'found',
    -- found -> screened -> qualified | rejected | dm_closed -> drafted
    created_at TEXT NOT NULL,
    UNIQUE (campaign_id, x_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id),
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    x_user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    text TEXT NOT NULL,
    citations TEXT NOT NULL DEFAULT '[]',
    issues TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    -- draft -> approved -> sending -> sent
    --                             \\-> failed | blocked | needs_check
    -- draft -> rejected
    error TEXT NOT NULL DEFAULT '',
    x_event_id TEXT,
    x_conversation_id TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    sent_at TEXT,
    replied_at TEXT,
    reply_seen INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_message_per_prospect
    ON messages(prospect_id) WHERE status NOT IN ('rejected');

-- The no-double-message ledger. One row per X user we ever attempted to DM.
-- A row is inserted in the same transaction that moves a message into 'sending',
-- and is only removed by an explicit human action after a definitive failure.
CREATE TABLE IF NOT EXISTS contacted (
    x_user_id TEXT PRIMARY KEY,
    message_id INTEGER NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS do_not_contact (
    x_user_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dm_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT,
    sender_id TEXT,
    peer_id TEXT,
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS dm_events_peer ON dm_events(peer_id);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    service TEXT NOT NULL,   -- x | llm
    endpoint TEXT NOT NULL,
    units INTEGER NOT NULL,
    cost REAL NOT NULL,
    status INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    campaign_id INTEGER,
    status TEXT NOT NULL,  -- queued | running | done | error
    log TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""

_db_path: Path = Path("data/outreach.db")


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def init(path: Path) -> None:
    global _db_path
    _db_path = path
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(_db_path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
    finally:
        con.close()


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Write transaction that takes the write lock immediately (no lost updates)."""
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise


def q(sql: str, *args: Any) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(sql, args).fetchall()


def q1(sql: str, *args: Any) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(sql, args).fetchone()


def ex(sql: str, *args: Any) -> int:
    with tx() as con:
        cur = con.execute(sql, args)
        return int(cur.lastrowid or 0)


def ex_count(sql: str, *args: Any) -> int:
    """Execute a write and return the number of rows changed."""
    with tx() as con:
        return int(con.execute(sql, args).rowcount)


def kv_get(key: str, default: str = "") -> str:
    row = q1("SELECT value FROM kv WHERE key=?", key)
    return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    ex(
        "INSERT INTO kv(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        key,
        value,
    )


def log(kind: str, detail: str) -> None:
    ex("INSERT INTO activity(at, kind, detail) VALUES(?,?,?)", now(), kind, detail)


def loads(value: str | None) -> Any:
    return json.loads(value) if value else []
