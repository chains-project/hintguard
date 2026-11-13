"""hintguard's own rules: known positives, known negatives, boundaries."""
from __future__ import annotations

import pytest

from hintguard._rules import check

DYNAMIC_RM = [
    "rm -rf $(dirname $T)",
    'rm -f "$pw"',
    "rm -rf ${DIR}/x",
    "rm -rf `pwd`/x",
    "cd /tmp && rm -rf $S/out",
    "sudo rm -rf $X",
    "timeout 5 rm -f $F",
    "bash -c 'rm -rf $X'",
    "find . -name x | xargs rm -f $EXTRA",
    "for f in a b; do rm -f \"$f\"; done",
]
LITERAL = [
    "rm -rf build",
    "rm -rf /tmp/claude-1001/x",
    "rm -f -- a.txt b.txt",
    "grep -rm $x file",
    "echo $x; rm a",
    "ls -la $HOME",
    "git rm --cached $f",
    "rmdir empty",
    "",
    "if [ -n \"$x\" ]; then",  # unparseable fragment
]


@pytest.mark.parametrize("cmd", DYNAMIC_RM)
def test_rm_with_runtime_target_is_flagged(cmd: str) -> None:
    assert check(cmd) == ["rm-unresolvable"]


@pytest.mark.parametrize("cmd", LITERAL)
def test_literal_or_unrelated_is_not_flagged(cmd: str) -> None:
    assert check(cmd) == []
