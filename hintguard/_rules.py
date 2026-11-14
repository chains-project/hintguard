"""hintguard's own static rules, on the tree-sitter-bash syntax tree.

They cover what nah cannot see: its custom guards never receive commands with unresolved expansions,
and it has no notion of a wait that can never end.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterator

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


RULES = {"rm-unresolvable": rm_unresolvable, "pgrep-self-match": pgrep_self_match}


def check(command: str) -> list[str]:
    """Names of the own rules the command breaks, in table order."""
    root = _PARSER.parse(command.encode()).root_node
    return [name for name, rule in RULES.items() if rule(root)]
