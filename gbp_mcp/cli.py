"""Offline demo: runs the real scoring code on bundled sample data (no Google calls)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .completeness import render_html, render_text, score_location

DEFAULT_SAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sample-location.json"


def run_demo(sample: Path = DEFAULT_SAMPLE) -> dict:
    data = json.loads(Path(sample).read_text(encoding="utf-8"))
    return score_location(data["location"], data["media_count"], data["attributes"],
                          data["questions"], data["reviews"])


def demo_main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run_demo()
    print(render_text(report))
    if "--html" in argv:
        out = Path(argv[argv.index("--html") + 1])
        out.write_text(render_html(report), encoding="utf-8")
        print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(demo_main())
