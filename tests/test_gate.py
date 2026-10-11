import dataclasses

import httpx
import pytest
import respx

from gbp_mcp import approve as ap
from gbp_mcp.approve import ApprovalError, ApprovalStore
from gbp_mcp.client import V4_API
from gbp_mcp.tools import ToolError, WriteBlocked
from conftest import REVIEW

REPLY_URL = f"{V4_API}/{REVIEW}/reply"


def mint(tools, text="Thanks!"):
    return tools.store.mint("reply", ap.reply_fields(REVIEW, text))


@respx.mock
def test_reply_without_token_fails_and_never_calls_google(tools):
    route = respx.put(REPLY_URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(ApprovalError, match="required"):
        tools.post_review_reply(REVIEW, "Thanks!", "")
    assert route.call_count == 0


@respx.mock
def test_reply_with_valid_token_posts_once(tools):
    route = respx.put(REPLY_URL).mock(return_value=httpx.Response(200, json={"comment": "Thanks!"}))
    out = tools.post_review_reply(REVIEW, "Thanks!", mint(tools))
    assert out["ok"] is True
    assert route.call_count == 1
    assert route.calls[0].request.content == b'{"comment":"Thanks!"}' or b"Thanks!" in route.calls[0].request.content


@respx.mock
def test_reused_token_fails(tools):
    respx.put(REPLY_URL).mock(return_value=httpx.Response(200, json={}))
    tok = mint(tools)
    tools.post_review_reply(REVIEW, "Thanks!", tok)
    with pytest.raises(ApprovalError, match="already used"):
        tools.post_review_reply(REVIEW, "Thanks!", tok)


@respx.mock
def test_token_bound_to_exact_text(tools):
    route = respx.put(REPLY_URL).mock(return_value=httpx.Response(200, json={}))
    tok = mint(tools, "Approved text")
    with pytest.raises(ApprovalError, match="does not match"):
        tools.post_review_reply(REVIEW, "Different text", tok)
    assert route.call_count == 0


@respx.mock
def test_reply_token_cannot_authorise_a_post(tools):
    route = respx.post(f"{V4_API}/accounts/1/locations/2/localPosts").mock(return_value=httpx.Response(200, json={}))
    tok = mint(tools, "Hello")
    with pytest.raises(ApprovalError):
        tools.create_post("accounts/1/locations/2", "Hello", None, tok)
    assert route.call_count == 0


def test_unknown_token_rejected(tools):
    with pytest.raises(ApprovalError, match="Unknown"):
        tools.post_review_reply(REVIEW, "x", "gbp_fake")


def test_expired_token_rejected(tmp_path):
    t = [100.0]
    store = ApprovalStore(tmp_path / "a.json", ttl_seconds=60, now=lambda: t[0])
    tok = store.mint("reply", ap.reply_fields(REVIEW, "x"))
    t[0] += 61
    with pytest.raises(ApprovalError, match="expired"):
        store.consume(tok, "reply", ap.reply_fields(REVIEW, "x"))


def test_store_never_persists_raw_token(tools, cfg):
    tok = mint(tools)
    assert tok not in cfg.approvals_path.read_text()


@respx.mock
def test_write_enabled_false_blocks_even_with_valid_token(cfg, make_tools):
    t = make_tools(dataclasses.replace(cfg, write_enabled=False))
    route = respx.put(REPLY_URL).mock(return_value=httpx.Response(200, json={}))
    tok = t.store.mint("reply", ap.reply_fields(REVIEW, "x"))
    with pytest.raises(WriteBlocked, match="WRITE_ENABLED"):
        t.post_review_reply(REVIEW, "x", tok)
    assert route.call_count == 0
    log = t.audit.read()
    assert log[-1]["status"] == "blocked"
    # the token was NOT burned by a blocked write
    t2 = make_tools(dataclasses.replace(cfg, write_enabled=True))
    respx.put(REPLY_URL).mock(return_value=httpx.Response(200, json={}))
    assert t2.post_review_reply(REVIEW, "x", tok)["ok"]


def test_default_config_is_read_only(monkeypatch):
    from gbp_mcp.config import Config
    monkeypatch.delenv("WRITE_ENABLED", raising=False)
    assert Config.from_env().write_enabled is False
    monkeypatch.setenv("WRITE_ENABLED", "true")
    assert Config.from_env().write_enabled is True


@respx.mock
def test_daily_cap_enforced(tools):
    respx.put(url__regex=r".*/reply$").mock(return_value=httpx.Response(200, json={}))
    for i in range(3):
        rid = f"accounts/1/locations/2/reviews/R{i}"
        tools.post_review_reply(rid, "ok", tools.store.mint("reply", ap.reply_fields(rid, "ok")))
    rid = "accounts/1/locations/2/reviews/R9"
    with pytest.raises(WriteBlocked, match="cap"):
        tools.post_review_reply(rid, "ok", tools.store.mint("reply", ap.reply_fields(rid, "ok")))


@respx.mock
def test_every_write_is_audited_without_token(tools):
    respx.put(REPLY_URL).mock(return_value=httpx.Response(200, json={}))
    tok = mint(tools)
    tools.post_review_reply(REVIEW, "Thanks!", tok)
    raw = tools.audit.path.read_text()
    assert '"status": "ok"' in raw and "post_review_reply" in raw
    assert tok not in raw


@respx.mock
def test_failed_write_is_audited_and_token_stays_burned(tools):
    respx.put(REPLY_URL).mock(return_value=httpx.Response(403, json={"error": {"message": "nope"}}))
    tok = mint(tools)
    with pytest.raises(Exception):
        tools.post_review_reply(REVIEW, "Thanks!", tok)
    assert tools.audit.read()[-1]["status"] == "failed"
    with pytest.raises(ApprovalError, match="already used"):
        tools.post_review_reply(REVIEW, "Thanks!", tok)


@respx.mock
def test_create_post_gated_and_body_shape(tools):
    route = respx.post(f"{V4_API}/accounts/1/locations/2/localPosts").mock(return_value=httpx.Response(200, json={"name": "p"}))
    cta = {"actionType": "learn_more", "url": "https://example.com"}
    norm = {"actionType": "LEARN_MORE", "url": "https://example.com"}
    tok = tools.store.mint("post", ap.post_fields("accounts/1/locations/2", "Spring offer", norm))
    assert tools.create_post("accounts/1/locations/2", "Spring offer", cta, tok)["ok"]
    sent = route.calls[0].request.content.decode()
    assert '"summary":"Spring offer"' in sent and "LEARN_MORE" in sent


def test_create_post_validates_input(tools):
    with pytest.raises(ToolError):
        tools.create_post("accounts/1/locations/2", "", None, "t")
    with pytest.raises(ToolError):
        tools.create_post("accounts/1/locations/2", "x" * 1501, None, "t")
    with pytest.raises(ToolError, match="actionType"):
        tools.create_post("accounts/1/locations/2", "hi", {"actionType": "BUY_NOW"}, "t")


def test_reply_validates_review_id_shape(tools):
    with pytest.raises(ToolError, match="review_id"):
        tools.post_review_reply("R1", "hi", "t")


def test_approve_over_mcp_disabled_by_default(tools):
    with pytest.raises(ApprovalError, match="disabled"):
        tools.approve_reply(REVIEW, "hi")


def test_approve_over_mcp_when_opted_in(cfg, make_tools):
    t = make_tools(dataclasses.replace(cfg, allow_mcp_approve=True))
    out = t.approve_reply(REVIEW, "hi")
    t.store.consume(out["approve_token"], "reply", ap.reply_fields(REVIEW, "hi"))


def test_cli_mints_token_for_exact_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GBP_MCP_HOME", str(tmp_path))
    assert ap.main(["reply", "--review-id", REVIEW, "--text", "Hello", "--yes"]) == 0
    out = capsys.readouterr().out
    token = [l for l in out.splitlines() if l.startswith("approve_token:")][0].split(": ")[1]
    store = ApprovalStore(tmp_path / "approvals.json")
    store.consume(token, "reply", ap.reply_fields(REVIEW, "Hello"))
    with pytest.raises(ApprovalError):
        store.consume(token, "reply", ap.reply_fields(REVIEW, "Hello"))


def test_cli_refuses_without_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("GBP_MCP_HOME", str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert ap.main(["reply", "--review-id", REVIEW, "--text", "Hello"]) == 1
    assert not (tmp_path / "approvals.json").exists()
