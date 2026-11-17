"""submodule-drift: real repositories with a submodule; known positives, known negatives, boundaries."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hintguard._rules import check, submodule_problems

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "protocol.file.allow=always", "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, **ENV}).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository `top` with a committed submodule `lib` (a clone of `upstream`, two commits)."""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q")
    (up / "a").write_text("1")
    git(up, "add", "a")
    git(up, "commit", "-qm", "one")
    top = tmp_path / "top"
    top.mkdir()
    git(top, "init", "-q")
    git(top, "submodule", "add", "-q", str(up), "lib")
    (top / "x").write_text("x")
    git(top, "add", ".")
    git(top, "commit", "-qm", "init")
    return top


def advance(top: Path) -> None:
    """Move the submodule checkout one commit ahead of the recorded pointer: status `+lib`."""
    (top / "lib" / "a").write_text("2")
    git(top / "lib", "commit", "-qam", "two")


def test_clean_repo_passes(repo: Path) -> None:
    assert check("git commit -am 'msg'", str(repo)) == []
    assert submodule_problems(str(repo), commit_all=True) == []


def test_moved_checkout_with_commit_all_is_flagged(repo: Path) -> None:
    advance(repo)
    assert git(repo, "submodule", "status").startswith("+")  # the instrument sees the drift
    for cmd in ("git commit -am 'msg'", "git commit -a -m msg", "git add -A && git commit -m msg",
                "git add . && git commit -m msg", "cd lib && cd .. && git commit --all -m msg",
                f"git -C {repo} commit -qam msg"):
        assert check(cmd, str(repo)) == ["submodule-drift"], cmd
    assert check("git commit -am msg", str(repo / "lib" / "..")) == ["submodule-drift"]


def test_moved_checkout_committed_by_path_passes(repo: Path) -> None:
    advance(repo)
    assert check("git add x && git commit -m 'only x'", str(repo)) == []
    assert check("git commit -m 'msg'", str(repo)) == []
    assert check("git commit -m 'a message with -a in it'", str(repo)) == []


def test_intended_pointer_bump_passes(repo: Path) -> None:
    advance(repo)
    git(repo, "add", "lib")  # stage the bump on purpose: status is ` lib` again
    assert check("git commit -am 'bump lib'", str(repo)) == []


def test_lost_submodule_directory_is_flagged(repo: Path) -> None:
    subprocess.run(["rm", "-rf", str(repo / "lib")], check=True)
    assert check("git commit -am msg", str(repo)) == ["submodule-drift"]
    assert check("git commit -m msg", str(repo)) == []  # nothing staged: the commit records no deletion
    git(repo, "add", "-A")  # what an agent's `git add -A` does to a lost checkout
    assert check("git commit -m msg", str(repo)) == ["submodule-drift"]


def test_proper_submodule_removal_passes(repo: Path) -> None:
    git(repo, "rm", "-q", "lib")  # also drops it from .gitmodules
    assert check("git commit -m 'drop lib'", str(repo)) == []


def test_not_a_commit_or_not_a_repo_passes(repo: Path, tmp_path: Path) -> None:
    advance(repo)
    assert check("git status && git add -A", str(repo)) == []
    assert check("echo 'git commit -am x'", str(repo)) == []
    assert check("git commit -am msg", str(tmp_path)) == []  # not a repository
    assert check("git commit -am msg", str(tmp_path / "missing")) == []
    assert check("git commit -am msg") == []  # no cwd: static rules only
