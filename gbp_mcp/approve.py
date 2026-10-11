"""Human approval gate: single-use tokens bound to the exact payload.

Tokens are minted by a human via the CLI (`python -m gbp_mcp.approve ...`) or,
only if GBP_ALLOW_MCP_APPROVE=true, via the approve_reply tool. Only a SHA-256
of each token is stored. A token is valid for one write of one exact payload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Callable

from .config import Config


class ApprovalError(PermissionError):
    pass


def payload_hash(kind: str, fields: dict) -> str:
    blob = json.dumps({"kind": kind, "fields": fields}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ApprovalStore:
    def __init__(self, path: Path, ttl_seconds: int = 900, now: Callable[[], float] = time.time):
        self.path = Path(path)
        self.ttl = ttl_seconds
        self.now = now

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def mint(self, kind: str, fields: dict) -> str:
        token = "gbp_" + secrets.token_urlsafe(24)
        now = self.now()
        data = {k: v for k, v in self._load().items() if v["expires"] > now and not v["used"]}
        data[_h(token)] = {"payload": payload_hash(kind, fields), "kind": kind,
                           "expires": now + self.ttl, "used": False}
        self._save(data)
        return token

    def consume(self, token: str, kind: str, fields: dict) -> None:
        """Validate and burn the token. Raises ApprovalError on any mismatch."""
        if not token:
            raise ApprovalError("approve_token is required for writes.")
        data = self._load()
        rec = data.get(_h(token))
        if rec is None:
            raise ApprovalError("Unknown approve_token.")
        if rec["used"]:
            raise ApprovalError("approve_token was already used (tokens are single-use).")
        if rec["expires"] <= self.now():
            raise ApprovalError("approve_token has expired.")
        if rec["kind"] != kind or rec["payload"] != payload_hash(kind, fields):
            raise ApprovalError("approve_token does not match this exact action and text.")
        rec["used"] = True  # burn BEFORE the write so a crash can never allow a replay
        self._save(data)


def reply_fields(review_id: str, text: str) -> dict:
    return {"review_id": review_id, "text": text}


def post_fields(location: str, text: str, cta: dict | None) -> dict:
    return {"location": location, "text": text, "cta": cta or None}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gbp-mcp-approve",
                                description="Mint a single-use approve token after reading the exact text.")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reply", help="approve one review reply")
    r.add_argument("--review-id", required=True, help="accounts/A/locations/L/reviews/R")
    r.add_argument("--text", required=True)
    q = sub.add_parser("post", help="approve one local post")
    q.add_argument("--location", required=True)
    q.add_argument("--text", required=True)
    q.add_argument("--cta-type", default="")
    q.add_argument("--cta-url", default="")
    for s in (r, q):
        s.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    args = p.parse_args(argv)

    cfg = Config.from_env()
    store = ApprovalStore(cfg.approvals_path, cfg.approval_ttl_seconds)
    if args.cmd == "reply":
        kind, fields = "reply", reply_fields(args.review_id, args.text)
        print(f"\nREPLY to {args.review_id}:\n---\n{args.text}\n---")
    else:
        cta = {"actionType": args.cta_type, "url": args.cta_url} if args.cta_type else None
        kind, fields = "post", post_fields(args.location, args.text, cta)
        print(f"\nPOST to {args.location}:\n---\n{args.text}\n---\nCTA: {cta}")
    if not args.yes:
        if input("Type 'approve' to mint a single-use token: ").strip().lower() != "approve":
            print("Not approved.")
            return 1
    print(f"\napprove_token: {store.mint(kind, fields)}")
    print(f"Valid for {cfg.approval_ttl_seconds}s, one use, this exact text only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
