import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from gbp_mcp import cli
from gbp_mcp.client import ACCOUNT_API, INFO_API, PERF_API, V4_API
from gbp_mcp.completeness import render_html, render_text, score_location
from gbp_mcp.tools import ToolError, parse_date_range, parse_since
from conftest import REVIEW

ROOT = Path(__file__).resolve().parent.parent
LOC = "accounts/1/locations/2"


def review(i, stars, comment="ok", replied=False, update="2026-10-10T00:00:00Z"):
    r = {"name": f"{LOC}/reviews/R{i}", "reviewer": {"displayName": f"Person {i}"},
         "starRating": stars, "comment": comment, "updateTime": update}
    if replied:
        r["reviewReply"] = {"comment": "thanks"}
    return r


@respx.mock
def test_list_reviews_paginates_filters_and_counts(tools):
    route = respx.get(f"{V4_API}/{LOC}/reviews")
    route.side_effect = [
        httpx.Response(200, json={"reviews": [review(1, "FIVE"), review(2, "ONE")], "nextPageToken": "n"}),
        httpx.Response(200, json={"reviews": [review(3, "FOUR", replied=True),
                                              review(4, "FIVE", update="2026-09-01T00:00:00Z")]}),
    ]
    out = tools.list_reviews(LOC, since="7d", min_stars=4)
    ids = [r["review_id"] for r in out["reviews"]]
    assert ids == [f"{LOC}/reviews/R1", f"{LOC}/reviews/R3"]
    assert out["unreplied"] == 1
    assert out["reviews"][0]["stars"] == 5


@respx.mock
def test_account_autodiscovery_when_single_account(cfg, make_tools):
    import dataclasses
    t = make_tools(dataclasses.replace(cfg, account_id=""))
    respx.get(f"{ACCOUNT_API}/accounts").mock(return_value=httpx.Response(200, json={"accounts": [{"name": "accounts/9"}]}))
    r = respx.get(f"{V4_API}/accounts/9/locations/2/reviews").mock(return_value=httpx.Response(200, json={"reviews": []}))
    assert t.list_reviews("locations/2")["count"] == 0
    assert r.called


@respx.mock
def test_ambiguous_accounts_raise(cfg, make_tools):
    import dataclasses
    t = make_tools(dataclasses.replace(cfg, account_id=""))
    respx.get(f"{ACCOUNT_API}/accounts").mock(return_value=httpx.Response(200, json={"accounts": [{"name": "accounts/1"}, {"name": "accounts/2"}]}))
    with pytest.raises(ToolError, match="GBP_ACCOUNT_ID"):
        t.list_reviews("2")


@respx.mock
def test_list_locations_uses_read_mask(tools):
    route = respx.get(f"{INFO_API}/accounts/1/locations").mock(
        return_value=httpx.Response(200, json={"locations": [{"name": "locations/2", "title": "X"}]}))
    out = tools.list_locations()
    assert out["count"] == 1
    assert "readMask" in route.calls[0].request.url.params


@respx.mock
def test_draft_reply_never_writes_and_is_deterministic(tools):
    respx.get(f"{V4_API}/{REVIEW}").mock(return_value=httpx.Response(200, json=review(1, "FIVE")))
    put = respx.put(url__regex=r".*/reply$").mock(return_value=httpx.Response(200, json={}))
    a = tools.draft_review_reply(REVIEW, "friendly")
    b = tools.draft_review_reply(REVIEW, "friendly")
    assert a["draft"] == b["draft"] and "Person" in a["draft"]
    assert put.call_count == 0
    assert a["source"] == "template"


@respx.mock
def test_draft_reply_tone_and_negative_bucket(tools):
    r = review(1, "ONE", "Terrible")
    respx.get(f"{V4_API}/{REVIEW}").mock(return_value=httpx.Response(200, json=r))
    neg = tools.draft_review_reply(REVIEW, "professional")["draft"]
    assert "apologise" in neg
    with pytest.raises(ToolError, match="tone"):
        tools.draft_review_reply(REVIEW, "sarcastic")


@respx.mock
def test_draft_reply_rating_only_uses_no_comment_template(tools):
    respx.get(f"{V4_API}/{REVIEW}").mock(return_value=httpx.Response(200, json=review(1, "FIVE", "")))
    assert "rating" in tools.draft_review_reply(REVIEW)["draft"].lower()


