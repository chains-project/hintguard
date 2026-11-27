"""Shell scripts that switch off set -e (SC2310): the lint, the PostToolUse hook, the commit rule.

Known positives and negatives were checked against bash itself: in each positive, `f() { false; echo
after; }` prints `after` (errexit suspended); `g && f` stops at `false` yet shellcheck flags it (a
documented false positive, kept as shellcheck does).
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hintguard import _cli, _errexit
from hintguard._rules import check, errexit_problems

pytestmark = pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")

HEAD = "#!/bin/bash\nset -e\nf() { false; echo after; }\ng() { :; }\n"
POSITIVE = ["if f; then :; fi", "while f; do :; done", "! f", "f && g", "f || g", "f || exit 1",
            "g && f"]  # the last one: shellcheck's false positive
NEGATIVE = ["f", "f; g", "if [ -n x ]; then f; fi", "true || false", "x=$(date)"]
# The ai-examiner bug that motivated the rule (run-thesis.sh, 212a432), reduced.
REAL = """#!/usr/bin/env bash
set -euo pipefail
fetch_package() {
  git clone -q "$1" pkg
  echo fetched
}
if ! fetch_package "$url"; then
  echo failed >&2
fi
"""


@pytest.mark.parametrize("body", POSITIVE)
def test_positive(body: str) -> None:
    assert _errexit.findings(HEAD + body + "\n")


@pytest.mark.parametrize("body", NEGATIVE)
def test_negative(body: str) -> None:
    assert _errexit.findings(HEAD + body + "\n") == []


def test_real_case() -> None:
    # `if !` is reported twice, once per construct.
    assert {f.split(":")[0] for f in _errexit.findings(REAL)} == {"line 7"}


def test_without_set_e_nothing() -> None:
    assert _errexit.findings("#!/bin/bash\nf() { false; }\nif f; then :; fi\n") == []


def test_no_shebang_is_read_as_bash() -> None:
    assert _errexit.findings("set -e\nf() { false; }\nif f; then :; fi\n")


@pytest.mark.parametrize(("name", "text", "shell"), [
    ("a.sh", "echo", True), ("a.bash", "echo", True), ("run", "#!/bin/bash\n", True),
    ("run", "#!/usr/bin/env bash\n", True), ("run", "#!/bin/sh\n", True), ("a.py", "print()", False),
    ("run", "#!/usr/bin/env python3\n", False), ("run", "#!/bin/zsh\n", False), ("README.md", "x", False)])
def test_is_shell_script(name: str, text: str, shell: bool) -> None:
    assert _errexit.is_shell_script(name, text) is shell


def test_file_findings_skip_non_shell(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("set -e\nf() { false; }\n! f\n")
    assert _errexit.file_findings(tmp_path / "a.py") == []
    assert _errexit.file_findings(tmp_path / "missing.sh") == []


def test_without_shellcheck_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_errexit.shutil, "which", lambda _: None)
    assert _errexit.findings(REAL) == []


def run_hook(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict[str, object]) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert _cli.main(["lint-hook"]) == 0
    return capsys.readouterr().out


def test_hook_blocks_a_written_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "run.sh").write_text(REAL)
    out = json.loads(run_hook(monkeypatch, capsys, {"tool_name": "Edit",
                                                     "tool_input": {"file_path": str(tmp_path / "run.sh")}}))
    assert out["decision"] == "block" and "line 7" in out["reason"] and "own line" in out["reason"]


@pytest.mark.parametrize("payload", [
    {"tool_name": "Write", "tool_input": {"file_path": "/nonexistent/x.sh"}},
    {"tool_name": "Bash", "tool_input": {"command": "true"}},
    {"tool_name": "Write", "tool_input": {}}])
def test_hook_silent(payload: dict[str, object], monkeypatch: pytest.MonkeyPatch,
                     capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hook(monkeypatch, capsys, payload) == ""


def test_hook_silent_on_clean_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "ok.sh").write_text(HEAD + "f\n")
    assert run_hook(monkeypatch, capsys, {"tool_name": "Write",
                                          "tool_input": {"file_path": str(tmp_path / "ok.sh")}}) == ""


def test_lint_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "bad.sh").write_text(REAL)
    (tmp_path / "ok.sh").write_text(HEAD + "f\n")
    assert _cli.main(["lint", str(tmp_path / "ok.sh")]) == 0 and capsys.readouterr().out == ""
    assert _cli.main(["lint", str(tmp_path / "ok.sh"), str(tmp_path / "bad.sh")]) == 1
    assert capsys.readouterr().out.startswith(f"{tmp_path / 'bad.sh'}: line 7:")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "ok.sh").write_text(HEAD + "f\n")
    git(tmp_path, "add", "ok.sh")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_commit_denied_with_staged_bad_script(repo: Path) -> None:
    (repo / "run.sh").write_text(REAL)
    git(repo, "add", "run.sh")
    assert errexit_problems(str(repo), False) == [f"run.sh: {f}" for f in _errexit.findings(REAL)]
    assert "commit-shell-errexit" in check("git commit -m x", str(repo))
    assert "commit-shell-errexit" in check(f"git -C {repo} commit -m x", "/")
    assert "commit-shell-errexit" in check(f"cd {repo} && git commit -m x", "/")


def test_commit_checks_the_staged_content_not_the_working_tree(repo: Path) -> None:
    (repo / "run.sh").write_text(REAL)
    git(repo, "add", "run.sh")
    (repo / "run.sh").write_text(HEAD + "f\n")  # fixed but not staged: the commit still records REAL
    assert "commit-shell-errexit" in check("git commit -m x", str(repo))
    git(repo, "add", "run.sh")
    assert "commit-shell-errexit" not in check("git commit -m x", str(repo))


def test_commit_allowed_when_bad_change_is_unstaged(repo: Path) -> None:
    (repo / "ok.sh").write_text(REAL)
    assert "commit-shell-errexit" not in check("git commit -m x", str(repo))
    assert "commit-shell-errexit" in check("git commit -am x", str(repo))


def test_commit_ignores_non_shell_files(repo: Path) -> None:
    (repo / "notes.txt").write_text(REAL.split("\n", 1)[1])  # same text, no shebang
    git(repo, "add", "notes.txt")
    assert "commit-shell-errexit" not in check("git commit -m x", str(repo))


def test_not_a_commit(repo: Path) -> None:
    (repo / "run.sh").write_text(REAL)
    git(repo, "add", "run.sh")
    assert "commit-shell-errexit" not in check("git status", str(repo))
