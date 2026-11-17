"""OAuth 2.0 Authorization Code + PKCE for the X account that sends DMs.

Tokens are stored only in the local SQLite file. Nothing leaves this machine except calls to X.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx

from . import db
from .config import settings

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
SCOPES = "tweet.read users.read follows.read dm.read dm.write offline.access"


def authorize_url() -> str:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    state = secrets.token_urlsafe(24)
    db.kv_set("oauth_verifier", verifier)
    db.kv_set("oauth_state", state)
    params = {
        "response_type": "code",
        "client_id": settings.x_client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token_request(data: dict[str, str]) -> None:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth_tuple = None
    if settings.x_client_secret:  # confidential client: HTTP Basic
        auth_tuple = (settings.x_client_id, settings.x_client_secret)
    else:  # public client: client_id in the body
        data["client_id"] = settings.x_client_id
    r = httpx.post(TOKEN_URL, data=data, headers=headers, auth=auth_tuple, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"X token endpoint returned {r.status_code}: {r.text[:300]}")
    tok = r.json()
    db.kv_set("x_access_token", tok["access_token"])
    if tok.get("refresh_token"):
        db.kv_set("x_refresh_token", tok["refresh_token"])
    db.kv_set("x_token_expires_at", str(time.time() + int(tok.get("expires_in", 7200))))


def handle_callback(code: str, state: str) -> None:
    if not state or state != db.kv_get("oauth_state"):
        raise RuntimeError("OAuth state mismatch. Start the connection again from Settings.")
    _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.redirect_uri,
            "code_verifier": db.kv_get("oauth_verifier"),
        }
    )
    db.kv_set("oauth_state", "")


_refresh_lock = threading.Lock()


def access_token() -> str:
    """Valid access token, refreshing it if needed. Refresh tokens are single-use, so only one
    thread may refresh at a time; others wait and then read the new token."""
    if not db.kv_get("x_access_token"):
        return ""
    with _refresh_lock:
        expires_at = float(db.kv_get("x_token_expires_at", "0") or 0)
        if time.time() > expires_at - 120:
            refresh = db.kv_get("x_refresh_token")
            if not refresh:
                return ""
            _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
        return db.kv_get("x_access_token")


def disconnect() -> None:
    for key in ("x_access_token", "x_refresh_token", "x_token_expires_at", "me_id", "me_username"):
        db.kv_set(key, "")
