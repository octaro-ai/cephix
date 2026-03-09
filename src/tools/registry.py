from __future__ import annotations

from typing import Any

from src.tools.models import ToolDefinition
from src.tools.ports import ToolCatalogPort


class InMemoryToolRegistry:
    """Runtime working set of currently mounted tools.

    Uses a ``ToolCatalogPort`` to look up definitions when mounting.
    """

    def __init__(self, catalog: ToolCatalogPort) -> None:
        self._catalog = catalog
        self._mounted: dict[str, ToolDefinition] = {}

    def list_mounted(self) -> list[ToolDefinition]:
        return list(self._mounted.values())

    def mount(self, tool_name: str) -> None:
        if tool_name in self._mounted:
            return
        definition = self._catalog.get_definition(tool_name)
        if definition is None:
            raise ValueError(f"Unknown tool: {tool_name!r} (not in catalog)")
        self._mounted[tool_name] = definition

    def unmount(self, tool_name: str) -> None:
        self._mounted.pop(tool_name, None)

    def unmount_all(self) -> None:
        self._mounted.clear()

    def get_schemas(self) -> list[dict[str, Any]]:
        return [defn.to_schema() for defn in self._mounted.values()]
