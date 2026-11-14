"""End to end: the hook protocol, with nah, and the examples of every hint (no hint loops)."""
from __future__ import annotations

import io
import json
import shutil
import sys

import pytest

from hintguard import _cli
from hintguard._hints import table

needs_nah = pytest.mark.skipif(not shutil.which("nah"), reason="nah not installed")


def run(argv: list[str], stdin: str = "", monkeypatch: pytest.MonkeyPatch | None = None,
        capsys: pytest.CaptureFixture[str] | None = None) -> tuple[int, str, str]:
    assert monkeypatch is not None and capsys is not None
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = _cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def payload(cmd: str, tool: str = "Bash") -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": "/tmp",
                       "tool_input": {"command": cmd}})


@needs_nah
def test_hook_denies_with_one_hint_line(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    code, out, err = run(["hook"], payload("git reset --hard HEAD~1"), monkeypatch, capsys)
    assert code == 0 and err == ""
    h = json.loads(out)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert h["permissionDecisionReason"] == table()["git-hard-reset"].line()


@needs_nah
def test_hook_is_silent_when_allowed(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(["hook"], payload("ls -la"), monkeypatch, capsys) == (0, "", "")


def test_hook_ignores_other_tools(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    assert run(["hook"], payload("rm -rf $X", tool="Read"), monkeypatch, capsys) == (0, "", "")


def test_hook_checks_monitor_commands(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HINTGUARD_NAH", "/nonexistent/nah")
    code, out, _ = run(["hook"], payload("while pgrep -f 'job.sh'; do sleep 9; done", tool="Monitor"),
                       monkeypatch, capsys)
    assert code == 0 and "Denied (pgrep-self-match):" in out


def test_own_rule_works_without_nah(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HINTGUARD_NAH", "/nonexistent/nah")
    code, out, err = run(["hook"], payload("rm -rf $(dirname $T)"), monkeypatch, capsys)
    assert code == 0 and "hintguard:" in err
    assert json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"].startswith(
        "Denied (rm-unresolvable):")


@needs_nah
@pytest.mark.parametrize("h", [h for h in table().values() if h.example], ids=lambda h: h.rule)
def test_hint_example_is_not_denied(h, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """Following a hint must not lead to another deny."""
    code, out, err = run(["check", h.example, "--cwd", "/tmp"], "", monkeypatch, capsys)
    assert (code, out, err) == (0, "", "")


def test_hook_logs_without_command_text(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    log = tmp_path / "d.jsonl"
    monkeypatch.setenv("HINTGUARD_LOG", str(log))
    monkeypatch.setenv("HINTGUARD_NAH", "/nonexistent/nah")
    p = json.loads(payload("rm -rf $(dirname $SECRETPATH)"))
    for k, cmd in enumerate(["rm -rf $(dirname $SECRETPATH)", "rm -rf /tmp/x", "rm -f $A", "rm -f $A"]):
        p.update(session_id="s1", tool_use_id=f"t{k}", tool_input={"command": cmd})
        run(["hook"], json.dumps(p), monkeypatch, capsys)
    text = log.read_text()
    assert "SECRETPATH" not in text and len(text.splitlines()) == 4
    _, out, _ = run(["stats", "--json"], "", monkeypatch, capsys)
    s = json.loads(out)
    assert (s["calls"], s["denied"], s["sessions"]) == (4, 3, 1)
    assert s["after_deny"] == {"next_call_passed": 1, "denied_again_same_rule": 1, "no_further_bash_call": 1}


def test_unknown_rule_still_denies_with_a_hint() -> None:
    from hintguard._hints import hint
    h = hint("guard-from-a-newer-nah")
    assert h.line().startswith("Denied (guard-from-a-newer-nah): Rewrite the command")
