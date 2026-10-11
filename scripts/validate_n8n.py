#!/usr/bin/env python
"""Validate n8n workflow JSON files: structure, connections, triggers, no secrets."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECRET_PATTERNS = [r"ya29\.", r"1//[0-9A-Za-z_-]{20,}", r"AIza[0-9A-Za-z_-]{30,}",
                   r"xox[bp]-", r"-----BEGIN [A-Z ]*PRIVATE KEY"]


def validate(wf: dict) -> list[str]:
    errors: list[str] = []
    nodes = wf.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ["workflow has no nodes"]
    names = [n.get("name") for n in nodes]
    if len(set(names)) != len(names):
        errors.append("duplicate node names")
    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids):
        errors.append("duplicate node ids")
    for n in nodes:
        for key in ("name", "type", "typeVersion", "position", "parameters"):
            if key not in n:
                errors.append(f"node {n.get('name')!r} missing {key}")
    if not any("trigger" in str(n.get("type", "")).lower() for n in nodes):
        errors.append("no trigger node")
    name_set = set(names)
    for src, outs in (wf.get("connections") or {}).items():
        if src not in name_set:
            errors.append(f"connection from unknown node {src!r}")
        for branch in outs.get("main", []):
            for link in branch:
                if link.get("node") not in name_set:
                    errors.append(f"connection to unknown node {link.get('node')!r}")
    connected = set((wf.get("connections") or {}).keys())
    for outs in (wf.get("connections") or {}).values():
        for branch in outs.get("main", []):
            connected.update(link.get("node") for link in branch)
    for n in nodes:
        if n.get("name") not in connected and len(nodes) > 1:
            errors.append(f"orphan node {n.get('name')!r}")
    blob = json.dumps(wf)
    for pat in SECRET_PATTERNS:
        if re.search(pat, blob):
            errors.append(f"possible secret matching {pat}")
    if '"credentials"' in blob:
        errors.append("workflow embeds a credentials block; use n8n credential references instead")
    return errors


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or sorted(Path(__file__).resolve().parent.parent.glob("n8n/*.json"))
    bad = 0
    for p in paths:
        try:
            errs = validate(json.loads(p.read_text(encoding="utf-8")))
        except ValueError as exc:
            errs = [f"invalid JSON: {exc}"]
        print(f"{'FAIL' if errs else 'ok  '} {p}")
        for e in errs:
            print(f"   - {e}")
        bad += bool(errs)
    return 1 if bad or not paths else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
