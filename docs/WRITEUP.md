# X Outreach: approach, trade-offs, costs, AI use

**What it is.** A local web app that finds AI and robotics builders on X and helps you open conversations with them by DM. You describe a segment, add seed accounts, or both. The tool:
- finds candidates,
- scores how well each one fits, quoting their own bio or posts as evidence,
- drafts a short DM built around one real thing they posted.

You approve each message. Approved messages go out from your connected X account at a conservative pace. The dashboard shows what was sent, who replied, and what needs a human. Setup takes about 10 minutes (README). A demo mode with fictional accounts runs with no keys.

## Approach

- **Official X API v2 only, pay-per-use.** Discovery uses recent search (7 days) and the accounts that seed accounts follow. Sending uses the DM endpoint with OAuth 2.0 PKCE. There is no scraping and no browser automation. Following X's terms matters here, and so does not putting a founder's account at risk.
- **Two-stage funnel, because X charges per resource read.** Stage one screens everyone using data the search already paid for: the bio and the post that matched. Only the top 10 get the extra step: their recent posts are pulled (about $0.05 each) and they get a full, cited assessment and a draft.
- **Evidence over eloquence.**
  - The model must return verbatim quotes with source IDs.
  - A deterministic validator rejects a draft when:
    - a quote is not in the person's bio or posts,
    - the message doesn't use the cited evidence,
    - it fakes familiarity ("as we discussed"),
    - it has a placeholder, or a link that isn't in the sender context or their posts.
  - The sender context is the only source the model may use to describe GenAI Fund.
  - A human approves every message.
- **"Never double-message" is enforced in the database, not left to good intentions.**
  - A `contacted` ledger with one row per X user is written in the same transaction that claims a message. A second claim fails on the primary key.
  - Before each send, the tool asks X whether a conversation already exists.
  - Uncertain outcomes (timeout, 5xx, crash mid-send) are parked as "check on X" and never retried automatically.
  - Tests cover concurrent workers, crash recovery, rate limits, closed DMs and the same person in two campaigns.
- **Tracking.**
  - The inbox sync pages only to the last event already stored. The first page is small because every returned event is billed.
  - Replies are linked to sent messages, and a reply also confirms an uncertain send.
  - "Not interested", "stop" and similar replies add the person to do-not-contact.

## Trade-offs I chose

| Chose | Over | Why |
|---|---|---|
| Local app (FastAPI + SQLite) | Hosted SaaS | Tokens stay on your machine and nothing needs operating. The cost is that you install `uv` and run one command. |
| Human approval of every DM | Auto-send above a score | The brief values real conversations. X also penalises accounts that look automated. |
| Low default pace (20/day, 90 s apart) | X's ceiling (15 per 15 min) | It protects a new or valuable account. It's configurable. |
| Recent search + seed followings | Bio search or third-party lead databases | They are on the standard API and fit the "no prohibited scraping" rule. Recent search only covers 7 days, so quiet people are missed. |
| Claude Haiku-class model | Larger model | Scoring and short drafts are simple tasks. The validator catches the failure that matters (invented facts). |

**Known limits:**
- X keeps DM history for 30 days.
- Whether someone accepts DMs is sometimes only known at send time; those messages show as *blocked* with X's reason.
- Replies are read in the tool but answered on X.
- The tool has a single user and a single sending account.

## Costs (pay-per-use list prices, Nov 2026)

| Item | Cost |
|---|---|
| One campaign run (50 posts, 1 seed × 50, deep check on 10 people) | ≤ $1.75 in X reads |
| Per DM sent | $0.015 |
| LLM | ≈ $0.05–0.10 per run |
| Idle inbox sync | ≈ $0.05 per check |

**Roughly $2 per 20 well-researched conversations.** No subscriptions are needed. The app meters spend per endpoint, and the X console is the source of truth. My own testing cost: _[fill after live test]_.

## What I'd do next

1. Reply drafting inside the tool, with the same evidence rules.
2. Follow-ups gated by human approval (still never two unanswered messages).
3. Better recall:
   - list-based discovery,
   - "people who engaged with X" seeds,
   - a cached people database to avoid paying for re-reads.
4. Multi-user access with roles, plus hosting behind SSO.
5. A larger labelled set to tune the threshold for each segment.

## How I used AI tools, and how I checked them

I used Claude Code as a pair engineer, with a fixed loop: **spec → plan → tests → code → independent review → run it for real.**

1. **Spec and plan first.** I turned the brief into acceptance criteria and wrote a plan (files, risks, how to verify). I approved it before any code was written.
2. **Facts from the source, not from model memory.** A research sub-agent read docs.x.com for endpoints, scopes, rate limits and prices. Things the docs changed in my design:
   - DM `sender_id` comes through *expansions*, not fields.
   - DM reads are limited to 15 per 15 min and billed per event, which is why the inbox paging is small.
   - The `receives_your_dm` user field exists, so people with closed DMs are filtered before any drafting cost.
3. **Tests before code where mistakes are expensive.** The validator (11 tests) and the sender state machine (14 tests, including 8 threads racing to send one message) were written first. The full suite is now 59 tests, including an end-to-end run through the real web app and background worker.
4. **AI inside the product is boxed in.** It returns structured JSON, cites verbatim quotes, and passes a deterministic validator. A human approves, and every LLM call is metered.
5. **Independent review.** A fresh-context reviewer agent audited the code. It found 12 real issues, 3 of them serious:
   - No CSRF protection, so any website could make the localhost app approve and send a DM.
   - Some errors left messages stuck in "sending" and slowly drained the queue.
   - Inbox polling that could have cost about $48/day.

   I wrote a failing test for each issue, then fixed it (19 regression tests).

   A second agent then used the app as two personas, a first-time non-technical user on a phone and a picky QA reviewer. It reported 20 usability problems. Examples:
   - Save and Approve showed no confirmation, because the flash message ended up after the `#` anchor.
   - An empty form returned a raw JSON error.
   - Status labels contradicted each other.
   - Demo mode linked to real X profiles.

   Each behaviour bug got a failing test first (6 tests); the rest were copy and layout fixes, checked again with screenshots.
6. **Measuring output quality.** `scripts/eval_scoring.py` runs the scorer on 12 hand-labelled profiles:
   - Keyword fallback: precision 1.00, recall 0.33.
   - Claude: _[fill]_.
   - Drafts passing the validator first time: _[fill]_.
7. **Seeing it work.** I clicked through the UI headless at 1280 px and 375 px (no horizontal scroll, no console errors), then did a live send between two accounts I control: _[fill]_.

**Where the AI was wrong and got caught:**
- A test used an impossible timestamp.
- The first template draft read badly ("your post about We just…").
- A citation check passed hooks the message never used.
- Its first X client treated every 403 as "recipient has DMs closed", which would have silently marked the whole queue as blocked.
