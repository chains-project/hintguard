"""Replay nah's own block cases (tests/data/nah-corpus) through hintguard.

Whatever nah blocks must come out as one of our hints: no unknown guard name, no crash, no
operator talk. Cases of the guards hintguard disables are skipped.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import shutil
from pathlib import Path

import pytest

from hintguard import _cli, _nah
from hintguard._hints import table

CASES = [json.loads(line) for line in (Path(__file__).parent / "data" / "nah-corpus" / "blocks.jsonl").open()]


@pytest.mark.skipif(not shutil.which("nah"), reason="nah not installed")
def test_every_nah_block_becomes_our_hint() -> None:
    cases = [c for c in CASES if c["guard"] not in _nah.DISABLED]
    with cf.ThreadPoolExecutor(6) as ex:
        verdicts = list(ex.map(lambda c: _cli.verdict(c["command"], "/tmp"), cases))
    errors = [(c["id"], e) for c, (_, e) in zip(cases, verdicts) if e]
    assert errors == []
    lines = {h.line() for h in table().values()}
    blocked = 0
    for hints, _ in verdicts:
        if hints:
            blocked += 1
            assert _cli.reason(hints).split(" Also breaks:")[0] in lines
    # nah v1.7.0 against a corpus from a later commit, outside its test fixtures: most still block.
    assert blocked / len(cases) > 0.8, f"{blocked}/{len(cases)}"
