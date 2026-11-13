# hintguard

A PreToolUse hook for coding agents that denies risky shell commands with **one crisp line telling
the agent how to rewrite the command**. It never asks a human: no permission prompt, no "ask the
user", no approval code.

```text
Denied (git-hard-reset): Run git stash first, or use git reset --keep, or restore single files. Why: it discards uncommitted changes.
```

Detection comes from [nah](https://github.com/manuelschipper/nah) (unforked, called through
`nah decide`) plus hintguard's own rules on the tree-sitter-bash syntax tree, for what nah does not
see, such as `rm -rf $(dirname $T)`. The wording is hintguard's: `hintguard/hints.toml` has one hint per
nah guard and per own rule.

## Design rules (enforced by `tests/test_hints.py`)

- Every rule that can deny has a hint.
- A hint starts with an imperative verb, has a reason of at most 12 words, and fits on one line of at
  most 200 characters.
- A hint never mentions a person: user, operator, approval, permission, manual steps.
- A hint gives a concrete `example` command, or says to drop the step when no safe variant exists.
- The example is not denied by hintguard itself, so following a hint never leads to another deny.

## Install

```console
uv tool install /path/to/hintguard        # or: pip install -e .
# nah v1.7.0 on PATH (release binary), or HINTGUARD_NAH=/path/to/nah
hintguard setup                            # enable the nah guards hintguard expects (43 of 48)
```

Claude Code, `~/.claude/settings.json`:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "hintguard hook"}]}]}}
```

## Usage

```console
hintguard check 'git reset --hard'      # prints the deny line, nothing if allowed
hintguard check 'rm -rf $X' --json      # {"denied": true, "rules": [...], "reason": ..., "error": null}
hintguard hints [--json]                 # every rule with its hint
hintguard setup [--dry-run]              # align nah's enabled guards; prints only changes
```

`hook` reads the Claude Code PreToolUse JSON on stdin. It prints a `permissionDecision: deny` JSON on
stdout when a rule fires, and nothing otherwise. If nah is missing or fails, hintguard prints one line on
stderr, exits 1 (non-blocking), and its own rules still apply.

## Guard set

All 48 nah guards except five that flag routine work in our trajectories (10,000 sampled calls, see
`bash-safety-analysis-study`): `git-protected-push`, `fs-outside-workspace-delete`,
`git-history-rewrite`, `git-ref-delete`, `sys-service-stop`.

## License

MIT. `tests/data/nah-corpus/` is an excerpt of nah's corpus, MIT, Copyright (c) 2026 Manuel Schipper.
