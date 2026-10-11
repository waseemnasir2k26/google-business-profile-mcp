# Changelog

## 1.0.0 - 2026-10-12

- Initial release: 9 tools plus an opt-in `approve_reply` (accounts, locations, reviews, draft reply, gated reply, gated post, performance, completeness audit).
- Single-use, payload-bound approve tokens; `WRITE_ENABLED=false` default; daily write cap; JSONL audit log.
- Profile completeness audit (0-100) with prioritised fix list and HTML report renderer.
- n8n weekly review digest workflow with validator script.
- Offline test suite with mocked Google APIs (respx); no live Google calls in CI.
