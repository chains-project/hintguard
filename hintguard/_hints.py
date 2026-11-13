"""The hint table: one agent-facing rewrite instruction per rule, loaded from hints.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

MAX_LEN = 200


@dataclass(frozen=True)
class Hint:
    rule: str
    rewrite: str
    why: str
    example: str | None = None

    def line(self) -> str:
        """The single line the agent reads."""
        return f"Denied ({self.rule}): {self.rewrite} Why: {self.why}"


@cache
def table() -> dict[str, Hint]:
    data = tomllib.loads(files("hintguard").joinpath("hints.toml").read_text())
    return {rule: Hint(rule, v["rewrite"], v["why"], v.get("example")) for rule, v in data.items()}


def hint(rule: str) -> Hint:
    """The hint for a rule; a rule without one is a bug, caught by the tests, so fail loudly."""
    return table()[rule]
