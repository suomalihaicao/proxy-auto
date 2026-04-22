from __future__ import annotations

import os
from pathlib import Path

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import load_settings


BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = BASE_DIR / "data" / "settings.json"
COOKIE_NAME = "auth"


def get_session_secret() -> str:
    """Get the session secret used for auth cookie signing."""
    return load_settings(SETTINGS_PATH).session_secret or os.urandom(24).hex()


def sign_user(username: str) -> str:
    """Serialize user info into a signed cookie token."""
    serializer = URLSafeTimedSerializer(get_session_secret(), salt="pm-auth")
    return serializer.dumps({"u": username})


def parse_user(token: str | None) -> str:
    """Parse a signed auth token and return username, or empty when invalid."""
    if not token:
        return ""
    serializer = URLSafeTimedSerializer(get_session_secret(), salt="pm-auth")
    try:
        payload = serializer.loads(token, max_age=3600 * 24)
    except (BadSignature, Exception):
        return ""
    username = str(payload.get("u", "") or "")
    return username
