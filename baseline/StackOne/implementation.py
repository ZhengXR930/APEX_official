"""StackOne tool-catalog transformation boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


class StackOne:
    def __init__(self, transform: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]):
        self.transform = transform

    def protect(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        source = deepcopy(tools)
        protected = self.transform(source)
        if not isinstance(protected, list) or not all(
            isinstance(tool, dict) and isinstance(tool.get("name"), str)
            for tool in protected
        ):
            raise ValueError("StackOne transform returned an invalid tool catalog")
        names = [tool["name"] for tool in protected]
        if len(names) != len(set(names)):
            raise ValueError("StackOne transform returned duplicate tool names")
        return protected
