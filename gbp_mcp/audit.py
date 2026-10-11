"""Append-only JSONL audit log of every write attempt (ok, blocked, failed)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog:
    def __init__(self, path: Path, now: Callable[[], datetime] = _utcnow):
        self.path = Path(path)
        self.now = now

    def append(self, action: str, status: str, **fields) -> dict:
        entry = {"ts": self.now().isoformat(), "action": action, "status": status, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def ok_writes_today(self) -> int:
        today = self.now().date().isoformat()
        return sum(1 for e in self.read()
                   if e.get("status") == "ok" and str(e.get("ts", "")).startswith(today))
