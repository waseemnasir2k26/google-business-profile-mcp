"""Environment-driven configuration. Writes are OFF unless WRITE_ENABLED=true."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass
class Config:
    home: Path
    write_enabled: bool = False
    daily_write_cap: int = 10
    approval_ttl_seconds: int = 900
    allow_mcp_approve: bool = False
    account_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    business_name: str = ""
    llm_hook: str = ""

    @property
    def token_path(self) -> Path:
        return self.home / "token.json"

    @property
    def approvals_path(self) -> Path:
        return self.home / "approvals.json"

    @property
    def audit_path(self) -> Path:
        override = os.environ.get("GBP_AUDIT_LOG")
        return Path(override) if override else self.home / "audit.jsonl"

    @classmethod
    def from_env(cls) -> "Config":
        home = Path(os.environ.get("GBP_MCP_HOME") or Path.home() / ".gbp-mcp")
        return cls(
            home=home,
            write_enabled=_truthy(os.environ.get("WRITE_ENABLED")),
            daily_write_cap=_int("GBP_DAILY_WRITE_CAP", 10),
            approval_ttl_seconds=_int("GBP_APPROVAL_TTL_SECONDS", 900),
            allow_mcp_approve=_truthy(os.environ.get("GBP_ALLOW_MCP_APPROVE")),
            account_id=os.environ.get("GBP_ACCOUNT_ID", ""),
            client_id=os.environ.get("GBP_CLIENT_ID", ""),
            client_secret=os.environ.get("GBP_CLIENT_SECRET", ""),
            refresh_token=os.environ.get("GBP_REFRESH_TOKEN", ""),
            business_name=os.environ.get("GBP_BUSINESS_NAME", ""),
            llm_hook=os.environ.get("GBP_LLM_HOOK", ""),
        )
