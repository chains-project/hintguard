"""Ask nah (https://github.com/manuelschipper/nah) for its verdict through `nah decide` (nah/decide/v1)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

# All 48 built-in nah guards except five that mostly flag routine coding-agent work, measured on
# 10,000 real Claude Code Bash calls: pushes to main, cleanup of temporary directories,
# `git pull --rebase`, branch deletion, and service restarts.
DISABLED = ("git-protected-push", "fs-outside-workspace-delete", "git-history-rewrite",
            "git-ref-delete", "sys-service-stop")


class NahError(RuntimeError):
    """nah gave no decision. kind: "unrunnable" (missing, or cannot be started), "timeout", or "exit"
    (an exit status other than block or delegate)."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def binary() -> str:
    # Hooks may run with a PATH that lacks ~/.local/bin, a common place for nah's release binary.
    local = os.path.expanduser("~/.local/bin/nah")
    path = os.environ.get("HINTGUARD_NAH") or shutil.which("nah") or (local if os.access(local, os.X_OK) else None)
    if not path:
        raise NahError("nah not found on PATH (set HINTGUARD_NAH)", "unrunnable")
    return path


# nah stops on a wall-clock deadline when the host is loaded and then delegates even plain
# `git reset --hard` (13% of calls on a 4-core host at load 4.6, 2026-11-14). A rerun usually finishes
# in time. Other partial results ("analysis is incomplete" on complex commands) are deterministic: a
# rerun gives the same, so only the deadline case is retried.
DEADLINE = "deadline-exceeded"
DEADLINE_TRIES = 3


def decide(command: str, cwd: str, timeout: float = 5.0) -> tuple[list[str], str | None]:
    """Names of the nah guards that block the command ([] when nah delegates), and why nah's analysis
    was partial when it delegated without a full check: "deadline" (its evaluation deadline ran out)
    or "incomplete" (it cannot fully analyze the command, e.g. a script it does not model); None for
    a full check. A delegation on the deadline is retried, up to DEADLINE_TRIES runs in all."""
    for _ in range(DEADLINE_TRIES):
        guards, partial = _decide_once(command, cwd, timeout)
        if guards or partial != "deadline":
            break
    return guards, partial


def _decide_once(command: str, cwd: str, timeout: float) -> tuple[list[str], str | None]:
    """One `nah decide`: (blocking guards, why the check was partial or None)."""
    request = json.dumps({"v": 1, "tool": "Bash", "input": {"command": command}, "cwd": cwd})
    try:
        p = subprocess.run([binary(), "decide"], input=request, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise NahError(f"nah decide timed out after {timeout}s", "timeout") from e
    except OSError as e:
        raise NahError(f"cannot run nah: {e}", "unrunnable") from e
    if p.returncode == 2:
        try:
            partial = json.loads(p.stdout).get("reason") == "partial coverage"
        except json.JSONDecodeError:
            partial = False
        if not partial:
            return [], None
        return [], "deadline" if DEADLINE in p.stderr else "incomplete"
    if p.returncode != 1:
        raise NahError(f"nah decide exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}", "exit")
    body = json.loads(p.stdout)
    names = [a["name"] for a in body.get("policy_attributions", []) if a.get("name")]
    return names or ["nah"], None


def guards() -> dict[str, bool]:
    """Every built-in guard and whether it is enabled, from `nah guards`."""
    p = subprocess.run([binary(), "guards"], capture_output=True, text=True, check=True)
    out: dict[str, bool] = {}
    for line in p.stdout.splitlines():
        if line.startswith("- ["):
            out[line[6:].strip()] = line[3] == "x"
        elif line.startswith("Custom"):
            break
    return out


def set_guard(name: str, enabled: bool) -> None:
    subprocess.run([binary(), "guard", "enable" if enabled else "disable", name],
                   capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
