"""hintguard's own static rules, on the tree-sitter-bash syntax tree.

They cover what nah cannot see: its custom guards never receive commands with unresolved expansions,
it has no notion of a wait that can never end or of a pkill that kills its own shell, and it never
looks at the repository a `git commit` runs in (submodule pointers, via `git submodule status`).
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator
from typing import NamedTuple

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

_PARSER = Parser(Language(tree_sitter_bash.language()))

# Nodes whose value is only known at run time.
_DYNAMIC = {"command_substitution", "simple_expansion", "expansion", "process_substitution",
            "arithmetic_expansion"}


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _program(cmd: Node) -> str:
    name = cmd.child_by_field_name("name")
    return os.path.basename(name.text.decode()) if name is not None and name.text else ""


def _is_dynamic(node: Node) -> bool:
    return any(n.type in _DYNAMIC for n in _walk(node))


# Programs that run their arguments as a command: `sudo rm $x` is an rm.
_WRAPPERS = {"sudo", "env", "nice", "nohup", "timeout", "time", "command", "exec", "xargs", "doas"}
_SHELLS = {"bash", "sh", "zsh", "dash"}


def _text(node: Node) -> str:
    return node.text.decode() if node.text else ""


def _rm_args(cmd: Node) -> list[Node] | None:
    """The arguments of the rm this command runs, directly or through a wrapper; None if no rm."""
    args = cmd.children_by_field_name("argument")
    if _program(cmd) == "rm":
        return args
    if _program(cmd) in _WRAPPERS:
        for k, arg in enumerate(args):
            if os.path.basename(_text(arg)) == "rm":
                return args[k + 1:]
    return None


def _shell_c_scripts(cmd: Node) -> Iterator[str]:
    """The script of `bash -c '...'`, when it is a literal string."""
    if _program(cmd) not in _SHELLS:
        return
    args = cmd.children_by_field_name("argument")
    for k, arg in enumerate(args[:-1]):
        if _text(arg) == "-c" and args[k + 1].type in ("raw_string", "string"):
            yield _text(args[k + 1])[1:-1]


def rm_unresolvable(tree_root: Node) -> bool:
    """An `rm` whose target (a non-option argument) holds $VAR, ${...}, $(...) or backticks."""
    for cmd in _walk(tree_root):
        if cmd.type != "command":
            continue
        for script in _shell_c_scripts(cmd):
            if rm_unresolvable(_PARSER.parse(script.encode()).root_node):
                return True
        args = _rm_args(cmd)
        if args is None:
            continue
        for arg in args:
            if _is_dynamic(arg):
                return True
    return False


# `pgrep -f PAT` skips only itself, not the `sh -c` running it, whose argv holds PAT: a wait on it never
# ends. A pgrep that opens a condition (after while/until/do/||/&&, maybe negated) inside a shell loop or
# a `cmd:` condition (wake-up tools run those in `sh -c`), whose pattern read as a regex matches the
# command itself. `'[x]yz'` cannot match itself and passes. A pgrep inside a quoted argument (`"pgrep …"`
# typed into another tool) does not open a condition here and passes.
_PGREP = re.compile(r"(\b(while|until|do)\s+|\|\||&&)\s*(!\s*)?pgrep\b(?P<opts>(\s+-\w+)*)\s+"
                    r"(?P<pat>'[^']*'|\"[^\"]*\"|[^\s;|&)]+)")
_WAITS = re.compile(r"\b(while|until)\b.*[;\n]\s*do\b.*[;\n]\s*done\b|\bcmd:", re.DOTALL)


def self_matching_pgrep(command: str) -> str | None:
    """The first `pgrep -f` pattern of a wait in `command` that matches `command` itself, else None."""
    if not _WAITS.search(command):
        return None
    for m in _PGREP.finditer(command):
        if not any("f" in o for o in m.group("opts").split()):
            continue
        pat = m.group("pat")
        raw = pat[1:-1] if pat[0] in "'\"" else pat
        try:
            hit = re.search(raw, command) is not None
        except re.error:
            hit = raw in command
        if hit:
            return pat
    return None


def pgrep_self_match(tree_root: Node) -> bool:
    return self_matching_pgrep(_text(tree_root)) is not None


def _unquote(arg: Node) -> str | None:
    """The literal value of a word or quoted string; None if it holds an expansion."""
    if _is_dynamic(arg):
        return None
    text = _text(arg)
    if arg.type in ("raw_string", "string") and len(text) >= 2:
        return text[1:-1]
    return text


def _command_args(cmd: Node, program: str) -> list[Node] | None:
    """The arguments of `program` run by this command, directly or through a wrapper; None if not run."""
    args = cmd.children_by_field_name("argument")
    if _program(cmd) == program:
        return args
    if _program(cmd) in _WRAPPERS:
        for k, arg in enumerate(args):
            if os.path.basename(_text(arg)) == program:
                return args[k + 1:]
    return None


# pkill options that take a value as the next argument.
_PKILL_VALUED = {"-d", "-g", "-G", "-P", "-s", "-t", "-u", "-U", "-F", "--signal", "--ns", "--nslist",
                 "--pidfile", "--delimiter", "--pgroup", "--group", "--parent", "--session", "--terminal",
                 "--euid", "--uid", "-O", "--older"}


def _pkill_full_pattern(args: list[Node]) -> str | None:
    """The literal pattern of a `pkill -f` (full command line match); None if no -f or not literal."""
    full, pattern, skip = False, None, False
    for arg in args:
        text = _text(arg)
        if skip:
            skip = False
        elif text in _PKILL_VALUED:
            skip = True
        elif text == "--full":
            full = True
        elif text.startswith("--"):
            continue
        elif text.startswith("-") and len(text) > 1:
            full = full or "f" in text[1:]
        elif pattern is None:
            pattern = _unquote(arg) or ""
    return pattern if full and pattern else None


def self_matching_pkill(command: str, root: Node | None = None) -> str | None:
    """The first literal `pkill -f` pattern in `command` that, read as a regex, matches `command` itself.

    The agent's shell runs the command as `bash -c '<command>'`: pkill skips only itself, so a pattern
    that matches the command text kills the shell running it (checked: `pgrep -af X` run by Claude Code
    prints that `bash -c` line). `'[j]ob.sh'` cannot match itself and passes.
    """
    root = root if root is not None else _PARSER.parse(command.encode()).root_node
    for cmd in _walk(root):
        if cmd.type != "command":
            continue
        for script in _shell_c_scripts(cmd):
            if (pat := self_matching_pkill(command, _PARSER.parse(script.encode()).root_node)) is not None:
                return pat
        args = _command_args(cmd, "pkill")
        if args is None or (pat := _pkill_full_pattern(args)) is None:
            continue
        try:
            hit = re.search(pat, command) is not None
        except re.error:
            hit = pat in command
        if hit:
            return pat
    return None


def pkill_self_match(tree_root: Node) -> bool:
    return self_matching_pkill(_text(tree_root), tree_root) is not None


RULES = {"rm-unresolvable": rm_unresolvable, "pgrep-self-match": pgrep_self_match,
         "pkill-self-match": pkill_self_match}


# --- Rules that read the repository the command runs in (they need its working directory). ---

def _git_sub(args: list[Node]) -> tuple[str | None, list[str], list[Node]]:
    """(subcommand, the -C directories before it, its arguments) of a git invocation."""
    dirs: list[str] = []
    k = 0
    while k < len(args):
        text = _text(args[k])
        if text in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and k + 1 < len(args):
            if text == "-C":
                dirs.append(_unquote(args[k + 1]) or "")
            k += 2
        elif text.startswith("-"):
            k += 1
        else:
            return text, dirs, args[k + 1:]
    return None, dirs, []


# Short `git commit` options whose value follows in the same cluster or the next argument.
_COMMIT_VALUED = set("mFcCtS")


def _commits_all(args: list[Node]) -> bool:
    """Whether `git commit` args include -a/--all (possibly in a cluster like -am)."""
    for arg in args:
        text = _text(arg)
        if text in ("--all", "-a"):
            return True
        if text.startswith("-") and not text.startswith("--"):
            for ch in text[1:]:
                if ch == "a":
                    return True
                if ch in _COMMIT_VALUED:
                    break
    return False


def _adds_all(args: list[Node]) -> bool:
    """Whether `git add` args stage everything (-A, --all, -u, --update, ., :/)."""
    for arg in args:
        text = _text(arg)
        if text in ("-A", "--all", "-u", "--update", ".", ":/", "--no-ignore-removal"):
            return True
        if text.startswith("-") and not text.startswith("--") and set(text[1:]) & {"A", "u"}:
            return True
    return False


class _Commit(NamedTuple):
    cwd: str
    all: bool  # the command stages every tracked change for this commit (commit -a, add -A/-u/. before it)


def _commits(root: Node, cwd: str) -> list[_Commit]:
    """Every `git commit` the command runs, with its directory (literal `cd` and `-C` followed)."""
    out: list[_Commit] = []
    here, staged_all = cwd, False
    for cmd in _walk(root):
        if cmd.type != "command":
            continue
        args = cmd.children_by_field_name("argument")
        if _program(cmd) == "cd":
            target = _unquote(args[0]) if args else os.path.expanduser("~")
            if target:
                here = os.path.normpath(os.path.join(here, os.path.expanduser(target)))
            continue
        git_args = _command_args(cmd, "git")
        if git_args is None:
            continue
        sub, dirs, rest = _git_sub(git_args)
        where = here
        for d in dirs:
            where = os.path.normpath(os.path.join(where, os.path.expanduser(d)))
        if sub == "add" and _adds_all(rest):
            staged_all = True
        elif sub == "commit":
            out.append(_Commit(where, staged_all or _commits_all(rest)))
    return out


def _git(cwd: str, *args: str) -> list[str] | None:
    """NUL- or newline-separated output of a git command in cwd; None if it fails."""
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10,
                           check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return [x for x in re.split(r"[\0\n]", r.stdout) if x]


def submodule_problems(cwd: str, commit_all: bool) -> list[str]:
    """Submodule pointers a `git commit` in cwd would record by accident, as `<path>: <why>`.

    It runs `git submodule status`: a `+` pointer (checkout differs from the index) is committed as is by
    a commit that stages everything (`commit -a`, `add -A`/`-u`/`.` first). A submodule whose
    pointer deletion is staged (or, staging everything, whose directory is gone) while `.gitmodules`
    still declares it is a lost checkout, not a removal: `git rm PATH` also updates `.gitmodules`.
    """
    if not os.path.isdir(cwd) or not (top := _git(cwd, "rev-parse", "--show-toplevel")):
        return []
    root = top[0]
    declared = {line.split(" ", 1)[1] for line in
                _git(root, "config", "--blob", ":.gitmodules", "--get-regexp", r"^submodule\..*\.path$") or []
                if " " in line}
    if not declared:
        return []
    status = _git(root, "submodule", "status") or []
    problems = []
    if commit_all:
        for line in status:
            if line.startswith("+"):
                problems.append(f"{line[1:].split()[1]}: checkout differs from the recorded pointer")
    deleted = set(_git(root, "diff", "--cached", "--name-only", "--diff-filter=D", "-z") or [])
    if commit_all:
        deleted |= set(_git(root, "diff", "--name-only", "--diff-filter=D", "-z") or [])
    problems += [f"{p}: pointer deleted while .gitmodules declares it" for p in sorted(deleted & declared)]
    return problems


def submodule_drift(root: Node, cwd: str) -> bool:
    return any(submodule_problems(c.cwd, c.all) for c in _commits(root, cwd))


REPO_RULES = {"submodule-drift": submodule_drift}


def check(command: str, cwd: str | None = None) -> list[str]:
    """Names of the own rules the command breaks, in table order; repository rules only with a cwd."""
    root = _PARSER.parse(command.encode()).root_node
    names = [name for name, rule in RULES.items() if rule(root)]
    if cwd is not None:
        names += [name for name, rule in REPO_RULES.items() if rule(root, cwd)]
    return names
