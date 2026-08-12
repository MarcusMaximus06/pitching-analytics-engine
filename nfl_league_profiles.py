"""Local, non-secret NFL league profiles used by the draft lab."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def profile_path() -> Path:
    configured = os.getenv("HAGLABS_NFL_PROFILES_FILE")
    if configured:
        return Path(configured)
    return Path("haglabs_data") / "nfl_league_profiles.json"


def load_league_profiles() -> list[dict[str, Any]]:
    path = profile_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    profiles = payload.get("profiles", []) if isinstance(payload, dict) else []
    return [profile for profile in profiles if isinstance(profile, dict)]
