"""The hard design rules on hints: every rule has one, it is crisp, imperative, and never talks to a human."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from hintguard import _nah, _rules
from hintguard._hints import MAX_LEN, table

# A hint must never hand the decision to a person.
HUMAN = re.compile(r"\b(user|operator|human|admin|owner|manual(ly)?|approv\w*|permission|allow-once|"
                   r"ask|confirm\w*|contact|request|review(er)?|someone|(have|let|tell) them)\b", re.IGNORECASE)
VERBS = {"Archive", "Bound", "Check", "Delete", "Deprecate", "Do", "Download", "Drop", "Keep", "Leave",
         "Let", "Merge", "Name", "Push", "Remove", "Restart", "Retype", "Rewrite", "Run", "Save", "Send",
         "Set", "Spell", "Stop", "Sync", "Target", "Use", "Work", "Write"}
DROP = "drop this step"
# Hint text must hold on any machine: no home-relative or absolute paths (examples may use them).
LOCAL = re.compile(r"~/|\$HOME|(?<![\w.])/(home|Users|opt|srv|tmp)/")


def corpus_guards() -> set[str]:
    path = Path(__file__).parent / "data" / "nah-corpus" / "blocks.jsonl"
    return {json.loads(line)["guard"] or "nah" for line in path.open()}


def test_every_own_rule_has_a_hint() -> None:
    assert set(_rules.RULES) <= set(table())


def test_every_nah_guard_in_the_corpus_has_a_hint() -> None:
    assert corpus_guards() - set(table()) == set()


@pytest.mark.skipif(not shutil.which("nah"), reason="nah not installed")
def test_every_live_nah_guard_has_a_hint() -> None:
    assert set(_nah.guards()) - set(table()) == set()


@pytest.mark.parametrize("h", table().values(), ids=lambda h: h.rule)
def test_hint_is_crisp_and_agent_only(h) -> None:  # type: ignore[no-untyped-def]
    line = h.line()
    assert len(line) <= MAX_LEN, f"{len(line)} chars"
    assert "\n" not in line
    assert not HUMAN.search(h.rewrite + " " + h.why), HUMAN.search(h.rewrite + " " + h.why)
    assert h.rewrite.split()[0] in VERBS, "rewrite must start with an imperative verb"
    assert h.rewrite.endswith(".") and h.why.endswith(".")
    assert len(h.why.split()) <= 12
    assert h.example or DROP in h.rewrite.lower(), "a hint proposes a concrete command, or says to drop the step"
    assert not LOCAL.search(h.rewrite + " " + h.why), "hint text names generic fixes, not local paths"


def test_linter_catches_operator_talk() -> None:
    """The instrument itself: known-bad hints taken from the guards we evaluated must fail."""
    bad = ["ask the operator to perform any intentional power action",
           "ask the user for explicit permission and have them run the command manually",
           "the user can approve it with dcg allow-once"]
    assert all(HUMAN.search(b) for b in bad)
    assert not HUMAN.search("Run git stash first, or use git reset --keep, or restore single files.")
    assert LOCAL.search("Use ~/bin/sched to run jobs.") and LOCAL.search("rm -rf /tmp/claude-1001/x")
    assert not LOCAL.search("rm -rf ./build-cache") and not LOCAL.search("git clone --mirror . ../rewrite.git")
