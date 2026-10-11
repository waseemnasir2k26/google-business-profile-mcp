"""Profile completeness scoring (0-100). Pure functions, no network.

Weights sum to 100. Items whose data could not be fetched (for example Q&A,
or review response rate when there are no reviews) are skipped and the score is
normalised over the remaining weight, so missing data never reads as a failure.
"""
from __future__ import annotations

from html import escape

WEIGHTS = {
    "categories": 10, "description": 10, "hours": 10, "special_hours": 5,
    "phone": 5, "website": 5, "photos": 15, "services": 10,
    "attributes": 5, "qa": 5, "review_response_rate": 20,
}
LABELS = {
    "categories": "Categories", "description": "Description length",
    "hours": "Opening hours", "special_hours": "Special hours",
    "phone": "Phone number", "website": "Website", "photos": "Photos",
    "services": "Services", "attributes": "Attributes", "qa": "Q&A",
    "review_response_rate": "Review response rate",
}
FIX_TEXT = {
    "categories": "Set a precise primary category and add 2+ secondary categories.",
    "description": "Write a 250-750 character description with your services, area and a call to action.",
    "hours": "Add regular opening hours for all 7 days (mark closed days explicitly).",
    "special_hours": "Add special hours for upcoming holidays so customers see accurate times.",
    "phone": "Add a primary phone number.",
    "website": "Add your website URL (use a UTM-tagged link to track visits).",
    "photos": "Upload photos: aim for 25+ covering exterior, interior, team and work examples.",
    "services": "List 6+ services with short descriptions.",
    "attributes": "Fill in attributes (payment methods, accessibility, amenities) that apply.",
    "qa": "Seed 3-5 Q&A entries with your most common customer questions.",
    "review_response_rate": "Reply to unanswered reviews, newest first, aiming for 90%+ answered.",
}


def _num(x: float):
    x = round(x, 1)
    return int(x) if x == int(x) else x


def _item(key: str, earned: float, detail: str) -> dict:
    mx = WEIGHTS[key]
    status = "pass" if earned >= mx else ("fail" if earned == 0 else "partial")
    return {"key": key, "label": LABELS[key], "max": mx, "earned": _num(earned),
            "status": status, "detail": detail}


def _skipped(key: str, why: str) -> dict:
    return {"key": key, "label": LABELS[key], "max": WEIGHTS[key], "earned": 0,
            "status": "skipped", "detail": why}


def _tier(n: int, tiers: list[tuple[int, float]]) -> float:
    """tiers: ascending (min_count, points); returns points of the highest tier reached."""
    pts = 0.0
    for lo, p in tiers:
        if n >= lo:
            pts = p
    return pts


