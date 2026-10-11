"""Tool logic (plain Python, MCP-agnostic so it is testable without a server)."""
from __future__ import annotations

import hashlib
import importlib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import approve as ap
from .approve import ApprovalError, ApprovalStore
from .audit import AuditLog
from .client import GBPClient
from .completeness import score_location
from .config import Config

STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
METRICS = {
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS", "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS", "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "BUSINESS_CONVERSATIONS", "BUSINESS_DIRECTION_REQUESTS", "CALL_CLICKS",
    "WEBSITE_CLICKS", "BUSINESS_BOOKINGS", "BUSINESS_FOOD_ORDERS", "BUSINESS_FOOD_MENU_CLICKS",
}
CTA_TYPES = {"BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"}
MAX_POST_CHARS = 1500
MAX_REPLY_CHARS = 4096
TEMPLATES_PATH = Path(__file__).parent / "templates" / "replies.yaml"


class WriteBlocked(PermissionError):
    pass


class ToolError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_review(r: dict) -> dict:
    reply = r.get("reviewReply") or {}
    return {
        "review_id": r.get("name", ""),
        "reviewer": (r.get("reviewer") or {}).get("displayName", "Anonymous"),
        "stars": STARS.get(r.get("starRating", ""), 0),
        "comment": r.get("comment", ""),
        "create_time": r.get("createTime", ""),
        "update_time": r.get("updateTime", ""),
        "replied": bool(reply),
        "reply": reply.get("comment", ""),
    }


def parse_since(value: str | None, now: datetime) -> datetime | None:
    if not value:
        return None
    m = re.fullmatch(r"(\d+)d", value.strip())
    if m:
        return now - timedelta(days=int(m.group(1)))
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError("since must be ISO date/datetime or like '7d'") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_date_range(spec: str, today: date) -> tuple[date, date]:
    spec = (spec or "last_30_days").strip()
    m = re.fullmatch(r"last_(\d+)_days", spec)
    if m:
        n = int(m.group(1))
        return today - timedelta(days=n), today - timedelta(days=1)
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", spec)
    if m:
        a, b = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        if a > b:
            raise ToolError("date_range start is after end")
        return a, b
    raise ToolError("date_range must be 'last_N_days' or 'YYYY-MM-DD..YYYY-MM-DD'")


