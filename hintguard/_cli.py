"""hintguard command line: a PreToolUse hook that denies risky shell commands with a rewrite hint."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from importlib.metadata import version

from . import _log, _nah, _rules
from ._hints import MAX_LEN, Hint, hint, table


def verdict(command: str, cwd: str) -> tuple[list[Hint], str | None]:
    """Hints for every rule the command breaks (own rules first), and a nah error if nah failed."""
    hints, error, _ = verdict_full(command, cwd)
    return hints, error


def verdict_full(command: str, cwd: str) -> tuple[list[Hint], str | None, bool]:
    """As verdict, plus whether nah's check was partial (out of its evidence budget)."""
    rules = _rules.check(command)
    error = None
    partial = False
    try:
        guards, partial = _nah.decide(command, cwd)
        rules += [g for g in guards if g not in rules]
    except _nah.NahError as e:
        error = str(e)
    return [hint(r) for r in rules], error, partial


def reason(hints: list[Hint]) -> str:
    """The first hint is the instruction; further rules are named so the rewrite can avoid them too."""
    text = hints[0].line()
    if len(hints) > 1:
        more = " Also: " + ", ".join(h.rule for h in hints[1:]) + "."
        if len(text) + len(more) <= MAX_LEN:
            text += more
    return text


def cmd_hook(_: argparse.Namespace) -> int:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    t = time.monotonic()
    hints, error, partial = verdict_full(command, payload.get("cwd") or os.getcwd())
    _log.record(payload.get("session_id"), payload.get("tool_use_id"), [h.rule for h in hints],
                error is not None, (time.monotonic() - t) * 1000, partial)
    if error:
        print(f"hintguard: {error}", file=sys.stderr)
    if hints:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason(hints)}}))
        return 0
    return 1 if error else 0


def cmd_check(a: argparse.Namespace) -> int:
    hints, error = verdict(a.command, os.path.abspath(a.cwd))
    if error:
        print(f"hintguard: {error}", file=sys.stderr)
    if a.json:
        print(json.dumps({"denied": bool(hints), "rules": [h.rule for h in hints],
                          "reason": reason(hints) if hints else None, "error": error}))
    elif hints:
        print(reason(hints))
    return 1 if error else 0


def cmd_hints(a: argparse.Namespace) -> int:
    rows = [{"rule": h.rule, "rewrite": h.rewrite, "why": h.why} for h in table().values()]
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        for h in table().values():
            print(h.line())
    return 0


def cmd_stats(a: argparse.Namespace) -> int:
    s = _log.stats(_log.parse_since(a.since))
    if a.json:
        print(json.dumps(s))
    elif s["calls"]:
        rules = ", ".join(f"{k} {v}" for k, v in s["rules"].items())  # type: ignore[attr-defined]
        after = ", ".join(f"{k} {v}" for k, v in s["after_deny"].items())  # type: ignore[attr-defined]
        print(f"{s['calls']} calls in {s['sessions']} sessions, {s['denied']} denied ({rules})")
        if after:
            print(f"after a deny: {after}")
    return 0


def cmd_setup(a: argparse.Namespace) -> int:
    """Enable every nah guard except DISABLED; print only what changed."""
    for name, on in _nah.guards().items():
        want = name not in _nah.DISABLED
        if on != want:
            if not a.dry_run:
                _nah.set_guard(name, want)
            print(f"{'enabled' if want else 'disabled'} {name}{' (dry run)' if a.dry_run else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hintguard", description=__doc__)
    p.add_argument("--version", action="version", version=f"hintguard {version('hintguard')}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("hook", help="PreToolUse hook: Claude Code JSON on stdin, deny JSON on stdout")
    s.set_defaults(fn=cmd_hook)
    s = sub.add_parser("check", help="dry-run one command; prints the deny line, nothing if allowed")
    s.add_argument("command")
    s.add_argument("--cwd", default=".")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("hints", help="list every rule with its hint")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_hints)
    s = sub.add_parser("stats", help="usage from the local decision log (no command text is logged)")
    s.add_argument("--since", default="7d", help="window, e.g. 12h, 7d, 2w (default 7d)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_stats)
    s = sub.add_parser("setup", help="enable the nah guards hintguard expects")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_setup)
    a = p.parse_args(argv)
    return int(a.fn(a))


if __name__ == "__main__":
    sys.exit(main())
