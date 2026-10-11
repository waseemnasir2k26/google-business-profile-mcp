"""Thin httpx client for the official Business Profile APIs (user OAuth only).

Hosts/versions (checked against Google's REST reference, Oct 2026):
  Account Management   mybusinessaccountmanagement.googleapis.com /v1
  Business Information mybusinessbusinessinformation.googleapis.com /v1
  Reviews, media, posts, Q&A  mybusiness.googleapis.com /v4
  Performance          businessprofileperformance.googleapis.com /v1
"""
from __future__ import annotations

import time
from typing import Callable

import httpx

from .auth import TokenProvider

ACCOUNT_API = "https://mybusinessaccountmanagement.googleapis.com/v1"
INFO_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
V4_API = "https://mybusiness.googleapis.com/v4"
PERF_API = "https://businessprofileperformance.googleapis.com/v1"

LOCATION_READ_MASK = ("name,title,categories,profile,regularHours,specialHours,"
                      "phoneNumbers,websiteUri,serviceItems,storefrontAddress,metadata")


class GBPError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Google API error {status}: {message}")
        self.status = status


class GBPClient:
    def __init__(self, tokens: TokenProvider, http: httpx.Client | None = None,
                 max_retries: int = 2, backoff: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep):
        self.tokens = tokens
        self.http = http or httpx.Client(timeout=30)
        self.max_retries = max_retries
        self.backoff = backoff
        self.sleep = sleep

    def request(self, method: str, url: str, params: dict | None = None,
                json: dict | None = None) -> dict:
        refreshed = False
        attempt = 0
        while True:
            headers = {"Authorization": f"Bearer {self.tokens.access_token()}"}
            resp = self.http.request(method, url, params=params, json=json, headers=headers)
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self.tokens.invalidate()
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                attempt += 1
                self.sleep(self.backoff * (2 ** (attempt - 1)))
                continue
            if resp.status_code >= 400:
                try:
                    msg = resp.json().get("error", {}).get("message", resp.text[:200])
                except ValueError:
                    msg = resp.text[:200]
                raise GBPError(resp.status_code, msg)
            return resp.json() if resp.content else {}

    def paginate(self, url: str, key: str, params: dict | None = None,
                 max_pages: int = 50) -> list[dict]:
        out: list[dict] = []
        params = dict(params or {})
        for _ in range(max_pages):
            body = self.request("GET", url, params=params)
            out.extend(body.get(key, []))
            token = body.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return out

    # --- reads -----------------------------------------------------------
    def list_accounts(self) -> list[dict]:
        return self.paginate(f"{ACCOUNT_API}/accounts", "accounts", {"pageSize": 20})

    def list_locations(self, account: str,
                       read_mask: str = "name,title,storefrontAddress,websiteUri") -> list[dict]:
        return self.paginate(f"{INFO_API}/{account}/locations", "locations",
                             {"readMask": read_mask, "pageSize": 100})

    def get_location(self, name: str) -> dict:
        return self.request("GET", f"{INFO_API}/{name}", {"readMask": LOCATION_READ_MASK})

    def get_attributes(self, location: str) -> dict:
        return self.request("GET", f"{INFO_API}/{location}/attributes")

    def list_reviews(self, v4_parent: str) -> list[dict]:
        return self.paginate(f"{V4_API}/{v4_parent}/reviews", "reviews",
                             {"pageSize": 50, "orderBy": "updateTime desc"})

    def get_review(self, review_name: str) -> dict:
        return self.request("GET", f"{V4_API}/{review_name}")

    def media_count(self, v4_parent: str) -> int:
        body = self.request("GET", f"{V4_API}/{v4_parent}/media", {"pageSize": 1})
        return int(body.get("totalMediaItemCount", len(body.get("mediaItems", []))))

    def list_questions(self, v4_parent: str) -> dict:
        return self.request("GET", f"{V4_API}/{v4_parent}/questions", {"pageSize": 10})

    def daily_metric(self, location: str, metric: str, start: tuple[int, int, int],
                     end: tuple[int, int, int]) -> dict:
        p: dict = {"dailyMetric": metric}
        for tag, (y, m, d) in (("start_date", start), ("end_date", end)):
            p[f"dailyRange.{tag}.year"] = y
            p[f"dailyRange.{tag}.month"] = m
            p[f"dailyRange.{tag}.day"] = d
        return self.request("GET", f"{PERF_API}/{location}:getDailyMetricsTimeSeries", p)

    # --- writes (callers MUST go through the approve gate in tools.py) ---
    def put_reply(self, review_name: str, text: str) -> dict:
        return self.request("PUT", f"{V4_API}/{review_name}/reply", json={"comment": text})

    def create_local_post(self, v4_parent: str, body: dict) -> dict:
        return self.request("POST", f"{V4_API}/{v4_parent}/localPosts", json=body)