class Tools:
    def __init__(self, cfg: Config, client: GBPClient, store: ApprovalStore | None = None,
                 audit: AuditLog | None = None, now=_utcnow):
        self.cfg = cfg
        self.client = client
        self.store = store or ApprovalStore(cfg.approvals_path, cfg.approval_ttl_seconds)
        self.audit = audit or AuditLog(cfg.audit_path)
        self.now = now

    # --- resource name helpers -------------------------------------------
    def _v4_parent(self, location: str) -> str:
        location = location.strip().strip("/")
        if location.startswith("accounts/"):
            return location
        loc = location if location.startswith("locations/") else f"locations/{location}"
        account = self.cfg.account_id
        if not account:
            accounts = self.client.list_accounts()
            if len(accounts) != 1:
                raise ToolError("Pass 'accounts/A/locations/L' or set GBP_ACCOUNT_ID "
                                f"({len(accounts)} accounts visible).")
            account = accounts[0]["name"]
        if not account.startswith("accounts/"):
            account = f"accounts/{account}"
        return f"{account}/{loc}"

    @staticmethod
    def _v1_location(location: str) -> str:
        location = location.strip().strip("/")
        if "/locations/" in location:
            location = "locations/" + location.split("/locations/")[1]
        return location if location.startswith("locations/") else f"locations/{location}"

    # --- reads ---------------------------------------------------------------
    def list_accounts(self) -> dict:
        accounts = self.client.list_accounts()
        return {"accounts": [{"name": a.get("name"), "account_name": a.get("accountName"),
                              "type": a.get("type")} for a in accounts]}

    def list_locations(self, account: str = "") -> dict:
        account = account or self.cfg.account_id
        if not account:
            raise ToolError("account is required (accounts/123) or set GBP_ACCOUNT_ID.")
        if not account.startswith("accounts/"):
            account = f"accounts/{account}"
        locs = self.client.list_locations(account)
        return {"locations": locs, "count": len(locs)}

    def get_location(self, location: str) -> dict:
        return self.client.get_location(self._v1_location(location))

    def list_reviews(self, location: str, since: str | None = None,
                     min_stars: int | None = None) -> dict:
        cutoff = parse_since(since, self.now())
        out = []
        for raw in self.client.list_reviews(self._v4_parent(location)):
            r = normalize_review(raw)
            if min_stars is not None and r["stars"] < min_stars:
                continue
            if cutoff and r["update_time"] and _ts(r["update_time"]) < cutoff:
                continue
            out.append(r)
        unreplied = sum(1 for r in out if not r["replied"])
        return {"reviews": out, "count": len(out), "unreplied": unreplied}

    def draft_review_reply(self, review_id: str, tone: str = "friendly",
                           business_name: str = "") -> dict:
        """Returns a DRAFT only. Never writes to Google."""
        templates = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))["tones"]
        if tone not in templates:
            raise ToolError(f"tone must be one of {sorted(templates)}")
        review = normalize_review(self.client.get_review(review_id))
        if review["stars"] >= 4:
            bucket = "positive"
        elif review["stars"] == 3:
            bucket = "neutral"
        else:
            bucket = "negative"
        if not review["comment"].strip():
            bucket = "no_comment"
        options = templates[tone][bucket]
        idx = int(hashlib.sha256(review_id.encode()).hexdigest(), 16) % len(options)
        first = (review["reviewer"].split() or ["there"])[0]
        draft = options[idx].format(name=first)
        biz = business_name or self.cfg.business_name
        if biz:
            draft += f" - {biz}"
        source = "template"
        hook = self.cfg.llm_hook
        if hook:
            draft = self._apply_hook(hook, review, draft, tone)
            source = "template+llm_hook"
        return {"review_id": review_id, "tone": tone, "stars": review["stars"],
                "draft": draft[:MAX_REPLY_CHARS], "source": source,
                "next_step": "A human must approve this exact text (python -m gbp_mcp.approve "
                             "reply ...) before post_review_reply will accept it."}

    @staticmethod
    def _apply_hook(spec: str, review: dict, draft: str, tone: str) -> str:
        """GBP_LLM_HOOK='package.module:function'; function(review, draft, tone) -> str."""
        mod, _, fn = spec.partition(":")
        try:
            result = getattr(importlib.import_module(mod), fn)(review, draft, tone)
        except Exception as exc:  # a broken hook must never block drafting
            return draft + f"\n[llm hook failed: {type(exc).__name__}]"
        return result if isinstance(result, str) and result.strip() else draft

    def get_performance(self, location: str, metrics: list[str] | str,
                        date_range: str = "last_30_days") -> dict:
        if isinstance(metrics, str):
            metrics = [m.strip() for m in metrics.split(",") if m.strip()]
        metrics = [m.upper() for m in metrics]
        bad = [m for m in metrics if m not in METRICS]
        if bad or not metrics:
            raise ToolError(f"Unknown metrics {bad}. Valid: {sorted(METRICS)}")
        start, end = parse_date_range(date_range, self.now().date())
        loc = self._v1_location(location)
        result = {}
        for m in metrics:
            body = self.client.daily_metric(loc, m, (start.year, start.month, start.day),
                                            (end.year, end.month, end.day))
            vals = (body.get("timeSeries") or {}).get("datedValues") or []
            total = sum(int(v.get("value", 0)) for v in vals)
            result[m] = {"total": total, "days_with_data": len(vals)}
        return {"location": loc, "start": start.isoformat(), "end": end.isoformat(), "metrics": result}

    def audit_completeness(self, location: str) -> dict:
        loc = self._v1_location(location)
        data = self.client.get_location(loc)
        parent = None
        try:
            parent = self._v4_parent(location)
        except Exception:
            pass
        media = attrs = questions = reviews = None
        if parent:
            media = self._try(lambda: self.client.media_count(parent))
            questions = self._try(lambda: self.client.list_questions(parent))
            reviews = self._try(lambda: self.client.list_reviews(parent))
        attrs = self._try(lambda: self.client.get_attributes(f"{loc}"))
        return score_location(data, media, attrs, questions, reviews)

    @staticmethod
    def _try(fn):
        try:
            return fn()
        except Exception:
            return None

    # --- approval ----------------------------------------------------------
    def approve_reply(self, review_id: str, text: str) -> dict:
        """Opt-in: only works when GBP_ALLOW_MCP_APPROVE=true. Default is CLI-only approval."""
        if not self.cfg.allow_mcp_approve:
            raise ApprovalError(
                "Approval over MCP is disabled so the model cannot approve itself. A human runs: "
                "python -m gbp_mcp.approve reply --review-id ... --text ... "
                "(or set GBP_ALLOW_MCP_APPROVE=true if your client prompts you per tool call).")
        token = self.store.mint("reply", ap.reply_fields(review_id, text))
        return {"approve_token": token, "expires_in_seconds": self.cfg.approval_ttl_seconds}

    # --- gated writes ------------------------------------------------------
    def _guard(self, action: str, kind: str, fields: dict, token: str) -> None:
        if not self.cfg.write_enabled:
            self.audit.append(action, "blocked", reason="WRITE_ENABLED=false", **_log_fields(fields))
            raise WriteBlocked("Writes are disabled (WRITE_ENABLED=false). Set WRITE_ENABLED=true to allow.")
        if self.audit.ok_writes_today() >= self.cfg.daily_write_cap:
            self.audit.append(action, "blocked", reason="daily cap", **_log_fields(fields))
            raise WriteBlocked(f"Daily write cap reached ({self.cfg.daily_write_cap}).")
        try:
            self.store.consume(token, kind, fields)
        except ApprovalError as exc:
            self.audit.append(action, "blocked", reason=str(exc), **_log_fields(fields))
            raise

    def post_review_reply(self, review_id: str, text: str, approve_token: str = "") -> dict:
        text = (text or "").strip()
        if not text or len(text) > MAX_REPLY_CHARS:
            raise ToolError(f"text must be 1-{MAX_REPLY_CHARS} characters")
        if not re.fullmatch(r"accounts/[^/]+/locations/[^/]+/reviews/[^/]+", review_id):
            raise ToolError("review_id must be the full name accounts/A/locations/L/reviews/R")
        fields = ap.reply_fields(review_id, text)
        self._guard("post_review_reply", "reply", fields, approve_token)
        try:
            resp = self.client.put_reply(review_id, text)
        except Exception as exc:
            self.audit.append("post_review_reply", "failed", error=str(exc), **_log_fields(fields))
            raise
        self.audit.append("post_review_reply", "ok", **_log_fields(fields))
        return {"ok": True, "reply": resp}

    def create_post(self, location: str, text: str, cta: dict | None = None,
                    approve_token: str = "") -> dict:
        text = (text or "").strip()
        if not text or len(text) > MAX_POST_CHARS:
            raise ToolError(f"text must be 1-{MAX_POST_CHARS} characters")
        if cta:
            if str(cta.get("actionType", "")).upper() not in CTA_TYPES:
                raise ToolError(f"cta.actionType must be one of {sorted(CTA_TYPES)}")
            cta = {"actionType": cta["actionType"].upper(), **({"url": cta["url"]} if cta.get("url") else {})}
        parent = self._v4_parent(location)
        fields = ap.post_fields(parent, text, cta)
        self._guard("create_post", "post", fields, approve_token)
        body = {"languageCode": "en", "summary": text, "topicType": "STANDARD"}
        if cta:
            body["callToAction"] = cta
        try:
            resp = self.client.create_local_post(parent, body)
        except Exception as exc:
            self.audit.append("create_post", "failed", error=str(exc), **_log_fields(fields))
            raise
        self.audit.append("create_post", "ok", **_log_fields(fields))
        return {"ok": True, "post": resp}


def _log_fields(fields: dict) -> dict:
    """Audit entries keep the target and a text preview/hash, never the token."""
    text = fields.get("text", "")
    return {"target": fields.get("review_id") or fields.get("location"),
            "text_preview": text[:120],
            "text_sha256": hashlib.sha256(text.encode()).hexdigest()[:16]}
