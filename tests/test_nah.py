"""nah's deadline: a delegation caused by it is retried; a deterministic partial result is not."""
from __future__ import annotations

from pathlib import Path

import pytest

from hintguard import _nah

BLOCK = '{"policy_attributions": [{"name": "git-hard-reset"}]}'
DELEGATE = '{"reason": "partial coverage"}'


def fake_nah(tmp_path: Path, stalls: int, stderr: str) -> Path:
    """A nah that delegates `stalls` times with `stderr`, then blocks; counts its runs in `runs`."""
    runs = tmp_path / "runs"
    script = tmp_path / "nah"
    script.write_text(f"""#!/bin/sh
cat >/dev/null
n=$(cat {runs} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {runs}
if [ $n -le {stalls} ]; then echo '{DELEGATE}'; echo '{stderr}' >&2; exit 2; fi
echo '{BLOCK}'; exit 1
""")
    script.chmod(0o755)
    return script


def runs(tmp_path: Path) -> int:
    return int((tmp_path / "runs").read_text())


DEADLINE = "nah: effinterp evaluation stopped: deadline-exceeded"
INCOMPLETE = "nah: effinterp analysis is incomplete; only established effects were evaluated"


@pytest.mark.parametrize("stalls", [1, 2])
def test_deadline_delegation_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stalls: int) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, stalls, DEADLINE)))
    assert _nah.decide("git reset --hard", "/tmp") == (["git-hard-reset"], False)
    assert runs(tmp_path) == stalls + 1


def test_retries_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, 99, DEADLINE)))
    assert _nah.decide("git reset --hard", "/tmp") == ([], True)
    assert runs(tmp_path) == _nah.DEADLINE_TRIES


def test_deterministic_partial_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, 1, INCOMPLETE)))
    assert _nah.decide("python3 x.py | tee y", "/tmp") == ([], True)
    assert runs(tmp_path) == 1


def test_a_block_is_one_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, 0, DEADLINE)))
    assert _nah.decide("git reset --hard", "/tmp") == (["git-hard-reset"], False)
    assert runs(tmp_path) == 1
