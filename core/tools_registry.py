"""
Empty placeholder for future tool/MCP integration.

When you're ready to add tools (e.g. file search, web browse, MCP servers),
register them here. The chat loop will call get_tools() to discover what's
available and include tool descriptions in the system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class Tool:
    """One callable tool the model can invoke."""

    name: str
    description: str
    function: Callable[..., Any]
    # Future: parameter schema, required permissions, etc.


class ToolsRegistry:
    """Simple registry — register tools, look them up by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register_tool(
        self,
        name: str,
        function: Callable[..., Any],
        description: str = "",
    ) -> None:
        """Register a tool. Overwrites if the name already exists."""
        self._tools[name] = Tool(
            name=name, description=description, function=function
        )

    def get_tool(self, name: str) -> Tool | None:
        """Look up a single tool by name."""
        return self._tools.get(name)

    def get_tools(self) -> list[Tool]:
        """Return all registered tools (for building the system prompt)."""
        return list(self._tools.values())
