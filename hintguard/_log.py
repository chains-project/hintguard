"""Local usage log of hook decisions, and the statistics `hintguard stats` reports.

One JSON line per hooked Bash call in ~/.local/state/hintguard/decisions.jsonl (HINTGUARD_LOG
overrides). No command text is logged: commands can hold secrets. A line holds the time, session and
tool-call ids, the rules that fired, whether nah failed or only checked partially (out of its
evidence budget), and the latency.
"""
from __future__ import annotations

import collections
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def path() -> Path:
    return Path(os.environ.get("HINTGUARD_LOG") or Path.home() / ".local/state/hintguard/decisions.jsonl")


def record(session: str | None, tool_use_id: str | None, rules: list[str], error: bool, ms: float,
           partial: bool = False) -> None:
    """Append one decision; never let logging break the hook."""
    line = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "session": session,
            "tool_use_id": tool_use_id, "rules": rules, "error": error, "partial": partial, "ms": round(ms, 1)}
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        pass


def parse_since(text: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([hdw])", text)
    if not m:
        raise ValueError(f"--since wants e.g. 12h, 7d, 2w, not {text!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]


def stats(since: timedelta, now: datetime | None = None) -> dict[str, object]:
    """Counts over the window, and what the agent did right after each deny in the same session."""
    cutoff = (now or datetime.now(UTC)) - since
    rows: list[dict[str, Any]] = []
    if path().exists():
        for line in path().open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if datetime.fromisoformat(r["ts"]) >= cutoff:
                rows.append(r)
    by_session: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by_session[str(r.get("session"))].append(r)
    after: collections.Counter[str] = collections.Counter()
    for seq in by_session.values():
        for k, r in enumerate(seq):
            if not r["rules"]:
                continue
            nxt = seq[k + 1] if k + 1 < len(seq) else None
            if nxt is None:
                after["no_further_bash_call"] += 1
            elif not nxt["rules"]:
                after["next_call_passed"] += 1
            elif set(nxt["rules"]) & set(r["rules"]):
                after["denied_again_same_rule"] += 1
            else:
                after["denied_by_another_rule"] += 1
    denied = [r for r in rows if r["rules"]]
    ms = sorted(float(r["ms"]) for r in rows)
    return {
        "since": cutoff.isoformat(timespec="seconds"),
        "calls": len(rows),
        "sessions": len(by_session),
        "denied": len(denied),
        "nah_errors": sum(1 for r in rows if r.get("error")),
        "nah_partial": sum(1 for r in rows if r.get("partial")),
        "rules": dict(collections.Counter(r["rules"][0] for r in denied).most_common()),
        "after_deny": dict(after),
        "latency_ms_p50": ms[len(ms) // 2] if ms else None,
        "latency_ms_p95": ms[int(len(ms) * 0.95)] if ms else None,
    }
