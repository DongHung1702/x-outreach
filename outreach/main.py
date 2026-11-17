"""Web UI (FastAPI + server-rendered Jinja). Run with `uv run outreach`."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import auth, db, demo, llm, pipeline, sender, validator, worker, xapi
from .config import settings

HERE = Path(__file__).parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init(settings.db_path)
    if not settings.live:
        db.kv_set("me_id", demo.ME["id"])
        db.kv_set("me_username", demo.ME["username"])
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="X Outreach", lifespan=lifespan)
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["fromjson"] = db.loads


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """CSRF guard. The app runs on localhost with no login, so any website the operator visits
    could otherwise submit forms to it (e.g. approve a message with attacker text). Browsers
    send Origin and/or Sec-Fetch-Site on cross-site POSTs; reject those."""
    if request.method == "POST":
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if site and site not in ("same-origin", "none"):
            return PlainTextResponse("Cross-site request blocked", status_code=403)
        if origin and origin != "null" and urlparse(origin).netloc != host:
            return PlainTextResponse("Cross-site request blocked", status_code=403)
    return await call_next(request)


def back(url: str, msg: str = "") -> RedirectResponse:
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        url = "/"  # only local redirects
    if not msg:
        return RedirectResponse(url, status_code=303)
    path, _, frag = url.partition("#")  # the message must go before the #fragment
    sep = "&" if "?" in path else "?"
    target = f"{path}{sep}msg={quote(msg)}" + (f"#{frag}" if frag else "")
    return RedirectResponse(target, status_code=303)


def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
    ctx.update(
        request=request,
        msg=request.query_params.get("msg", ""),
        live=settings.live,
        me=db.kv_get("me_username"),
        llm_name=llm.get().name,
        attention=attention_count(),
        path=request.url.path,
        drafts=_count("SELECT COUNT(*) n FROM messages WHERE status='draft'"),
        side={
            "paused": sender.is_paused(),
            "sent24": sender.sends_last_24h(),
            "cap": settings.daily_send_cap,
            "queued": _count("SELECT COUNT(*) n FROM messages WHERE status='approved'"),
            "outcome": db.kv_get("last_send_outcome"),
            "last_sync": db.kv_get("last_inbox_sync"),
            "costs": costs(),
        },
    )
    return templates.TemplateResponse(request, name, ctx)


def _hue(handle: str) -> int:
    return sum(map(ord, handle or "x")) * 47 % 360


def _ago(ts: str | None) -> str:
    """'2026-11-15T15:52:02Z' -> '3m' / '5h' / 'Nov 14', like X timestamps."""
    from datetime import UTC, datetime

    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts[:10]
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    s = (datetime.now(UTC) - t).total_seconds()
    if s < 60:
        return "now"
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    return f"{t:%b} {t.day}"


templates.env.filters["hue"] = _hue
templates.env.filters["ago"] = _ago


def attention_count() -> int:
    row = db.q1(
        "SELECT (SELECT COUNT(*) FROM messages WHERE status IN ('needs_check','failed'))"
        " + (SELECT COUNT(*) FROM messages WHERE replied_at IS NOT NULL AND reply_seen=0)"
        " + (SELECT COUNT(*) FROM messages WHERE status='draft') AS n"
    )
    return int(row["n"]) if row else 0


def costs() -> dict[str, float]:
    rows = db.q("SELECT service, SUM(cost) c FROM usage GROUP BY service")
    out = {r["service"]: float(r["c"] or 0) for r in rows}
    out["total"] = out.get("x", 0) + out.get("llm", 0)
    return out


# --- dashboard ---------------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    counts = {
        r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM messages GROUP BY status")
    }
    replied = db.q1("SELECT COUNT(*) n FROM messages WHERE replied_at IS NOT NULL")
    needs = db.q(
        "SELECT m.*, (SELECT name FROM prospects p WHERE p.id=m.prospect_id) AS name,"
        " (SELECT text FROM dm_events d WHERE d.peer_id=m.x_user_id"
        " AND d.sender_id=m.x_user_id ORDER BY created_at DESC LIMIT 1) AS last_reply"
        " FROM messages m WHERE m.status IN ('needs_check','failed')"
        " OR (m.replied_at IS NOT NULL AND m.reply_seen=0) ORDER BY m.updated_at DESC"
    )
    return render(
        request,
        "dashboard.html",
        counts=counts,
        replied=replied["n"] if replied else 0,
        needs=needs,
        campaigns=db.q(
            "SELECT c.*, (SELECT COUNT(*) FROM prospects p WHERE p.campaign_id=c.id)"
            " n FROM campaigns c ORDER BY id DESC"
        ),
        activity=db.q("SELECT * FROM activity ORDER BY id DESC LIMIT 12"),
        costs=costs(),
        paused=sender.is_paused(),
        sent24=sender.sends_last_24h(),
        cap=settings.daily_send_cap,
        outcome=db.kv_get("last_send_outcome"),
        last_sync=db.kv_get("last_inbox_sync"),
    )


# --- campaigns ---------------------------------------------------------------------------------

DEFAULT_PITCH = ""  # empty on purpose; the form shows an example as a placeholder


@app.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign(request: Request):
    return render(
        request,
        "campaign_new.html",
        form={
            "name": "",
            "segment": "",
            "seeds": "",
            "query": "",
            "pitch": DEFAULT_PITCH,
            "max_posts": 50,
            "max_seed_following": 50,
            "min_score": 70,
        },
    )


@app.post("/campaigns", response_class=HTMLResponse)
def create_campaign(
    request: Request,
    action: str = Form("create"),
    name: str = Form(""),
    segment: str = Form(""),
    seeds: str = Form(""),
    query: str = Form(""),
    pitch: str = Form(""),
    max_posts: int = Form(50),
    max_seed_following: int = Form(50),
    min_score: int = Form(70),
):
    form = dict(
        name=name,
        segment=segment,
        seeds=seeds,
        query=query,
        pitch=pitch,
        max_posts=max(10, min(500, max_posts)),
        max_seed_following=max(1, min(1000, max_seed_following)),
        min_score=max(0, min(100, min_score)),
    )
    if action == "suggest":
        if not segment.strip():
            return render(
                request,
                "campaign_new.html",
                form=form,
                errors={
                    "segment": "Describe who you want to meet first, then I can "
                    "write the search for you."
                },
            )
        try:
            form["query"] = llm.get().suggest_query(segment)
            note = "Search written below. Edit it if you like, then create the campaign."
        except Exception as e:
            note = f"Couldn't write a search right now ({e}). You can type one yourself."
        return render(request, "campaign_new.html", form=form, note=note, ok=True)
    errors: dict[str, str] = {}
    if not name.strip():
        errors["name"] = "Give it a name, e.g. “Robotics founders in Vietnam”."
    if not segment.strip():
        errors["segment"] = "Describe who you want to meet."
    if not query.strip() and not seeds.strip():
        errors["query"] = "Add a search, a seed account, or both."
    if not pitch.strip():
        errors["pitch"] = "Say who you are in a sentence or two."
    elif "<" in pitch and ">" in pitch:
        errors["pitch"] = "Replace the parts in <angle brackets> with your own words."
    if errors:
        return render(request, "campaign_new.html", form=form, errors=errors)
    cid = db.ex(
        "INSERT INTO campaigns(name, segment, seeds, query, pitch, max_posts, max_seed_following,"
        " min_score, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        name,
        segment,
        seeds,
        query,
        pitch,
        form["max_posts"],
        form["max_seed_following"],
        form["min_score"],
        db.now(),
    )
    return back(f"/campaigns/{cid}", "Campaign created. Check the cost estimate, then run it.")


def _count(sql: str, *args: Any) -> int:
    row = db.q1(sql, *args)
    return int(row["n"]) if row else 0


def estimate(c: Any) -> float:
    seeds = len([s for s in c["seeds"].replace(",", " ").split() if s.strip()])
    x = 0.0
    if c["query"].strip():
        x += c["max_posts"] * (xapi.PRICE["post_read"] + xapi.PRICE["user_read"])
    x += seeds * (xapi.PRICE["user_read"] + c["max_seed_following"] * xapi.PRICE["following_read"])
    x += 10 * 10 * xapi.PRICE["post_read"]  # deep-qualify top 10, ~10 posts each
    return round(x, 2)


@app.get("/campaigns/{cid}", response_class=HTMLResponse)
def campaign(request: Request, cid: int, show: str = "all"):
    c = db.q1("SELECT * FROM campaigns WHERE id=?", cid)
    if not c:
        raise HTTPException(404)
    floor = max(0, c["min_score"] - 15)
    groups = {
        "fit": "stage IN ('qualified','drafted','messaged','replied')",
        "wait": f"(stage='found' OR (stage='screened' AND score>={floor}))",
        "rest": f"(stage IN ('rejected','dm_closed','excluded')"
        f" OR (stage='screened' AND COALESCE(score,0)<{floor}))",
    }
    where = f" AND {groups[show]}" if show in groups else ""
    counts = {
        k: _count(f"SELECT COUNT(*) n FROM prospects WHERE campaign_id=? AND {w}", cid)
        for k, w in groups.items()
    }
    prospects = db.q(
        f"SELECT p.*, (SELECT status FROM messages m WHERE m.prospect_id=p.id"
        f" AND m.status!='rejected') msg_status FROM prospects p WHERE campaign_id=?{where}"
        " ORDER BY CASE stage WHEN 'replied' THEN 0 WHEN 'messaged' THEN 1 WHEN 'drafted' THEN 2"
        " WHEN 'qualified' THEN 3 WHEN 'screened' THEN 4 ELSE 5 END, score DESC",
        cid,
    )
    stages = {
        r["stage"]: r["n"]
        for r in db.q(
            "SELECT stage, COUNT(*) n FROM prospects WHERE campaign_id=? GROUP BY stage", cid
        )
    }
    job = db.q1("SELECT * FROM jobs WHERE campaign_id=? ORDER BY id DESC LIMIT 1", cid)
    return render(
        request,
        "campaign.html",
        c=c,
        prospects=prospects,
        stages=stages,
        job=job,
        est=estimate(c),
        show=show,
        counts=counts,
        floor=floor,
        remaining=_count(
            "SELECT COUNT(*) n FROM prospects WHERE campaign_id=? AND"
            " stage='screened' AND score>=?",
            cid,
            max(0, c["min_score"] - 15),
        ),
    )


@app.post("/campaigns/{cid}/run")
def run_campaign(cid: int):
    worker.enqueue("discover", cid)
    return back(f"/campaigns/{cid}", "Discovery started. This page refreshes while it runs.")


@app.post("/campaigns/{cid}/deep")
def deep_more(cid: int):
    worker.enqueue("deep", cid)
    return back(f"/campaigns/{cid}", "Qualifying the next 10 candidates.")


@app.post("/prospects/{pid}/qualify")
def qualify_prospect(pid: int):
    p = db.q1("SELECT campaign_id FROM prospects WHERE id=?", pid)
    if not p:
        raise HTTPException(404)
    try:
        pipeline.qualify_one(pid, refetch=True)
    except Exception as e:
        return back(f"/campaigns/{p['campaign_id']}", f"Couldn’t check this person: {e}")
    r = db.q1(
        "SELECT p.username, p.stage, p.score, c.min_score, p.red_flags FROM prospects p"
        " JOIN campaigns c ON c.id=p.campaign_id WHERE p.id=?",
        pid,
    )
    assert r is not None
    if r["stage"] == "drafted":
        msg = f"@{r['username']} looks like a good fit (fit {r['score']}). A draft is in Review."
    elif r["stage"] == "rejected":
        msg = (
            f"@{r['username']} scored {r['score']}, below your {r['min_score']}. No draft written."
        )
    else:
        why = (db.loads(r["red_flags"]) or ["not reachable"])[0]
        msg = f"@{r['username']} skipped: {why}."
    return back(f"/campaigns/{p['campaign_id']}", msg)


@app.post("/prospects/{pid}/dnc")
def do_not_contact(pid: int, next: str = Form("/")):
    p = db.q1("SELECT * FROM prospects WHERE id=?", pid)
    if not p:
        raise HTTPException(404)
    db.ex(
        "INSERT OR IGNORE INTO do_not_contact(x_user_id, reason, at) VALUES(?,?,?)",
        p["x_user_id"],
        "marked by user",
        db.now(),
    )
    for m in db.q(
        "SELECT id FROM messages WHERE prospect_id=? AND status IN ('draft','approved')", pid
    ):
        sender.reject(m["id"])
    return back(next, f"@{p['username']} will never be messaged.")


# --- review & messages -------------------------------------------------------------------------


@app.get("/review", response_class=HTMLResponse)
def review(request: Request):
    rows = db.q(
        "SELECT m.*, p.bio, p.name, p.score, p.role, p.focus, p.reasons, p.red_flags, p.evidence,"
        " p.followers, p.location, p.sea_signal, p.receives_dm, p.source, c.name campaign"
        " FROM messages m JOIN prospects p ON p.id=m.prospect_id JOIN campaigns c"
        " ON c.id=m.campaign_id WHERE m.status IN ('draft','approved')"
        " ORDER BY m.status='approved', p.score DESC"
    )
    return render(request, "review.html", rows=rows, max_chars=validator.MAX_DM_CHARS)


@app.post("/messages/{mid}/{action}")
def message_action(mid: int, action: str, text: str = Form(""), next: str = Form("/review")):
    try:
        if action == "approve":
            if text.strip():
                sender.edit(mid, text.strip())
            sender.approve(mid)
            msg = "Approved. It goes out in the next few minutes, within your daily limit."
        elif action == "save":
            sender.edit(mid, text.strip())
            msg = "Saved. Checks ran again on your version."
        elif action == "reject":
            sender.reject(mid)
            msg = "Skipped. They won’t be messaged from this campaign."
        elif action == "unapprove":
            sender.unapprove(mid)
            msg = "Moved back to draft."
        elif action == "regenerate":
            m = db.q1("SELECT prospect_id FROM messages WHERE id=? AND status='draft'", mid)
            if not m:
                raise sender.NotAllowed("only drafts can be regenerated")
            pipeline.qualify_one(m["prospect_id"], refetch=False)
            msg = "New draft written."
        elif action == "sent":
            sender.resolve_needs_check(mid, was_sent=True)
            msg = "Marked as delivered."
        elif action == "requeue":
            sender.resolve_needs_check(mid, was_sent=False)
            msg = "Queued to send again."
        elif action == "retry":
            sender.retry(mid)
            msg = "Re-queued."
        elif action == "seen":
            db.ex("UPDATE messages SET reply_seen=1 WHERE id=?", mid)
            msg = "Marked as done."
        else:
            raise HTTPException(404)
    except (sender.NotAllowed, ValueError) as e:
        msg = f"Not done: {e}"
    return back(next, msg)


@app.get("/outbox", response_class=HTMLResponse)
def outbox(request: Request):
    rows = db.q(
        "SELECT m.*, c.name campaign, (SELECT name FROM prospects p WHERE p.id=m.prospect_id)"
        " AS name FROM messages m JOIN campaigns c ON"
        " c.id=m.campaign_id WHERE m.status NOT IN ('draft','rejected')"
        " ORDER BY COALESCE(m.sent_at, m.updated_at) DESC"
    )
    convo = {}
    for r in rows:
        convo[r["id"]] = db.q(
            "SELECT * FROM dm_events WHERE peer_id=? ORDER BY created_at", r["x_user_id"]
        )
    return render(request, "outbox.html", rows=rows, convo=convo, me_id=db.kv_get("me_id"))


@app.post("/inbox/sync")
def sync_now():
    worker.enqueue("sync")
    return back("/outbox", "Inbox sync queued (X allows 15 reads per 15 minutes).")


# --- settings & auth ---------------------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    usage = db.q(
        "SELECT service, endpoint, SUM(units) units, SUM(cost) cost, COUNT(*) calls"
        " FROM usage GROUP BY service, endpoint ORDER BY cost DESC"
    )
    return render(
        request,
        "settings.html",
        s=settings,
        usage=usage,
        costs=costs(),
        dnc=db.q("SELECT * FROM do_not_contact ORDER BY at DESC"),
        connected=bool(db.kv_get("x_access_token")) or not settings.live,
        paused=sender.is_paused(),
        price=xapi.PRICE,
        jobs=db.q("SELECT * FROM jobs ORDER BY id DESC LIMIT 5"),
    )


@app.post("/settings/pause")
def pause(paused: str = Form("1")):
    sender.set_paused(paused == "1")
    return back("/", "Sending paused." if paused == "1" else "Sending resumed.")


@app.get("/auth/login")
def login():
    if not settings.live:
        return back("/settings", "Demo mode: set X_CLIENT_ID in .env to connect a real account.")
    return RedirectResponse(auth.authorize_url(), status_code=302)


@app.get("/auth/callback")
def callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return back("/settings", f"X authorisation was cancelled: {error}")
    try:
        auth.handle_callback(code, state)
        me = xapi.client().me()
        db.kv_set("me_id", me["id"])
        db.kv_set("me_username", me["username"])
        db.log("connect", f"connected @{me['username']}")
        worker.enqueue("sync")  # learn existing conversations so we never DM them cold
    except Exception as e:
        return back("/settings", f"Connection failed: {e}")
    return back("/settings", f"Connected as @{me['username']}. Syncing existing DMs...")


@app.post("/auth/disconnect")
def disconnect():
    auth.disconnect()
    return back("/settings", "Disconnected. Tokens removed from the local database.")


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "live": settings.live,
        "attention": attention_count(),
        "costs": json.loads(json.dumps(costs())),
    }


def run() -> None:
    from urllib.parse import urlparse

    u = urlparse(settings.base_url)
    uvicorn.run("outreach.main:app", host=u.hostname or "127.0.0.1", port=u.port or 8000)