@respx.mock
def test_llm_hook_is_applied_and_failure_is_safe(cfg, make_tools, monkeypatch, tmp_path):
    import dataclasses, sys
    (tmp_path / "myhook.py").write_text("def rewrite(review, draft, tone):\n    return draft.upper()\n"
                                        "def boom(review, draft, tone):\n    raise RuntimeError('x')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    respx.get(f"{V4_API}/{REVIEW}").mock(return_value=httpx.Response(200, json=review(1, "FIVE")))
    t = make_tools(dataclasses.replace(cfg, llm_hook="myhook:rewrite"))
    out = t.draft_review_reply(REVIEW)
    assert out["draft"].isupper() and out["source"] == "template+llm_hook"
    t2 = make_tools(dataclasses.replace(cfg, llm_hook="myhook:boom"))
    assert "llm hook failed" in t2.draft_review_reply(REVIEW)["draft"]


@respx.mock
def test_get_performance_sums_and_uses_documented_params(tools):
    route = respx.get(f"{PERF_API}/locations/2:getDailyMetricsTimeSeries").mock(
        return_value=httpx.Response(200, json={"timeSeries": {"datedValues": [
            {"date": {"year": 2026, "month": 10, "day": 1}, "value": "4"},
            {"date": {"year": 2026, "month": 10, "day": 2}}]}}))
    out = tools.get_performance(LOC, "website_clicks", "2026-10-01..2026-10-07")
    assert out["metrics"]["WEBSITE_CLICKS"] == {"total": 4, "days_with_data": 2}
    q = route.calls[0].request.url.params
    assert q["dailyMetric"] == "WEBSITE_CLICKS"
    assert q["dailyRange.start_date.day"] == "1" and q["dailyRange.end_date.day"] == "7"


def test_performance_validation(tools):
    with pytest.raises(ToolError, match="Unknown metrics"):
        tools.get_performance(LOC, ["NOT_A_METRIC"])
    with pytest.raises(ToolError):
        tools.get_performance(LOC, "CALL_CLICKS", "yesterday-ish")


def test_date_helpers():
    assert parse_date_range("last_7_days", date(2026, 10, 12)) == (date(2026, 10, 5), date(2026, 10, 11))
    with pytest.raises(ToolError):
        parse_date_range("2026-10-09..2026-10-01", date(2026, 10, 12))
    assert parse_since(None, None) is None
    with pytest.raises(ToolError):
        parse_since("soon", None)


@respx.mock
def test_audit_completeness_end_to_end_tolerates_missing_qa(tools):
    respx.get(f"{INFO_API}/locations/2").mock(return_value=httpx.Response(200, json={
        "title": "T", "websiteUri": "https://e.com", "phoneNumbers": {"primaryPhone": "1"}}))
    respx.get(f"{V4_API}/{LOC}/media").mock(return_value=httpx.Response(200, json={"totalMediaItemCount": 30}))
    respx.get(f"{V4_API}/{LOC}/questions").mock(return_value=httpx.Response(404, json={"error": {"message": "gone"}}))
    respx.get(f"{V4_API}/{LOC}/reviews").mock(return_value=httpx.Response(200, json={"reviews": [review(1, "FIVE", replied=True)]}))
    respx.get(f"{INFO_API}/locations/2/attributes").mock(return_value=httpx.Response(200, json={"attributes": []}))
    rep = tools.audit_completeness(LOC)
    assert "qa" in rep["skipped"]
    photos = [i for i in rep["items"] if i["key"] == "photos"][0]
    assert photos["earned"] == 15
    assert 0 <= rep["score"] <= 100


# ---------- completeness golden tests ----------

def sample():
    return json.loads((ROOT / "examples" / "sample-location.json").read_text(encoding="utf-8"))


def test_golden_sample_score_and_items():
    d = sample()
    rep = score_location(d["location"], d["media_count"], d["attributes"], d["questions"], d["reviews"])
    got = {i["key"]: i["earned"] for i in rep["items"]}
    assert got == {"categories": 5, "description": 3, "hours": 7.1, "special_hours": 0,
                   "phone": 5, "website": 5, "photos": 9, "services": 4, "attributes": 2,
                   "qa": 0, "review_response_rate": 10.0}
    assert rep["score"] == 50
    assert rep["fixes"][0]["key"] == "review_response_rate"
    assert rep["fixes"][0]["priority"] == "high"


def test_golden_perfect_profile_scores_100():
    loc = {"title": "Perfect", "categories": {"primaryCategory": {"name": "c"}, "additionalCategories": [{}, {}]},
           "profile": {"description": "d" * 300},
           "regularHours": {"periods": [{"openDay": d} for d in
                                        ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]]},
           "specialHours": {"specialHourPeriods": [{}]}, "phoneNumbers": {"primaryPhone": "1"},
           "websiteUri": "https://x.com", "serviceItems": [{}] * 6}
    rep = score_location(loc, 40, {"attributes": [{}, {}, {}]}, {"totalSize": 3},
                         [{"reviewReply": {"comment": "a"}}] * 10)
    assert rep["score"] == 100 and rep["fixes"] == []


