"""MCP stdio server. Registers the tools from tools.py."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .auth import TokenProvider
from .client import GBPClient
from .config import Config
from .completeness import render_text
from .tools import Tools


def build_tools(cfg: Config | None = None) -> Tools:
    cfg = cfg or Config.from_env()
    return Tools(cfg, GBPClient(TokenProvider(cfg)))


def build_server(tools: Tools | None = None) -> FastMCP:
    t = tools or build_tools()
    mcp = FastMCP("google-business-profile")

    @mcp.tool()
    def list_accounts() -> dict:
        """List Google Business Profile accounts the signed-in user can access."""
        return t.list_accounts()

    @mcp.tool()
    def list_locations(account: str = "") -> dict:
        """List locations for an account (accounts/123). Defaults to GBP_ACCOUNT_ID."""
        return t.list_locations(account)

    @mcp.tool()
    def get_location(location: str) -> dict:
        """Get one location's profile (locations/123 or accounts/1/locations/2)."""
        return t.get_location(location)

    @mcp.tool()
    def list_reviews(location: str, since: str = "", min_stars: int = 0) -> dict:
        """List reviews. since: ISO date or '7d'. min_stars: 1-5 filter (0 = all)."""
        return t.list_reviews(location, since or None, min_stars or None)

    @mcp.tool()
    def draft_review_reply(review_id: str, tone: str = "friendly", business_name: str = "") -> dict:
        """Draft a reply (template + optional LLM hook). NEVER posts. review_id is the full
        review name accounts/A/locations/L/reviews/R."""
        return t.draft_review_reply(review_id, tone, business_name)

    @mcp.tool()
    def approve_reply(review_id: str, text: str) -> dict:
        """Disabled by default. A human approves via the CLI: python -m gbp_mcp.approve reply."""
        return t.approve_reply(review_id, text)

    @mcp.tool()
    def post_review_reply(review_id: str, text: str, approve_token: str) -> dict:
        """Post a reply. Requires WRITE_ENABLED=true and a single-use human-minted approve_token
        for this exact text."""
        return t.post_review_reply(review_id, text, approve_token)

    @mcp.tool()
    def create_post(location: str, text: str, approve_token: str,
                    cta_type: str = "", cta_url: str = "") -> dict:
        """Create a local post. Requires WRITE_ENABLED=true and an approve_token for this exact
        text. cta_type: BOOK, ORDER, SHOP, LEARN_MORE, SIGN_UP, CALL."""
        cta = {"actionType": cta_type, "url": cta_url} if cta_type else None
        return t.create_post(location, text, cta, approve_token)

    @mcp.tool()
    def get_performance(location: str, metrics: str = "WEBSITE_CLICKS,CALL_CLICKS",
                        date_range: str = "last_30_days") -> dict:
        """Daily-metric totals. metrics: comma list (e.g. WEBSITE_CLICKS,CALL_CLICKS,
        BUSINESS_DIRECTION_REQUESTS). date_range: last_N_days or YYYY-MM-DD..YYYY-MM-DD."""
        return t.get_performance(location, metrics, date_range)

    @mcp.tool()
    def audit_completeness(location: str) -> dict:
        """Score the profile 0-100 with a prioritised fix list."""
        report = t.audit_completeness(location)
        report["text"] = render_text(report)
        return report

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
