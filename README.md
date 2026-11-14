# hintguard

A pre-tool-use hook for coding agents (Claude Code, Codex) that denies risky shell commands with
**one line telling the agent how to rewrite the command**. It never asks a human: no permission
prompt, no "ask the user", no approval code. The agent reads the line as the tool's error and
rewrites the command itself.

```text
$ hintguard check 'git reset --hard HEAD~1'
Denied (git-hard-reset): Run git stash first, or use git reset --keep, or restore single files. Why: it discards uncommitted changes.

$ hintguard check 'rm -rf $(dirname $T)'
Denied (rm-unresolvable): Delete only literal paths: write throwaway files to a temporary directory and skip the cleanup, or rm a literal path. Why: the rm target is only known at run time.

$ hintguard check 'ls -la'          # allowed: no output
```

## Why

Coding agents stop and ask the operator before risky commands. In unattended sessions nobody answers,
and in attended ones the operator approves without reading. Existing guards block with messages
written for a human ("ask the user for explicit permission", "approve with allow-once <code>"), or
bury the advice under tooling. hintguard keeps the detection and replaces the message with a short
rewrite instruction for the agent.

## How it works

- **Detection.** [nah](https://github.com/manuelschipper/nah) decides, through `nah decide`. nah is a
  deterministic guard that parses each command into its effects. hintguard calls it unchanged and adds
  its own rules on the [tree-sitter-bash](https://github.com/tree-sitter/tree-sitter-bash) syntax tree
  for what nah cannot see: nah's extension guards never receive commands with unresolved expansions,
  such as `rm -rf $(dirname $T)`, and nah has no notion of a wait that never ends, such as
  `while pgrep -f job.sh; do sleep 30; done` (`pgrep -f` matches the `sh -c` running it).
- **Wording.** [`hintguard/hints.toml`](hintguard/hints.toml) has one hint per nah guard and per own rule:
  a rewrite instruction, a short reason, and an example command that follows the hint.
- **Output.** A deny is the standard PreToolUse JSON (`permissionDecision: deny`), which Claude Code and
  Codex both accept. When nothing fires, hintguard prints nothing.

## Design rules, enforced by the tests

[`tests/test_hints.py`](tests/test_hints.py) fails if a hint breaks any of these:

- every rule that can deny has a hint, including every guard of the installed nah;
- the rewrite starts with an imperative verb, the reason has at most 12 words, and the whole line fits
  in 200 characters;
- no hint mentions a person: user, operator, approval, permission, manual steps;
- the hint text names a generic fix, not a local path or site-specific tool;
- a hint gives an example command, or says to drop the step when no safe variant exists;
- hintguard does not deny the example itself ([`tests/test_cli.py`](tests/test_cli.py)), so following a
  hint never leads to another deny.

[`tests/test_corpus.py`](tests/test_corpus.py) replays nah's own block cases (5,562 in its default Linux
context, minus those of the five disabled guards) and checks that each block comes out as one of our hints.

## Install

Python 3.12 or later, and nah 1.7.0 on `PATH` (or in `~/.local/bin`, or named by `HINTGUARD_NAH`):

```console
pip install git+https://github.com/chains-project/hintguard
curl -fsSLO https://github.com/manuelschipper/nah/releases/download/v1.7.0/nah-x86_64-unknown-linux-musl.tar.gz
tar -xzf nah-x86_64-unknown-linux-musl.tar.gz nah && install -m 755 nah ~/.local/bin/nah
hintguard setup        # enable the nah guards hintguard expects (43 of 48)
```

Claude Code, `~/.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|Monitor", "hooks": [{"type": "command", "command": "hintguard hook"}]}]}}
```

`Monitor` (a background shell watch) runs a shell command too; hintguard checks both tools.
Codex, `~/.codex/hooks.json` (matcher `Bash`). Then trust the hook once in Codex's `/hooks` view:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/absolute/path/to/hintguard hook"}]}]}}
```

## Usage

```console
hintguard check CMD [--cwd DIR] [--json]   # dry-run one command: the deny line, or nothing
hintguard hook                              # PreToolUse hook: hook JSON on stdin, deny JSON on stdout
hintguard hints [--json]                    # every rule with its hint
hintguard setup [--dry-run]                 # align nah's enabled guards; prints only what changed
hintguard stats [--since 7d] [--json]       # local usage: calls, denies per rule, what the agent did next
```

If nah is missing, fails, or takes more than 5 s, hintguard prints one line on stderr and lets the
command through (exit 1, which Claude Code treats as non-blocking); its own rules still apply. When
nah delegates because its evaluation deadline ran out (a loaded host), hintguard asks again, up to
three runs in all; a partial check for other reasons (a command nah cannot fully analyze) is final. Each
hooked call appends one line to `~/.local/state/hintguard/decisions.jsonl` (`HINTGUARD_LOG`
overrides): time, session and tool-call ids, rules, latency, and whether nah checked only partially.
The command text is never logged.

## Guard set

All 48 nah guards except five that mostly flag routine agent work. On 10,000 real Claude Code Bash
calls, they flagged pushes to `main` (`git-protected-push`), cleanup of temporary directories
(`fs-outside-workspace-delete`), `git pull --rebase` (`git-history-rewrite`), branch deletion
(`git-ref-delete`) and service restarts (`sys-service-stop`). On the same calls hintguard denies 1.6%.

## Development

```console
pip install -e '.[dev]'
ruff check . && mypy hintguard && pytest
```

Tests that need nah are skipped when it is not installed; CI installs nah 1.7.0.

## License

MIT. [`tests/data/nah-corpus/`](tests/data/nah-corpus/) is an excerpt of nah's corpus, MIT,
Copyright (c) 2026 Manuel Schipper.
