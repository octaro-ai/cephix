"""Adapter that bridges MCS (Model Context Standard) ToolDrivers into Cephix.

The MCS SDK is an external dependency.  This adapter wraps an MCS ToolDriver
(defined here as a Protocol for decoupling) so that its tools appear in the
Cephix ``ToolCatalogPort`` and are executable through ``ToolExecutionPort``.

Usage::

    from some_mcs_sdk import SomeMCSToolDriver

    mcs_driver = SomeMCSToolDriver(config=...)
    adapter = MCSToolDriverAdapter(driver=mcs_driver, namespace="mcs.crm")

    # Register in the catalog
    for tool_def in adapter.list_tools():
        catalog.register(tool_def)

    # Execute through the adapter
    result = adapter.execute("mcs.crm.search_contacts", {"query": "Doe"})
"""

from __future__ import annotations

from typing import Any, Protocol

from src.tools.models import ToolDefinition, ToolParameter


class MCSToolDriverPort(Protocol):
    """Protocol mirroring the MCS ToolDriver interface."""

    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        ...


class MCSToolDriverAdapter:
    """Wraps an MCS ToolDriver into Cephix tool ports."""

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

    def list_tools(self) -> list[ToolDefinition]:
        raw_tools = self._driver.list_tools()
        definitions: list[ToolDefinition] = []
        for raw in raw_tools:
            definitions.append(self._convert(raw))
        return definitions

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        for defn in self.list_tools():
            if defn.name == tool_name:
                return defn
        return None

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        original_name = self._strip_namespace(tool_name)
        return self._driver.execute_tool(original_name, arguments)

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
