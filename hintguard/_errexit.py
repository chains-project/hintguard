"""Shell scripts that silently switch off `set -e`: shellcheck's optional check SC2310.

Under `set -e`, a function called as the condition of `if`/`while`/`until`, after `!`, or inside an
`&&`/`||` list runs its whole body with errexit suspended (POSIX `set -e`): a failing command in it
is ignored and the script goes on. SC2310 (`check-set-e-suppressed`, off by default) flags such calls;
it fires only in scripts that enable `set -e`. It also flags a call in the last position of a list,
where errexit is in fact active: a harmless false positive, the rewrite costs nothing.

Used by the `lint-hook` (PostToolUse on Write/Edit), the `commit-shell-errexit` rule (a `git commit`
of such a script) and `hintguard lint`. Without shellcheck everything here is silent.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

CODE = 2310
SUFFIXES = {".sh", ".bash"}
# shellcheck's dialects; zsh is not one of them.
_SHEBANG = re.compile(r"#!\s*(/usr)?/bin/(env\s+)?(sh|bash|dash|ksh)\b")
FIX = ("Call the function on its own line; to handle its failure, use an ERR trap with set -E, or test "
       "a status variable set inside it.")


def is_shell_script(name: str, text: str) -> bool:
    return Path(name).suffix in SUFFIXES or bool(_SHEBANG.match(text))


def findings(text: str) -> list[str]:
    """`line N: message` for every SC2310 finding in a shell script's text; [] without shellcheck."""
    exe = shutil.which("shellcheck")
    if exe is None:
        return []
    cmd = [exe, "-f", "json", "-o", "check-set-e-suppressed", "-i", f"SC{CODE}"]
    if not text.startswith("#!"):
        cmd += ["-s", "bash"]
    try:
        r = subprocess.run([*cmd, "-"], input=text, capture_output=True, text=True, timeout=4, check=False)
        items = json.loads(r.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    return [f"line {i['line']}: {i['message']}" for i in items if i.get("code") == CODE]


def file_findings(path: str | Path) -> list[str]:
    """findings() for a file on disk, when it is a shell script; [] otherwise or if unreadable."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return []
    return findings(text) if is_shell_script(str(path), text) else []
