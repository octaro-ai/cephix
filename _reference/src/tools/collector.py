"""ToolCollector — aggregates multiple ToolDrivers into a unified catalog + executor.

Replaces the old pattern of manually wiring catalogs, handlers, and executors
separately.  Feed it a list of ``ToolDriverPort`` implementations and it
provides both ``ToolCatalogPort`` and ``ToolExecutionPort`` in one object.
"""

from __future__ import annotations

from typing import Any

from src.domain import ExecutionContext
from src.tools.models import ToolDefinition
from src.tools.ports import ToolDriverPort


class ToolCollector:
    """Aggregates multiple ``ToolDriverPort`` instances.

    Implements both ``ToolCatalogPort`` (list_available / get_definition) and
    can resolve + execute any tool through the owning driver.
    """

    def __init__(self, drivers: list[ToolDriverPort] | None = None) -> None:
        self._drivers: list[ToolDriverPort] = list(drivers or [])
        self._index: dict[str, ToolDriverPort] | None = None

    def add_driver(self, driver: ToolDriverPort) -> None:
        self._drivers.append(driver)
        self._index = None  # invalidate cache

    # -- ToolCatalogPort interface ------------------------------------------

    def list_available(self, *, tags: list[str] | None = None) -> list[ToolDefinition]:
        all_defs = [
            defn
            for driver in self._drivers
            for defn in driver.list_tools()
        ]
        if tags:
            tag_set = set(tags)
            all_defs = [
                d for d in all_defs
                if tag_set & set(d.metadata.get("tags", []))
            ]
        return all_defs

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        driver = self._build_index().get(tool_name)
        if driver is None:
            return None
        for defn in driver.list_tools():
            if defn.name == tool_name:
                return defn
        return None

    # -- ToolExecutionPort interface ----------------------------------------

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        driver = self._build_index().get(tool_name)
        if driver is None:
            raise RuntimeError(f"No driver registered for tool: {tool_name!r}")
        return driver.execute(ctx, tool_name, arguments)

    # -- Internal -----------------------------------------------------------

    def _build_index(self) -> dict[str, ToolDriverPort]:
        if self._index is not None:
            return self._index
        index: dict[str, ToolDriverPort] = {}
        for driver in self._drivers:
            for defn in driver.list_tools():
                index[defn.name] = driver
        self._index = index
        return index
