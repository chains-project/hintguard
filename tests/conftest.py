"""Every test logs to its own temporary file, never to the real usage log."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HINTGUARD_LOG", str(tmp_path / "decisions.jsonl"))
