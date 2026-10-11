import httpx
import pytest
import respx

from gbp_mcp.auth import AuthError, TokenProvider, save_token_file, load_token_file
from gbp_mcp.client import GBPClient, GBPError, ACCOUNT_API, V4_API
from gbp_mcp.config import Config
from conftest import StaticTokens


@respx.mock
def test_pagination_follows_next_page_token():
    route = respx.get(f"{ACCOUNT_API}/accounts")
    route.side_effect = [
        httpx.Response(200, json={"accounts": [{"name": "accounts/1"}], "nextPageToken": "p2"}),
        httpx.Response(200, json={"accounts": [{"name": "accounts/2"}]}),
    ]
    c = GBPClient(StaticTokens(), httpx.Client())
    accounts = c.list_accounts()
    assert [a["name"] for a in accounts] == ["accounts/1", "accounts/2"]
    assert route.call_count == 2
    assert route.calls[1].request.url.params["pageToken"] == "p2"
    assert route.calls[0].request.headers["authorization"] == "Bearer test-access"


@respx.mock
def test_401_triggers_one_token_refresh_then_succeeds():
    tokens = StaticTokens()
    route = respx.get(f"{V4_API}/accounts/1/locations/2/reviews/R1")
    route.side_effect = [httpx.Response(401, json={"error": {"message": "expired"}}),
                         httpx.Response(200, json={"name": "ok"})]
    c = GBPClient(tokens, httpx.Client())
    assert c.get_review("accounts/1/locations/2/reviews/R1")["name"] == "ok"
    assert tokens.invalidated == 1


@respx.mock
def test_persistent_401_raises_after_single_retry():
    respx.get(f"{V4_API}/x").mock(return_value=httpx.Response(401, json={"error": {"message": "no"}}))
    c = GBPClient(StaticTokens(), httpx.Client())
    with pytest.raises(GBPError) as e:
        c.request("GET", f"{V4_API}/x")
    assert e.value.status == 401


@respx.mock
def test_retries_on_429_with_backoff():
    sleeps = []
    route = respx.get(f"{V4_API}/y")
    route.side_effect = [httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"a": 1})]
    c = GBPClient(StaticTokens(), httpx.Client(), sleep=sleeps.append)
    assert c.request("GET", f"{V4_API}/y") == {"a": 1}
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_gives_up_after_max_retries():
    respx.get(f"{V4_API}/z").mock(return_value=httpx.Response(500, json={"error": {"message": "boom"}}))
    c = GBPClient(StaticTokens(), httpx.Client(), max_retries=1, sleep=lambda s: None)
    with pytest.raises(GBPError, match="boom"):
        c.request("GET", f"{V4_API}/z")


@respx.mock
def test_token_refresh_flow_and_caching(tmp_path):
    cfg = Config(home=tmp_path, client_id="cid", client_secret="sec", refresh_token="rt")
    clock = [1000.0]
    route = respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "A1", "expires_in": 3600}))
    tp = TokenProvider(cfg, httpx.Client(), now=lambda: clock[0])
    assert tp.access_token() == "A1"
    assert tp.access_token() == "A1"
    assert route.call_count == 1
    body = route.calls[0].request.content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=rt" in body
    clock[0] += 4000
    tp.access_token()
    assert route.call_count == 2


def test_missing_credentials_raises_helpful_error(tmp_path):
    tp = TokenProvider(Config(home=tmp_path), httpx.Client())
    with pytest.raises(AuthError, match="oauth_setup"):
        tp.access_token()


@respx.mock
def test_refresh_failure_surfaces(tmp_path):
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    tp = TokenProvider(Config(home=tmp_path, client_id="a", client_secret="b", refresh_token="c"), httpx.Client())
    with pytest.raises(AuthError, match="400"):
        tp.access_token()


def test_token_file_roundtrip_and_permissions(tmp_path):
    p = tmp_path / "sub" / "token.json"
    save_token_file(p, {"refresh_token": "r"})
    assert load_token_file(p) == {"refresh_token": "r"}
    import os, stat
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert load_token_file(tmp_path / "missing.json") == {}
