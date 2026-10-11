"""OAuth for an installed app: refresh-token storage and access-token refresh."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

import httpx

from .config import Config

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/business.manage"


class AuthError(RuntimeError):
    pass


def save_token_file(path: Path, data: dict) -> None:
    """Write the token file readable only by the current user (chmod 600 on POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows: see README for the icacls note


def load_token_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class TokenProvider:
    """Returns a valid access token, refreshing via the stored refresh token."""

    def __init__(self, cfg: Config, http: httpx.Client | None = None,
                 now: Callable[[], float] = time.time):
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=30)
        self.now = now
        self._access: str = ""
        self._expires: float = 0.0

    def _credentials(self) -> tuple[str, str, str]:
        stored = load_token_file(self.cfg.token_path)
        cid = self.cfg.client_id or stored.get("client_id", "")
        secret = self.cfg.client_secret or stored.get("client_secret", "")
        refresh = self.cfg.refresh_token or stored.get("refresh_token", "")
        if not (cid and secret and refresh):
            raise AuthError(
                "Missing OAuth credentials. Run `python scripts/oauth_setup.py` "
                "(or set GBP_CLIENT_ID, GBP_CLIENT_SECRET, GBP_REFRESH_TOKEN)."
            )
        return cid, secret, refresh

    def refresh(self) -> str:
        cid, secret, refresh = self._credentials()
        resp = self.http.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": secret,
            "refresh_token": refresh, "grant_type": "refresh_token",
        })
        if resp.status_code != 200:
            raise AuthError(f"Token refresh failed ({resp.status_code}): {resp.text[:200]}")
        body = resp.json()
        self._access = body["access_token"]
        self._expires = self.now() + int(body.get("expires_in", 3600)) - 60
        return self._access

    def access_token(self) -> str:
        if self._access and self.now() < self._expires:
            return self._access
        return self.refresh()

    def invalidate(self) -> None:
        self._access = ""
        self._expires = 0.0
