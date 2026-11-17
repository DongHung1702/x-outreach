# outreach

A small tool for finding AI and robotics builders on X and starting conversations with them by DM. You give it a target segment and/or seed accounts. It finds people, scores how well they fit and shows the evidence, then drafts a short DM that refers to something they actually posted. You approve each message, it sends from your connected X account at a safe pace, and it tracks who replied.

It runs on your own machine: a Python web app with a SQLite file. There is no hosted backend, so your X tokens never leave your computer.

```
segment / seeds ──► X search + seed followings ──► screen (bio + matched post)
      ──► deep check of the top 10 (recent posts) ──► cited reasons + DM draft
      ──► validator ──► you review / edit / approve ──► paced sender ──► inbox sync
```

## Status

Demo mode and the Claude scoring/drafting are tested end to end. The live X path (OAuth, search, DM send, inbox sync) is written against the official v2 API and covered by tests with a fake X client, but **has not yet been run against a real X account**. Treat your first live campaign as the test: start with a seed you control and a daily cap of 1–2.

## Quick start (about 5 minutes, demo mode)

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone <this repo> && cd x-outreach
uv sync
uv run outreach            # open http://127.0.0.1:8000
```

Without any keys the app starts in **demo mode**. It uses fictional `demo_*` accounts, makes no calls to X, and a "Demo" label sits in the sidebar (top right on phones). To walk through the whole flow:

1. Go to **New campaign**, enter `@demo_sea_ai_hub` as a seed and/or any query, and set a sender context.
2. Click **Find people** and wait for the job log to finish.
3. Go to **Review**, approve a few drafts, then wait about a minute.
4. Go to **Home**: the replies show up under "Needs you".

The demo includes some edge cases:
- one person who doesn't accept DMs,
- one person you already have a conversation with,
- one send that fails,
- one reply that opts out.

## Live setup (about 10 minutes)

### What you need

| Item | Notes | Cost |
|---|---|---|
| An X account to send from | The account that will own the conversations. | Free |
| X developer app with **pay-per-use credits** | console.x.com → create a Project and App. Since 6 Feb 2026 new apps are pay-per-use with no free tier. $5–10 of credit is plenty for a pilot. | Prepaid credits |
| Anthropic API key (recommended) | console.anthropic.com. Used for scoring and drafting. Without it, a simple keyword scorer and template are used instead. | A few cents per campaign |

### Configure the X app

1. In the X developer console open your App → **User authentication settings** → **Set up**.
2. **App permissions:** *Read and write and Direct message*. Set this before you connect, because tokens only get the permissions the app had when they were issued.
3. **Type of App:** *Web App, Automated App or Bot* (a confidential client).
4. **Callback URI:** `http://127.0.0.1:8000/auth/callback`. **Website URL:** any URL you own.
5. Save, then copy the **OAuth 2.0 Client ID and Client Secret**.

### Configure and connect the tool

```bash
cp .env.example .env       # then fill in X_CLIENT_ID, X_CLIENT_SECRET, ANTHROPIC_API_KEY
uv run outreach
```

Open **Settings → Connect X account** and approve access on X while logged in as the sending account. The sidebar label changes to "Live · @you". The app then syncs your existing DMs from the last 30 days, so it never cold-messages someone you already talk to.

If you change the port or host, set `OUTREACH_BASE_URL` and register the matching callback on X.

## Daily use

1. **New campaign.** Describe who you want in plain English. Optionally:
   - add seed accounts (the tool looks at who they follow),
   - click *Suggest a query* and edit the X search query it proposes.

   The **sender context** is the only information the AI may use to describe you.
2. **Find people.** The cost estimate is shown before you run. The job log explains each step. People land in stages:
   - *qualified / drafted:* good fit, a draft is ready,
   - *rejected:* poor fit,
   - *dm_closed:* X says they don't accept DMs from you,
   - *excluded:* already contacted, existing conversation, or on the do-not-contact list.
3. **Review.** Each draft sits next to the evidence it relies on (marked "used in the message"). Red issues block approval; fix the text and save to re-check. You can approve, edit, regenerate or reject each draft.
4. **Sending.** This happens automatically: one message at a time, at most `DAILY_SEND_CAP` per 24h (default 20), with at least `MIN_SECONDS_BETWEEN_SENDS` between sends (default 90 s). *Pause sending* (right-hand panel on Home) stops it immediately.
5. **Home → Needs you.** This lists new replies, sends that need a manual check, and failures. Reply on X itself; this tool only starts conversations.

## Guarantees and their limits

**One message per person, ever.** A person is skipped if any of these is true:
- they are in the `contacted` ledger (unique per X user, written in the same transaction that claims the message),
- they are on the do-not-contact list,
- a synced DM conversation with them exists.

Right before each send, the tool also asks X whether a conversation with that person already exists. This catches DMs sent by hand from the X app.

**No blind retries.** If X times out or returns a 5xx, the message becomes *check on X* and stays there until a person confirms "delivered" or "not delivered". If the app crashes mid-send, it does the same on restart. Rate limits (429) and explicit refusals (403) are safe to re-queue, because X tells us nothing was sent.

**Grounded personalisation.** The model must cite verbatim snippets with source IDs. `validator.py` rejects any of the following:
- citations it cannot find in the person's bio or posts,
- quoted text that appears nowhere in their bio or posts,
- invented familiarity ("as we discussed", "great to reconnect"),
- placeholders,
- links that aren't in your sender context or their posts.

A human still approves every message.

**Opt-outs.** A reply containing "stop", "not interested", or similar adds the person to do-not-contact.

**Known limits:**
- X keeps DM history for 30 days, so older conversations are only caught by the per-person check before each send.
- Recent search covers the last 7 days.
- Some people's DM settings can only be learned at send time; those messages become *blocked* with X's reason.

## Costs

Per campaign run with default settings (50 posts, one seed with 50 followings, deep check on 10 people):

| Item | Typical |
|---|---|
| X reads: 50 posts × $0.005, up to 50 authors × $0.01, 50 followings × $0.01, 100 recent posts × $0.005 | ≈ $1.75 max |
| X DM sends: $0.015 each | $0.30 for 20 messages |
| X inbox sync: $0.01 per DM event read, only new events | cents |
| Claude Haiku: about 60 short calls | ≈ $0.05–0.10 |

**About $2 per 20 well-researched messages.** The Settings page shows a running estimate per endpoint. Your X developer console is the source of truth.

## Development

```bash
uv run pytest -q          # 59 tests incl. no-double-send races and a full demo e2e run
uv run ruff check . && uv run mypy outreach
```

Code map: `sender.py` (state machine and ledger), `validator.py`, `pipeline.py` (discover → screen → deep qualify → draft), `inbox.py`, `xapi.py` (official v2 endpoints and metering), `auth.py` (OAuth 2.0 PKCE), `demo.py` (offline fixtures), `main.py` (web UI).
