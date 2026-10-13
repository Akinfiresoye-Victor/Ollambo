"""
Tool/MCP integration registry.

Register callables here so the chat loop can discover them via get_tools().
Web research tools (web_search, web_fetch_pages) are pre-registered when
internet access is available; they live in core.web_research (no UI imports).
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


# ---------------------------------------------------------------------------
# Module-level singleton with web research tools pre-registered.
# Web functions are imported lazily so the registry is safe to import
# even when ddgs/httpx are absent (they raise ImportError at call time).
# ---------------------------------------------------------------------------

_registry = ToolsRegistry()


def _lazy_search(query: str, max_results: int = 10) -> list[dict]:  # type: ignore[return]
    from core.web_research import search  # noqa: PLC0415
    return search(query, max_results=max_results)


def _lazy_fetch_pages(urls: list[str]) -> list[dict]:  # type: ignore[return]
    from core.web_research import fetch_pages  # noqa: PLC0415
    return fetch_pages(urls)


_registry.register_tool(
    name="web_search",
    function=_lazy_search,
    description=(
        "Search the web via DuckDuckGo (no API key). "
        "Args: query(str), max_results(int=10). "
        "Returns list of {title, url, snippet}."
    ),
)
_registry.register_tool(
    name="web_fetch_pages",
    function=_lazy_fetch_pages,
    description=(
        "Download web pages in parallel and return their HTML. "
        "Args: urls(list[str]). "
        "Blocks private/local addresses automatically."
    ),
)


def get_registry() -> ToolsRegistry:
    """Return the shared ToolsRegistry singleton."""
    return _registry
