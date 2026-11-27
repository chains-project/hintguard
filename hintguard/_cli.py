"""hintguard command line: a PreToolUse hook that denies risky shell commands with a rewrite hint,
and a PostToolUse hook that sends an agent back to a shell script it wrote that switches off set -e."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from . import _errexit, _log, _nah, _rules
from ._hints import MAX_LEN, Hint, hint, table

# Tools whose input `command` runs in a shell: Bash, and Monitor (a background shell watch).
SHELL_TOOLS = ("Bash", "Monitor")
# Tools that write a file named by `tool_input.file_path` (Claude Code), and Codex's patch tool, whose
# `tool_input.command` is the patch text.
WRITE_TOOLS = ("Write", "Edit", "MultiEdit")
PATCH_TOOL = "apply_patch"


def verdict(command: str, cwd: str) -> tuple[list[Hint], str | None]:
    """Hints for every rule the command breaks (own rules first), and a nah error if nah failed."""
    hints, error, _, _ = verdict_full(command, cwd)
    return hints, error


def verdict_full(command: str, cwd: str) -> tuple[list[Hint], str | None, str | None, str | None]:
    """As verdict, plus the kind of nah's failure and why its check was partial (see _nah), each None
    when it does not apply."""
    rules = _rules.check(command, cwd)
    error = error_kind = partial = None
    try:
        guards, partial = _nah.decide(command, cwd)
        rules += [g for g in guards if g not in rules]
    except _nah.NahError as e:
        error, error_kind = str(e), e.kind
    return [hint(r) for r in rules], error, error_kind, partial


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
    if payload.get("tool_name") not in SHELL_TOOLS:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    t = time.monotonic()
    hints, error, error_kind, partial = verdict_full(command, payload.get("cwd") or os.getcwd())
    _log.record(payload.get("session_id"), payload.get("tool_use_id"), [h.rule for h in hints],
                (time.monotonic() - t) * 1000, error_kind=error_kind, partial_cause=partial)
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


def cmd_lint_hook(_: argparse.Namespace) -> int:
    """PostToolUse: block (send the agent back) when the file just written is a shell script with
    an SC2310 finding; silent otherwise."""
    payload = json.load(sys.stdin)
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input") or {}
    if tool in WRITE_TOOLS:
        paths = [tool_input.get("file_path") or ""]
    elif tool == PATCH_TOOL:
        cwd = payload.get("cwd") or os.getcwd()
        paths = [os.path.join(cwd, p) for p in _errexit.patched_paths(str(tool_input.get("command") or ""))]
    else:
        return 0
    reasons = []
    for path in paths:
        if path and (found := _errexit.file_findings(path)):
            text = "; ".join(f.rstrip(".") for f in found[:5])
            reasons.append(f"{path} switches off set -e (shellcheck SC2310): {text}.")
    if reasons:
        print(json.dumps({"decision": "block", "reason": " ".join(reasons) + f" {_errexit.FIX}"}))
    return 0


def cmd_lint(a: argparse.Namespace) -> int:
    """Print `FILE: line N: message` for every SC2310 finding; exit 1 if any. With --staged, check
    what a `git commit` in --cwd would record, as the commit-shell-errexit rule does."""
    if a.staged:
        if a.files:
            print("hintguard: lint --staged takes no FILE", file=sys.stderr)
            return 2
        lines = _rules.errexit_problems(os.path.abspath(a.cwd), a.all)
    elif not a.files:
        print("hintguard: lint needs FILE... or --staged", file=sys.stderr)
        return 2
    else:
        lines = [f"{path}: {f}" for path in a.files for f in _errexit.file_findings(path)]
    for line in lines:
        print(line)
    return 1 if lines else 0


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
        if s["nah_errors"] or s["nah_partial"]:
            kinds = ", ".join(f"{k} {v}" for k, v in s["nah_errors_by_kind"].items())  # type: ignore[attr-defined]
            causes = ", ".join(f"{k} {v}" for k, v in s["nah_partial_by_cause"].items())  # type: ignore[attr-defined]
            print(f"nah failed {s['nah_errors']} ({kinds or 'none'}), checked partially {s['nah_partial']} ({causes or 'none'})")
    return 0


NAH_GUARDS = Path.home() / ".nah" / "guards"


def install_guards(dest: Path, dry_run: bool) -> list[str]:
    """Copy each custom guard shipped in hintguard/guards/ into nah's user guard directory.
    Returns the names whose bytes changed (new or updated): nah needs them enabled again."""
    changed = []
    for bundle in sorted(files("hintguard").joinpath("guards").iterdir(), key=lambda b: b.name):
        if not bundle.is_dir():
            continue
        target = dest / bundle.name
        sources = {f.name: f.read_bytes() for f in bundle.iterdir() if f.is_file()}
        if all((target / n).is_file() and (target / n).read_bytes() == b for n, b in sources.items()):
            continue
        changed.append(bundle.name)
        if dry_run:
            continue
        target.mkdir(parents=True, exist_ok=True)
        for n, b in sources.items():
            (target / n).write_bytes(b)
            (target / n).chmod(0o700 if n == "run" else 0o600)
    return changed


def cmd_setup(a: argparse.Namespace) -> int:
    """Enable every nah guard except DISABLED, install and enable hintguard's own guards;
    print only what changed."""
    suffix = " (dry run)" if a.dry_run else ""
    for name, on in _nah.guards().items():
        want = name not in _nah.DISABLED
        if on != want:
            if not a.dry_run:
                _nah.set_guard(name, want)
            print(f"{'enabled' if want else 'disabled'} {name}{suffix}")
    for name in install_guards(NAH_GUARDS, a.dry_run):
        print(f"installed {name}{suffix}")
    status = _nah.custom_guards()
    for bundle in files("hintguard").joinpath("guards").iterdir():
        if bundle.is_dir() and status.get(bundle.name) != "active":
            if not a.dry_run:
                _nah.set_guard(bundle.name, True)
            print(f"enabled {bundle.name}{suffix}")
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
    s = sub.add_parser("lint-hook", help="PostToolUse hook: block a written shell script that switches off set -e")
    s.set_defaults(fn=cmd_lint_hook)
    s = sub.add_parser("lint", help="shell scripts that switch off set -e (SC2310); exit 1 if any")
    s.add_argument("files", nargs="*")
    s.add_argument("--staged", action="store_true", help="the scripts a git commit in --cwd would record")
    s.add_argument("--all", action="store_true", help="with --staged: as git commit -a")
    s.add_argument("--cwd", default=".")
    s.set_defaults(fn=cmd_lint)
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