def score_location(location: dict, media_count: int | None = None,
                   attributes: dict | None = None, questions: dict | None = None,
                   reviews: list[dict] | None = None) -> dict:
    items: list[dict] = []

    cats = location.get("categories") or {}
    n_extra = len(cats.get("additionalCategories") or [])
    primary = bool(cats.get("primaryCategory"))
    pts = (5 if primary else 0) + _tier(n_extra, [(1, 3), (2, 5)])
    items.append(_item("categories", pts,
                       f"primary={'yes' if primary else 'no'}, secondary={n_extra}"))

    desc_len = len(((location.get("profile") or {}).get("description")) or "")
    pts = _tier(desc_len, [(1, 3), (100, 6), (250, 10)])
    items.append(_item("description", pts, f"{desc_len} characters (target 250-750)"))

    periods = (location.get("regularHours") or {}).get("periods") or []
    days = {p.get("openDay") for p in periods if p.get("openDay")}
    items.append(_item("hours", round(10 * min(len(days), 7) / 7, 1), f"{len(days)}/7 days set"))

    sp = (location.get("specialHours") or {}).get("specialHourPeriods") or []
    items.append(_item("special_hours", 5 if sp else 0, f"{len(sp)} special-hours entries"))

    phones = location.get("phoneNumbers") or {}
    items.append(_item("phone", 5 if phones.get("primaryPhone") else 0,
                       "primary phone set" if phones.get("primaryPhone") else "no primary phone"))

    items.append(_item("website", 5 if location.get("websiteUri") else 0,
                       location.get("websiteUri") or "no website"))

    if media_count is None:
        items.append(_skipped("photos", "media count unavailable"))
    else:
        items.append(_item("photos", _tier(media_count, [(1, 5), (5, 9), (10, 12), (25, 15)]),
                           f"{media_count} photos (target 25+)"))

    n_services = len(location.get("serviceItems") or [])
    items.append(_item("services", _tier(n_services, [(1, 4), (3, 7), (6, 10)]),
                       f"{n_services} services (target 6+)"))

    if attributes is None:
        items.append(_skipped("attributes", "attributes unavailable"))
    else:
        n_attr = len(attributes.get("attributes") or [])
        items.append(_item("attributes", _tier(n_attr, [(1, 2), (3, 5)]), f"{n_attr} attributes set"))

    if questions is None:
        items.append(_skipped("qa", "Q&A not available for this account/API"))
    else:
        n_q = int(questions.get("totalSize", len(questions.get("questions") or [])))
        items.append(_item("qa", 5 if n_q >= 1 else 0, f"{n_q} questions"))

    if not reviews:
        items.append(_skipped("review_response_rate", "no reviews to measure"))
    else:
        replied = sum(1 for r in reviews if r.get("reviewReply") or r.get("replied"))
        rate = replied / len(reviews)
        items.append(_item("review_response_rate", round(20 * rate, 1),
                           f"{replied}/{len(reviews)} reviews answered ({round(rate * 100)}%)"))

    live = [i for i in items if i["status"] != "skipped"]
    possible = sum(i["max"] for i in live)
    earned = sum(i["earned"] for i in live)
    score = round(100 * earned / possible) if possible else 0

    fixes = []
    for i in sorted(live, key=lambda x: x["max"] - x["earned"], reverse=True):
        lost = _num(i["max"] - i["earned"])
        if lost <= 0:
            continue
        priority = "high" if lost >= 8 else ("medium" if lost >= 4 else "low")
        fixes.append({"key": i["key"], "priority": priority, "points_available": lost,
                      "action": FIX_TEXT[i["key"]]})
    return {"title": location.get("title", ""), "score": score, "items": items,
            "skipped": [i["key"] for i in items if i["status"] == "skipped"], "fixes": fixes}


def render_text(report: dict) -> str:
    lines = [f"Profile completeness: {report['title'] or 'location'}  -> {report['score']}/100", ""]
    for i in report["items"]:
        mark = {"pass": "[x]", "partial": "[~]", "fail": "[ ]", "skipped": "[-]"}[i["status"]]
        lines.append(f"{mark} {i['label']:<22} {i['earned']:>4}/{i['max']:<3} {i['detail']}")
    if report["fixes"]:
        lines += ["", "Fix list (biggest wins first):"]
        for n, f in enumerate(report["fixes"], 1):
            lines.append(f"{n}. [{f['priority']}] +{f['points_available']} pts - {f['action']}")
    return "\n".join(lines)


def render_html(report: dict) -> str:
    rows = "".join(
        f"<tr class='{i['status']}'><td>{escape(i['label'])}</td><td>{i['earned']}/{i['max']}</td>"
        f"<td>{escape(i['detail'])}</td></tr>" for i in report["items"])
    fixes = "".join(
        f"<li><b>{escape(f['priority'])}</b> (+{f['points_available']} pts) {escape(f['action'])}</li>"
        for f in report["fixes"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Profile completeness audit - {escape(report['title'])}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 16px;color:#1a1a1a;background:#fff}}
.score{{font-size:3rem;font-weight:700}}table{{border-collapse:collapse;width:100%}}
td{{border-bottom:1px solid #ddd;padding:6px 8px}}tr.pass td:first-child{{color:#157f3b}}
tr.fail td:first-child{{color:#b3261e}}tr.partial td:first-child{{color:#9a6700}}tr.skipped{{color:#777}}
.note{{color:#555;font-size:.9rem}}</style></head><body>
<h1>Profile completeness audit</h1><p>{escape(report['title'])}</p>
<div class="score">{report['score']}<span style="font-size:1.2rem">/100</span></div>
<table>{rows}</table><h2>Prioritised fix list</h2><ol>{fixes}</ol>
<p class="note">Generated by google-business-profile-mcp from the bundled sample data (not a real business).</p>
</body></html>"""