def test_golden_empty_profile_scores_low_with_all_fixes():
    rep = score_location({}, 0, {"attributes": []}, {"totalSize": 0}, [{}])
    assert rep["score"] == 0
    assert len(rep["fixes"]) == 11
    assert rep["fixes"][0]["key"] == "review_response_rate"


def test_skipped_items_do_not_penalise():
    rep = score_location({"websiteUri": "x", "phoneNumbers": {"primaryPhone": "1"}}, None, None, None, None)
    assert set(rep["skipped"]) == {"photos", "attributes", "qa", "review_response_rate"}
    live = [i for i in rep["items"] if i["status"] != "skipped"]
    assert rep["score"] == round(100 * sum(i["earned"] for i in live) / sum(i["max"] for i in live))


def test_weights_sum_to_100():
    from gbp_mcp.completeness import WEIGHTS
    assert sum(WEIGHTS.values()) == 100


def test_description_tiers():
    f = lambda n: [i for i in score_location({"profile": {"description": "x" * n}})["items"] if i["key"] == "description"][0]["earned"]
    assert (f(0), f(50), f(150), f(300)) == (0, 3, 6, 10)


def test_renderers_escape_and_include_score():
    rep = score_location({"title": "<script>x</script>"}, 0, {}, {"totalSize": 0}, [])
    html = render_html(rep)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert f"{rep['score']}/100" in render_text(rep)


def test_example_html_matches_current_renderer():
    html = (ROOT / "examples" / "sample-audit.html").read_text(encoding="utf-8")
    assert html == render_html(cli.run_demo())


# ---------- n8n ----------

def test_n8n_workflow_validates():
    import importlib.util
    spec = importlib.util.spec_from_file_location("v", ROOT / "scripts" / "validate_n8n.py")
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    wf = json.loads((ROOT / "n8n" / "weekly-review-digest.json").read_text(encoding="utf-8"))
    assert v.validate(wf) == []
    assert any(n["type"].endswith("scheduleTrigger") for n in wf["nodes"])


def test_n8n_validator_catches_problems():
    import importlib.util
    spec = importlib.util.spec_from_file_location("v", ROOT / "scripts" / "validate_n8n.py")
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    bad = {"nodes": [{"name": "A", "type": "n8n-nodes-base.set", "typeVersion": 1, "position": [0, 0],
                      "parameters": {"t": "ya29.abcdefg"}, "id": "1"}],
           "connections": {"A": {"main": [[{"node": "Ghost", "type": "main", "index": 0}]]}}}
    errs = " ".join(v.validate(bad))
    assert "no trigger" in errs and "Ghost" in errs and "secret" in errs


def test_oauth_helpers():
    import importlib.util
    spec = importlib.util.spec_from_file_location("o", ROOT / "scripts" / "oauth_setup.py")
    o = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(o)
    verifier, challenge = o.pkce_pair()
    assert len(verifier) >= 43 and "=" not in challenge
    url = o.build_auth_url("cid", "http://127.0.0.1:1", "st", challenge)
    assert "access_type=offline" in url and "business.manage" in url and "code_challenge_method=S256" in url


def test_server_registers_expected_tools(tools):
    import asyncio
    from gbp_mcp.server import build_server
    names = {t.name for t in asyncio.run(build_server(tools).list_tools())}
    assert names == {"list_accounts", "list_locations", "get_location", "list_reviews",
                     "draft_review_reply", "approve_reply", "post_review_reply",
                     "create_post", "get_performance", "audit_completeness"}
