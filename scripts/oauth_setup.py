#!/usr/bin/env python
"""Installed-app OAuth flow (loopback + PKCE). Stores a refresh token locally.

Usage:
  set GBP_CLIENT_ID / GBP_CLIENT_SECRET (OAuth client of type "Desktop app"),
  then: python scripts/oauth_setup.py

The token file is written to $GBP_MCP_HOME/token.json (default ~/.gbp-mcp) with
chmod 600. On Windows chmod is limited; restrict it with:
  icacls "%USERPROFILE%\\.gbp-mcp\\token.json" /inheritance:r /grant:r "%USERNAME%:F"
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gbp_mcp.auth import AUTH_URL, SCOPE, TOKEN_URL, save_token_file  # noqa: E402
from gbp_mcp.config import Config  # noqa: E402


def build_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    q = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def exchange_code(http: httpx.Client, client_id: str, client_secret: str, code: str,
                  redirect_uri: str, verifier: str) -> dict:
    resp = http.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": code,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        "code_verifier": verifier,
    })
    resp.raise_for_status()
    body = resp.json()
    if "refresh_token" not in body:
        raise RuntimeError("No refresh_token returned. Revoke the app's access and retry with prompt=consent.")
    return body


def main() -> int:
    cfg = Config.from_env()
    if not (cfg.client_id and cfg.client_secret):
        print("Set GBP_CLIENT_ID and GBP_CLIENT_SECRET first (Desktop-app OAuth client).")
        return 1
    result: dict = {}
    state = secrets.token_urlsafe(16)
    verifier, challenge = pkce_pair()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if qs.get("state", [""])[0] == state and "code" in qs:
                result["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and return to the terminal.")

        def log_message(self, *a):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = build_auth_url(cfg.client_id, redirect, state, challenge)
    print("Opening browser to authorise (scope: business.manage). If it does not open, visit:\n" + url)
    webbrowser.open(url)
    t = threading.Thread(target=server.handle_request)
    t.start()
    t.join(timeout=300)
    server.server_close()
    if "code" not in result:
        print("No authorisation received.")
        return 1
    with httpx.Client(timeout=30) as http:
        body = exchange_code(http, cfg.client_id, cfg.client_secret, result["code"], redirect, verifier)
    save_token_file(cfg.token_path, {
        "client_id": cfg.client_id, "client_secret": cfg.client_secret,
        "refresh_token": body["refresh_token"],
    })
    print(f"Saved refresh token to {cfg.token_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
