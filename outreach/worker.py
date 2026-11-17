"""Single background thread: runs queued jobs, sends approved DMs at a safe pace, syncs inbox."""

from __future__ import annotations

import threading
import time
import traceback

from . import db, inbox, pipeline, sender, xapi
from .config import settings

_stop = threading.Event()


def enqueue(kind: str, campaign_id: int | None = None) -> int:
    running = db.q1(
        "SELECT id FROM jobs WHERE kind=? AND campaign_id IS ? AND status IN ('queued','running')",
        kind,
        campaign_id,
    )
    if running:
        return int(running["id"])
    return db.ex(
        "INSERT INTO jobs(kind, campaign_id, status, created_at) VALUES(?,?,?,?)",
        kind,
        campaign_id,
        "queued",
        db.now(),
    )


def _run_job(job_id: int) -> None:
    job = db.q1("SELECT * FROM jobs WHERE id=?", job_id)
    assert job is not None
    db.ex("UPDATE jobs SET status='running' WHERE id=?", job_id)

    def log(line: str) -> None:
        db.ex(
            "UPDATE jobs SET log = log || ? WHERE id=?",
            f"{time.strftime('%H:%M:%S')} {line}\n",
            job_id,
        )

    try:
        if job["kind"] == "discover":
            pipeline.discover(job["campaign_id"], log)
            pipeline.deep_qualify(job["campaign_id"], 10, log)
        elif job["kind"] == "deep":
            pipeline.deep_qualify(job["campaign_id"], 10, log)
        elif job["kind"] == "sync":
            log(inbox.sync(xapi.client()))
        status = "done"
    except Exception as e:
        log(f"ERROR: {e}")
        traceback.print_exc()
        status = "error"
    db.ex("UPDATE jobs SET status=?, finished_at=? WHERE id=?", status, db.now(), job_id)


def tick() -> None:
    job = db.q1("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
    if job:
        _run_job(int(job["id"]))

    if not db.kv_get("me_id"):
        if db.q1("SELECT 1 FROM messages WHERE status='approved'"):
            db.kv_set("last_send_outcome", "waiting: connect your X account in Settings")
        return
    if db.q1("SELECT 1 FROM messages WHERE status='approved'"):
        out = sender.send_next(
            xapi.client(), settings.daily_send_cap, settings.min_seconds_between_sends
        )
        if out != db.kv_get("last_send_outcome"):
            db.kv_set("last_send_outcome", out)

    minutes = settings.inbox_sync_minutes if settings.live else 1
    last = db.kv_get("last_inbox_sync_ts", "0")
    if (
        db.q1("SELECT 1 FROM messages WHERE status IN ('sent','needs_check')")
        and time.time() - float(last or 0) > minutes * 60
    ):
        db.kv_set("last_inbox_sync_ts", str(time.time()))
        try:
            inbox.sync(xapi.client())
        except Exception as e:
            db.kv_set("last_send_outcome", f"inbox sync failed: {e}")


def _loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception:
            traceback.print_exc()
        _stop.wait(3)


def start() -> None:
    db.ex(
        "UPDATE jobs SET status='error', log = log || 'interrupted by restart\n'"
        " WHERE status='running'"
    )
    sender.recover_after_crash()
    threading.Thread(target=_loop, daemon=True, name="outreach-worker").start()


def stop() -> None:
    _stop.set()
