"""Runtime configuration, read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    # "live" uses the real X API; "demo" uses bundled fictional fixtures and never touches X.
    # "auto" picks live when X_CLIENT_ID is set, otherwise demo.
    mode: str = field(default_factory=lambda: os.environ.get("OUTREACH_MODE", "auto"))
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("OUTREACH_DB", "data/outreach.db"))
    )
    base_url: str = field(
        default_factory=lambda: os.environ.get("OUTREACH_BASE_URL", "http://127.0.0.1:8000")
    )

    x_client_id: str = field(default_factory=lambda: os.environ.get("X_CLIENT_ID", ""))
    x_client_secret: str = field(default_factory=lambda: os.environ.get("X_CLIENT_SECRET", ""))

    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    llm_model: str = field(
        default_factory=lambda: os.environ.get("OUTREACH_LLM_MODEL", "claude-haiku-5-5")
    )
    # USD per million tokens; used only for the cost meter. Set to your model's list price.
    llm_input_price: float = field(default_factory=lambda: _float("LLM_INPUT_PRICE_PER_MTOK", 1.0))
    llm_output_price: float = field(
        default_factory=lambda: _float("LLM_OUTPUT_PRICE_PER_MTOK", 5.0)
    )

    # Sending guardrails. X allows 15 DM sends / 15 min per user; we stay well below that.
    daily_send_cap: int = field(default_factory=lambda: _int("DAILY_SEND_CAP", 20))
    min_seconds_between_sends: int = field(
        default_factory=lambda: _int("MIN_SECONDS_BETWEEN_SENDS", 90)
    )
    inbox_sync_minutes: int = field(default_factory=lambda: _int("INBOX_SYNC_MINUTES", 15))

    @property
    def live(self) -> bool:
        if self.mode == "live":
            return True
        if self.mode == "demo":
            return False
        return bool(self.x_client_id)

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/auth/callback"


settings = Settings()
