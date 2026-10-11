import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gbp_mcp.audit import AuditLog  # noqa: E402
from gbp_mcp.approve import ApprovalStore  # noqa: E402
from gbp_mcp.client import GBPClient  # noqa: E402
from gbp_mcp.config import Config  # noqa: E402
from gbp_mcp.tools import Tools  # noqa: E402

REVIEW = "accounts/1/locations/2/reviews/R1"


class StaticTokens:
    def __init__(self):
        self.invalidated = 0

    def access_token(self):
        return "test-access"

    def invalidate(self):
        self.invalidated += 1


@pytest.fixture
def cfg(tmp_path):
    return Config(home=tmp_path, write_enabled=True, daily_write_cap=3, account_id="accounts/1")


@pytest.fixture
def now():
    return datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_tools(tmp_path, now):
    def _make(cfg):
        client = GBPClient(StaticTokens(), httpx.Client(), sleep=lambda s: None)
        return Tools(cfg, client, ApprovalStore(cfg.approvals_path, 900),
                     AuditLog(tmp_path / "audit.jsonl", now=lambda: now), now=lambda: now)
    return _make


@pytest.fixture
def tools(cfg, make_tools):
    return make_tools(cfg)
