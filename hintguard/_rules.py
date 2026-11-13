"""hintguard's own static rules, on the tree-sitter-bash syntax tree.

They cover what nah cannot see: its custom guards never receive commands with unresolved expansions.
"""
from __future__ import annotations

import os
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


RULES = {"rm-unresolvable": rm_unresolvable}


def check(command: str) -> list[str]:
    """Names of the own rules the command breaks, in table order."""
    root = _PARSER.parse(command.encode()).root_node
    return [name for name, rule in RULES.items() if rule(root)]
