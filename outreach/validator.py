"""Deterministic checks that keep personalisation grounded in real evidence.

The LLM must cite exact snippets from the prospect's bio/posts. Here we verify every citation
really exists, and block drafts that fake familiarity, contain placeholders or invented links.
A human still reviews every message; these checks make the review fast and trustworthy.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MAX_DM_CHARS = 700  # X allows 10k; short messages get more replies and look less automated.
MIN_DM_CHARS = 40
MIN_QUOTE_CHARS = 6

FALSE_FAMILIARITY = [
    r"\bas (we|i) (discussed|mentioned|talked)",
    r"\b(great|nice|good|lovely) (to|meeting|seeing) (re)?(connect|meet|see|catch)",
    r"\b(re)?connect(ing)? again\b",
    r"\blast time we\b",
    r"\bfollowing up on our\b",
    r"\bour (call|chat|conversation|meeting)\b",
    r"\bhope you('| a)re (doing )?well\b.*\bsince\b",
    r"\b(i've|i have) been following (you|your work) for years\b",
    r"\bbig fan since\b",
    r"\bremember (me|when)\b",
    r"\bfriend of (yours|a friend)\b",
]
PLACEHOLDER = re.compile(r"\[[A-Za-z _]{2,30}\]|\{\{.*?\}\}|<[A-Z_ ]{3,}>|\bXXX\b|\bTODO\b")
URL = re.compile(r"https?://\S+")
QUOTED = re.compile(r"[\"“”]([^\"“”]{12,})[\"“”]")

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES).lower()
    text = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip(" .,!?;:'\"")


_STOP = {
    "this",
    "that",
    "with",
    "from",
    "have",
    "your",
    "about",
    "just",
    "today",
    "will",
    "they",
    "their",
    "what",
    "when",
    "been",
    "were",
    "into",
    "more",
    "than",
    "very",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{3,}", norm(text)) if w not in _STOP}


def uses_quote(message: str, quote: str) -> bool:
    """True if the message mentions most of the distinctive words of the cited snippet,
    so a paraphrase passes but an unrelated hook ("loved your NeurIPS talk") does not."""
    q = _words(quote)
    if not q:
        return False
    return len(q & _words(message)) / len(q) >= 0.5


def check_citations(
    citations: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (citations that are verbatim in the named evidence item, problems found)."""
    by_id = {str(e["id"]): norm(e["text"]) for e in evidence}
    ok: list[dict[str, Any]] = []
    issues: list[str] = []
    for c in citations or []:
        src = str(c.get("source_id", ""))
        quote = norm(str(c.get("quote", "")))
        if src not in by_id:
            issues.append(f"citation points to unknown source '{src}'")
        elif len(quote) < MIN_QUOTE_CHARS:
            issues.append(f"citation from {src} is too short to verify")
        elif quote not in by_id[src]:
            issues.append(f'quote not found in source {src}: "{c.get("quote", "")[:80]}"')
        else:
            ok.append(c)
    return ok, issues


def validate_dm(
    text: str,
    citations: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    allowed_urls: list[str],
) -> list[dict[str, str]]:
    """Return a list of {level: block|warn, msg}. Any 'block' disables approval."""
    issues: list[dict[str, str]] = []

    def block(msg: str) -> None:
        issues.append({"level": "block", "msg": msg})

    def warn(msg: str) -> None:
        issues.append({"level": "warn", "msg": msg})

    stripped = text.strip()
    if len(stripped) < MIN_DM_CHARS:
        block("message is too short")
    if len(stripped) > MAX_DM_CHARS:
        block(f"message is too long ({len(stripped)} > {MAX_DM_CHARS} chars)")

    if PLACEHOLDER.search(stripped):
        block("message contains an unfilled placeholder")

    low = stripped.lower()
    for pat in FALSE_FAMILIARITY:
        if re.search(pat, low):
            block("claims familiarity that does not exist (e.g. 'as we discussed')")
            break

    valid, problems = check_citations(citations, evidence)
    for p in problems:
        block(p)
    if not valid:
        block("no verified evidence: the personal hook must cite the person's bio or posts")
    elif not any(uses_quote(stripped, c["quote"]) for c in valid):
        block("the message does not use the cited evidence; mention what they actually posted")

    corpus = " ".join(norm(e["text"]) for e in evidence)
    for m in QUOTED.finditer(stripped):
        if norm(m.group(1)) not in corpus:
            block(f'quoted text not found in their posts/bio: "{m.group(1)[:60]}"')

    allowed = [u.rstrip("/").lower() for u in allowed_urls]
    evidence_urls = [
        u.rstrip("/").lower() for u in URL.findall(" ".join(e["text"] for e in evidence))
    ]
    for u in URL.findall(stripped):
        u_norm = u.rstrip("/.,)").lower()
        if not any(u_norm.startswith(a) for a in allowed + evidence_urls if a):
            block(f"link not from your pitch or their posts: {u}")

    if stripped.count("!") > 2:
        warn("many exclamation marks; reads as salesy")
    if "?" not in stripped:
        warn("no question; an easy question tends to get more replies")
    return issues


def blocking(issues: list[dict[str, str]]) -> bool:
    return any(i["level"] == "block" for i in issues)
