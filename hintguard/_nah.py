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
    pass


def binary() -> str:
    # Hooks may run with a PATH that lacks ~/.local/bin, a common place for nah's release binary.
    local = os.path.expanduser("~/.local/bin/nah")
    path = os.environ.get("HINTGUARD_NAH") or shutil.which("nah") or (local if os.access(local, os.X_OK) else None)
    if not path:
        raise NahError("nah not found on PATH (set HINTGUARD_NAH)")
    return path


# nah stops on a wall-clock deadline when the host is loaded and then delegates even plain
# `git reset --hard` (13% of calls on a 4-core host at load 4.6, 2026-11-14). A rerun usually finishes
# in time. Other partial results ("analysis is incomplete" on complex commands) are deterministic: a
# rerun gives the same, so only the deadline case is retried.
DEADLINE = "deadline-exceeded"
DEADLINE_TRIES = 3


def decide(command: str, cwd: str, timeout: float = 5.0) -> tuple[list[str], bool]:
    """Names of the nah guards that block the command ([] when nah delegates), and whether nah's
    analysis was partial (it delegated without a full check). A delegation caused by nah's own
    evaluation deadline is retried, up to DEADLINE_TRIES runs in all."""
    for _ in range(DEADLINE_TRIES):
        guards, partial, deadline = _decide_once(command, cwd, timeout)
        if guards or not deadline:
            break
    return guards, partial


def _decide_once(command: str, cwd: str, timeout: float) -> tuple[list[str], bool, bool]:
    """One `nah decide`: (blocking guards, partial, delegated on the evaluation deadline)."""
    request = json.dumps({"v": 1, "tool": "Bash", "input": {"command": command}, "cwd": cwd})
    try:
        p = subprocess.run([binary(), "decide"], input=request, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise NahError(f"nah decide timed out after {timeout}s") from e
    except OSError as e:
        raise NahError(f"cannot run nah: {e}") from e
    if p.returncode == 2:
        try:
            partial = json.loads(p.stdout).get("reason") == "partial coverage"
        except json.JSONDecodeError:
            partial = False
        return [], partial, partial and DEADLINE in p.stderr
    if p.returncode != 1:
        raise NahError(f"nah decide exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
    body = json.loads(p.stdout)
    names = [a["name"] for a in body.get("policy_attributions", []) if a.get("name")]
    return names or ["nah"], False, False


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
