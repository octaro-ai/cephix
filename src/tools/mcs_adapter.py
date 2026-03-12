"""Adapter that bridges MCS (Model Context Standard) ToolDrivers into Cephix.

The MCS SDK is an external dependency.  This adapter wraps an MCS ToolDriver
(defined here as a Protocol for decoupling) so that its tools appear as a
standard ``ToolDriverPort`` — same interface as SystemToolDriver, domain
drivers, etc.

Usage::

    from some_mcs_sdk import SomeMCSToolDriver

    mcs_driver = SomeMCSToolDriver(config=...)
    adapter = MCSToolDriverAdapter(driver=mcs_driver, namespace="mcs.crm")

    # Use as any ToolDriverPort
    for tool_def in adapter.list_tools():
        print(tool_def.name)

    result = adapter.execute(ctx, "mcs.crm.search_contacts", {"query": "Doe"})
"""

from __future__ import annotations

from typing import Any, Protocol

from src.domain import ExecutionContext
from src.tools.models import ToolDefinition, ToolParameter


class MCSToolDriverPort(Protocol):
    """Protocol mirroring the MCS ToolDriver interface."""

    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        ...


class MCSToolDriverAdapter:
    """Wraps an MCS ToolDriver into the Cephix ToolDriverPort interface."""

    def __init__(self, driver: MCSToolDriverPort, namespace: str) -> None:
        self._driver = driver
        self._namespace = namespace

    def _namespaced(self, name: str) -> str:
        return f"{self._namespace}.{name}" if self._namespace else name

    def _strip_namespace(self, namespaced_name: str) -> str:
        prefix = f"{self._namespace}."
        if self._namespace and namespaced_name.startswith(prefix):
            return namespaced_name[len(prefix):]
        return namespaced_name

    # -- ToolDriverPort interface -------------------------------------------

    def list_tools(self) -> list[ToolDefinition]:
        raw_tools = self._driver.list_tools()
        definitions: list[ToolDefinition] = []
        for raw in raw_tools:
            definitions.append(self._convert(raw))
        return definitions

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        original_name = self._strip_namespace(tool_name)
        return self._driver.execute_tool(original_name, arguments)

    # -- Convenience --------------------------------------------------------

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        for defn in self.list_tools():
            if defn.name == tool_name:
                return defn
        return None

    def _convert(self, raw: dict[str, Any]) -> ToolDefinition:
        raw_params = raw.get("parameters", [])
        params: list[ToolParameter] = []
        if isinstance(raw_params, list):
            for p in raw_params:
                if isinstance(p, dict):
                    params.append(ToolParameter(
                        name=p.get("name", ""),
                        type=p.get("type", "string"),
                        description=p.get("description", ""),
                        required=p.get("required", True),
                        enum=p.get("enum"),
                    ))

        return ToolDefinition(
            name=self._namespaced(raw.get("name", "")),
            description=raw.get("description", ""),
            parameters=params,
            metadata={
                "source": "mcs",
                "namespace": self._namespace,
                "original_name": raw.get("name", ""),
            },
        )
