# Demo video script (target 4 min)

Record the screen at 1280 px wide with the "Live · @you" label visible in the sidebar. Use a second browser (or your phone) logged into the recipient test account. Before you start, check:
- the sending account and the recipient account follow each other,
- the recipient has replied at least once before, or has DMs open.

| Time | Show | Say (short) |
|---|---|---|
| 0:00–0:20 | Home, sidebar shows "Live · @sender" | "This is a local tool that finds AI and robotics builders on X and opens conversations by DM. Every message is approved by a human, and it never messages anyone twice." |
| 0:20–0:50 | New campaign: segment text, seed `@<a real AI account>`, click *Write the search for me*, edit it, fill in *About you* | "I describe who I want. Claude proposes an X search, which I can edit. The sender context is the only thing the AI may say about us." |
| 0:50–1:30 | Campaign page: estimated cost, *Find people*, job log streaming, people table with scores, SEA tag, quotes, the *Skipped* tab with a news/promo account and a person with closed DMs | "Two stages to keep X costs down: everyone is screened on data the search already paid for, and only the top 10 get a deeper check. Each reason quotes the person's own words. Here it rejected a news account, and here X says this person doesn't accept DMs." |
| 1:30–2:20 | Review: the quoted post marked "used in the message". Edit a draft to "As we discussed…" and save: red block. Undo it. **Skip** the drafts for real people. | "Each draft must use something they really posted, and the validator checks the quote exists. Fake familiarity is blocked. I'm skipping these because this is a test: I only message accounts I control." |
| 2:20–2:50 | Add the recipient test account as a seed or as a draft (README: run a campaign with `from:<test account>` in the query). Approve it. | "Now a real send to my own test account." |
| 2:50–3:20 | Home (right panel): queued → sent. Phone shows the DM arriving. Reply from the phone: "Sure, let's talk". | |
| 3:20–3:45 | *Check inbox* on Sent → Home shows the reply under "Needs you" with *Reply on X*. Sent shows the conversation. | "Replies land in Needs you." |
| 3:45–4:10 | Approve a second draft to the same person (or re-run the campaign): it's *blocked: already messaged*. Pause button. Settings: spend per endpoint, guardrails, do-not-contact. | "It refuses to message the same person twice, even across campaigns. Uncertain sends are parked for a human, never retried. Here's what this demo cost." |
| 4:10–4:30 | Close | "Limits: recent search covers 7 days, X keeps DM history for 30, and replies are answered on X. The write-up covers what I'd build next." |

**Tip:** to target your own test account in a live campaign, use the query `from:<testaccount> -is:retweet` with the min score set to 0. Post one AI-related tweet from it beforehand so there is evidence to quote.
