"""Ask nah (https://github.com/manuelschipper/nah) for its verdict through `nah decide` (nah/decide/v1)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

# All 48 built-in nah guards except those that flag routine work for this operator
# (measured on 10k past Bash calls, see bash-safety-analysis-study): pushes to main, scratchpad
# cleanup, `git pull --rebase`, branch deletion, service restarts on own hosts.
DISABLED = ("git-protected-push", "fs-outside-workspace-delete", "git-history-rewrite",
            "git-ref-delete", "sys-service-stop")


class NahError(RuntimeError):
    pass


def binary() -> str:
    # Hooks may run with a PATH that lacks ~/.local/bin, where the fleet installs nah.
    local = os.path.expanduser("~/.local/bin/nah")
    path = os.environ.get("HINTGUARD_NAH") or shutil.which("nah") or (local if os.access(local, os.X_OK) else None)
    if not path:
        raise NahError("nah not found on PATH (set HINTGUARD_NAH)")
    return path


def decide(command: str, cwd: str, timeout: float = 10.0) -> list[str]:
    """Names of the nah guards that block the command; [] when nah delegates."""
    request = json.dumps({"v": 1, "tool": "Bash", "input": {"command": command}, "cwd": cwd})
    try:
        p = subprocess.run([binary(), "decide"], input=request, capture_output=True, text=True,
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise NahError(f"nah decide timed out after {timeout}s") from e
    except OSError as e:
        raise NahError(f"cannot run nah: {e}") from e
    if p.returncode == 2:
        return []
    if p.returncode != 1:
        raise NahError(f"nah decide exit {p.returncode}: {(p.stderr or p.stdout).strip()[:200]}")
    body = json.loads(p.stdout)
    names = [a["name"] for a in body.get("policy_attributions", []) if a.get("name")]
    return names or ["nah"]


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
