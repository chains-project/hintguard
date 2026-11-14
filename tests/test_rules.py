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
    "rm -rf /tmp/build/x",
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


SELF_MATCHING_WAITS = [
    'until ! pgrep -f "deploy.sh prod" >/dev/null; do sleep 60; done; tail -5 deploy.log',
    "while pgrep -f 'extract.py --cases --blocks 26095334' >/dev/null; do sleep 30; done",
    "while pgrep -af race-probe.py; do sleep 30; done",
    "ssh host 'while pgrep -f \"bench run\"; do sleep 5; done'",
    'until [ -s out.csv ] || ! pgrep -f "sellers-window" >/dev/null; do sleep 60; done',
    "until grep -q VERDICT h.out; do pgrep -f 'trigger-hints' >/dev/null || { echo gone; break; }; sleep 30; done",
    # a wake-up condition, run each minute in `sh -c`; here run remotely, again in `sh -c`
    "wake add --when \"cmd:test -s v.txt && ! pgrep -f 'by_venue.py'\" --then self",
    "wake add --when \"cmd:python3 kernel.py 'grep -q DONE step4.log || ! pgrep -f step4_smoke.sh >/dev/null'\"",
]
NOT_SELF_MATCHING = [
    "while pgrep -f 'replay-[1]hq -replay' >/dev/null; do sleep 30; done",
    "while pgrep -f 'replay-[1]hq|replay-[m]argin' >/dev/null; do sleep 30; done",
    "while pgrep postgres >/dev/null; do sleep 30; done",  # no -f: the process name only
    "pgrep -f 'deploy.sh'",  # a one-shot check, not a wait
    "while kill -0 $pid; do sleep 30; done",
    "wake add --when \"cmd:test -s v.txt && ! pgrep -f '[b]y_venue.py'\" --then self",
    "notify --message \"Agents wait with 'while pgrep -f <cmd>', which never ends\"",  # quotes a loop
    'S=/tmp/s; until ! pgrep -f "$S/compete -since 0" >/dev/null; do sleep 60; done',  # $S unexpanded
    # typed into a remote terminal (a pty, not `sh -c`): not where the loop's shell runs
    'until timeout 60 python3 tmux.py "pgrep -f build.sh >/dev/null || echo DONE" | grep -q DONE; do sleep 60; done',
]


@pytest.mark.parametrize("cmd", SELF_MATCHING_WAITS)
def test_self_matching_pgrep_wait_is_flagged(cmd: str) -> None:
    assert check(cmd) == ["pgrep-self-match"]


@pytest.mark.parametrize("cmd", NOT_SELF_MATCHING)
def test_pgrep_that_cannot_match_itself_passes(cmd: str) -> None:
    assert check(cmd) == []


def test_reference_a_real_shell_finds_itself() -> None:
    """The instrument's ground truth: `sh -c` running pgrep -f finds itself, unless the pattern is bracketed."""
    import subprocess

    def hit(pat: str) -> bool:
        return subprocess.run(["sh", "-c", f"pgrep -f '{pat}' >/dev/null"], check=False).returncode == 0
    assert hit("zz-selfmatch-probe") and not hit("[z]z-selfmatch-probe")
