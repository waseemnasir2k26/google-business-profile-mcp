# Google Business Profile MCP Server for Local SEO

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-stdio-purple)

**Let Claude answer "how are my reviews this week?" and draft replies, without ever auto-posting.**

A Model Context Protocol (MCP) server for your Google Business Profile (formerly Google My Business). It reads reviews, drafts replies, pulls performance metrics and scores your profile for local SEO. Every write (reply or post) is blocked by default and needs a single-use approval token that a human mints for that exact text.

> **Google approval required.** The Business Profile APIs are not open to everyone. You must request and receive API access for your Google Cloud project from Google before any call works. Until then the server cannot reach your data. The offline demo and the test suite do not need access. See [Google's prerequisites](https://developers.google.com/my-business/content/prerequisites).

## 60-second quickstart

```bash
git clone https://github.com/waseemnasir2k26/google-business-profile-mcp
cd google-business-profile-mcp
pip install -e .

# 1. See the audit on bundled sample data (no Google account needed)
python -m gbp_mcp.cli

# 2. Connect your own account (needs an approved Google Cloud project + Desktop-app OAuth client)
export GBP_CLIENT_ID=... GBP_CLIENT_SECRET=...
python scripts/oauth_setup.py

# 3. Add to Claude Desktop (see claude_desktop_config.example.json), then ask:
#    "How are my reviews this week? Draft replies for the unanswered ones."
```

Writes stay off until you set `WRITE_ENABLED=true`.

## Features

| Tool                 | What it does                                                              | Writes?    |
| -------------------- | ------------------------------------------------------------------------- | ---------- |
| `list_accounts`      | Accounts you can access                                                   | no         |
| `list_locations`     | Locations for an account                                                  | no         |
| `get_location`       | Full profile fields for one location                                      | no         |
| `list_reviews`       | Reviews, filter by `since` (`7d` or ISO date) and `min_stars`             | no         |
| `draft_review_reply` | Reply **draft** from templates (3 tones) plus an optional LLM hook        | no         |
| `post_review_reply`  | Posts a reply. Needs `WRITE_ENABLED=true` and a valid approve token       | **gated**  |
| `create_post`        | Creates a local post (optional CTA). Same gate                            | **gated**  |
| `get_performance`    | Daily-metric totals (website clicks, calls, directions, impressions, ...) | no         |
| `audit_completeness` | 0-100 profile score with a prioritised fix list                           | no         |
| `approve_reply`      | Opt-in only (`GBP_ALLOW_MCP_APPROVE=true`). Disabled by default           | token mint |

It also ships an n8n workflow ([`n8n/weekly-review-digest.json`](n8n/weekly-review-digest.json)) that pulls unanswered reviews each Monday, drafts replies from templates and posts the digest to Slack for human approval, plus a validator ([`scripts/validate_n8n.py`](scripts/validate_n8n.py)).

## The approve-before-write gate

```
draft_review_reply  ->  human reads the text  ->  python -m gbp_mcp.approve reply ...  ->  approve_token
                                                                                              |
post_review_reply(review_id, text, approve_token)  <-----------------------------------------+
```

- Default `WRITE_ENABLED=false`. A blocked attempt is logged and the token is not burned.
- The token is bound to the exact action and text (SHA-256 of the payload). Change one character and it fails.
- Single-use, expires after 15 minutes (`GBP_APPROVAL_TTL_SECONDS`), only its hash is stored.
- The token is burned before the Google call, so a crash can never allow a replay.
- Daily write cap (`GBP_DAILY_WRITE_CAP`, default 10) and a JSONL audit log of every attempt (`ok`, `blocked`, `failed`).
- Approval is CLI-only by default, so the model cannot approve its own output. Enable `approve_reply` over MCP only if your client asks you before each tool call.

Real output of the CLI:

```
$ python -m gbp_mcp.approve reply --review-id accounts/1/locations/2/reviews/R2 --text "Hi Alex, we are sorry we arrived late. Please contact us so we can put it right."

REPLY to accounts/1/locations/2/reviews/R2:
---
Hi Alex, we are sorry we arrived late. Please contact us so we can put it right.
---
Type 'approve' to mint a single-use token: approve

approve_token: gbp_...
Valid for 900s, one use, this exact text only.
```

And with writes disabled (real output):

```
WriteBlocked Writes are disabled (WRITE_ENABLED=false). Set WRITE_ENABLED=true to allow.
```

## Sample completeness audit

Real output of `python -m gbp_mcp.cli`, run on the bundled **fictional** sample business ([`examples/sample-location.json`](examples/sample-location.json)). It is not a live Google call. HTML version: [`examples/sample-audit.html`](examples/sample-audit.html).

```
Profile completeness: Example Plumbing Co (sample)  -> 50/100

[~] Categories                5/10  primary=yes, secondary=0
[~] Description length        3/10  36 characters (target 250-750)
[~] Opening hours           7.1/10  5/7 days set
[ ] Special hours             0/5   0 special-hours entries
[x] Phone number              5/5   primary phone set
[x] Website                   5/5   https://example.com/
[~] Photos                    9/15  6 photos (target 25+)
[~] Services                  4/10  2 services (target 6+)
[~] Attributes                2/5   1 attributes set
[ ] Q&A                       0/5   0 questions
[~] Review response rate     10/20  2/4 reviews answered (50%)

Fix list (biggest wins first):
1. [high] +10 pts - Reply to unanswered reviews, newest first, aiming for 90%+ answered.
2. [medium] +7 pts - Write a 250-750 character description with your services, area and a call to action.
3. [medium] +6 pts - Upload photos: aim for 25+ covering exterior, interior, team and work examples.
4. [medium] +6 pts - List 6+ services with short descriptions.
5. [medium] +5 pts - Set a precise primary category and add 2+ secondary categories.
6. [medium] +5 pts - Add special hours for upcoming holidays so customers see accurate times.
7. [medium] +5 pts - Seed 3-5 Q&A entries with your most common customer questions.
8. [low] +3 pts - Fill in attributes (payment methods, accessibility, amenities) that apply.
9. [low] +2.9 pts - Add regular opening hours for all 7 days (mark closed days explicitly).
```

Items that cannot be fetched (for example Q&A, if Google no longer serves it for your account, or review response rate when there are no reviews) are skipped and the score is normalised over what remains.

## Configuration

| Variable                                                    | Default                      | Purpose                                                                 |
| ----------------------------------------------------------- | ---------------------------- | ----------------------------------------------------------------------- |
| `WRITE_ENABLED`                                             | `false`                      | Master switch for replies and posts                                     |
| `GBP_DAILY_WRITE_CAP`                                       | `10`                         | Max successful writes per UTC day                                       |
| `GBP_APPROVAL_TTL_SECONDS`                                  | `900`                        | Approve token lifetime                                                  |
| `GBP_ALLOW_MCP_APPROVE`                                     | `false`                      | Allow the `approve_reply` tool                                          |
| `GBP_ACCOUNT_ID`                                            | auto if you have one account | e.g. `accounts/123`                                                     |
| `GBP_CLIENT_ID` / `GBP_CLIENT_SECRET` / `GBP_REFRESH_TOKEN` | from `token.json`            | OAuth credentials                                                       |
| `GBP_MCP_HOME`                                              | `~/.gbp-mcp`                 | token, approvals, audit log                                             |
| `GBP_AUDIT_LOG`                                             | `$GBP_MCP_HOME/audit.jsonl`  | audit log path                                                          |
| `GBP_BUSINESS_NAME`                                         | empty                        | appended to drafted replies                                             |
| `GBP_LLM_HOOK`                                              | empty                        | `package.module:function(review, draft, tone) -> str` to rewrite drafts |

The OAuth token file is written with `chmod 600`. On Windows run:
`icacls "%USERPROFILE%\.gbp-mcp\token.json" /inheritance:r /grant:r "%USERNAME%:F"`.

## How it works

Official APIs only, with your own OAuth (scope `business.manage`): Account Management v1, Business Information v1, My Business v4 (reviews, media, local posts, Q&A) and Business Profile Performance v1. No scraping and no third-party data. Endpoints and versions were checked against Google's REST reference in October 2026; Google changes these, so verify against the [current docs](https://developers.google.com/my-business/reference/rest) if a call returns 404.

## Tests

```bash
pip install -e ".[dev]"
pytest -q            # 53 tests, Google APIs mocked with respx, no network
python scripts/validate_n8n.py
```

The CI workflow (Python 3.10-3.12, never calls Google) is in [`ci/github-actions-ci.yml`](ci/github-actions-ci.yml). Copy it to `.github/workflows/ci.yml` to enable it (the token used for the first push lacked GitHub's `workflow` scope).

## FAQ

**Can Claude post replies on its own?** No. With defaults nothing can be written. Even with `WRITE_ENABLED=true`, a write needs a token a human minted for that exact text.

**Do I need Google's approval?** Yes, for API access to Business Profile. Without it, live calls fail. The demo and tests work without it.

**Does it cost anything?** The server is free (MIT). Google's Business Profile APIs have no per-call fee that I am aware of, but check Google's terms and quotas.

**Where do my credentials go?** Only into a local `token.json` (mode 600) or environment variables. Nothing is sent anywhere except Google.

**Is the LLM required for drafts?** No. Drafts come from `gbp_mcp/templates/replies.yaml`. The LLM hook is optional.

**Why is Q&A sometimes skipped in the audit?** Google has been changing Q&A availability. If the endpoint fails, that item is skipped instead of penalising you.

## More from SkynetLabs

- [aeo-kit](https://github.com/waseemnasir2k26/aeo-kit) - make WordPress quotable by AI answer engines (llms.txt, JSON-LD)
- [aeo-radar](https://github.com/waseemnasir2k26/aeo-radar) - score how quotable your WordPress content is by AI answer engines
- [n8n-workflows](https://github.com/waseemnasir2k26/n8n-workflows) - production n8n workflows from real client builds
- Local-business demo sites: [dental](https://github.com/waseemnasir2k26/skynetlabs-dental-demo), [plumbing](https://github.com/waseemnasir2k26/skynetlabs-plumbing-demo), [restaurant](https://github.com/waseemnasir2k26/skynetlabs-restaurant-demo)

**GBP optimisation + review-reply automation done for you:** [Fiverr](https://www.fiverr.com/agencies/skynetjoellc) or [Upwork](https://www.upwork.com/freelancers/muhammadwaseemnasir).

## Work with SkynetLabs

Built by [Waseem Nasir](https://www.skynetjoe.com/?utm_source=github&utm_medium=readme&utm_campaign=google-business-profile-mcp) at SkynetLabs — n8n + AI automation for small businesses.

- Hire on Fiverr: https://www.fiverr.com/agencies/skynetjoellc
- Hire on Upwork: https://www.upwork.com/freelancers/muhammadwaseemnasir
- Book a free call: https://calendly.com/skynetlabs/schedule-a-free-consultation
