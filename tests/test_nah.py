"""nah's deadline: a delegation caused by it is retried; a deterministic partial result is not. Each
partial result and failure carries its cause."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from hintguard import _nah

BLOCK = '{"policy_attributions": [{"name": "git-hard-reset"}]}'
DELEGATE = '{"reason": "partial coverage"}'


def fake_nah(tmp_path: Path, stalls: int, stderr: str, delay: float = 0) -> Path:
    """A nah that takes `delay` s, delegates `stalls` times with `stderr`, then blocks; counts its runs
    in `runs`."""
    runs = tmp_path / "runs"
    script = tmp_path / "nah"
    script.write_text(f"""#!/bin/sh
cat >/dev/null
n=$(cat {runs} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {runs}
sleep {delay}
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
    assert _nah.decide("git reset --hard", "/tmp") == (["git-hard-reset"], None)
    assert runs(tmp_path) == stalls + 1


def test_retries_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, 99, DEADLINE)))
    assert _nah.decide("git reset --hard", "/tmp") == ([], "deadline")
    assert runs(tmp_path) == _nah.DEADLINE_TRIES


def test_retries_share_one_time_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three slow deadline runs would take 1.8 s; together they get the 1 s of one call, and a retry
    cut short leaves the partial answer nah already gave, not a failure."""
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, 99, DEADLINE, delay=0.6)))
    t = time.monotonic()
    assert _nah.decide("ls *.py", "/tmp", timeout=1.0) == ([], "deadline")
    assert time.monotonic() - t < 1.3
    assert runs(tmp_path) == 2


def test_deterministic_partial_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, 1, INCOMPLETE)))
    assert _nah.decide("python3 x.py | tee y", "/tmp") == ([], "incomplete")
    assert runs(tmp_path) == 1


def test_a_block_is_one_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(fake_nah(tmp_path, 0, DEADLINE)))
    assert _nah.decide("git reset --hard", "/tmp") == (["git-hard-reset"], None)
    assert runs(tmp_path) == 1


def script(tmp_path: Path, body: str) -> Path:
    nah = tmp_path / "nah"
    nah.write_text("#!/bin/sh\ncat >/dev/null\n" + body + "\n")
    nah.chmod(0o755)
    return nah


@pytest.mark.parametrize(("body", "kind"), [
    ("sleep 5", "timeout"),
    ("echo 'no valid decision' >&2; exit 3", "exit"),
])
def test_failure_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, kind: str) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", str(script(tmp_path, body)))
    with pytest.raises(_nah.NahError) as e:
        _nah.decide("ls", "/tmp", timeout=0.5)
    assert e.value.kind == kind


def test_missing_nah_is_its_own_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTGUARD_NAH", "/nonexistent/nah")
    with pytest.raises(_nah.NahError) as e:
        _nah.decide("ls", "/tmp")
    assert e.value.kind == "unrunnable"
