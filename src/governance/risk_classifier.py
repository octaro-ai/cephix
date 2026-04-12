from __future__ import annotations

from src.governance.domain import RiskClass
from src.tools.models import ToolDefinition
from src.tools.ports import ToolRegistryPort


class MetadataRiskClassifier:
    """Reads the risk class from ``ToolDefinition.metadata["risk_class"]``.

    Falls back to ``LOW_RISK_MUTATION`` if the key is absent.
    """

    def __init__(self, *, registry: ToolRegistryPort) -> None:
        self._registry = registry

    def classify(self, tool_name: str) -> RiskClass:
        for tool_def in self._registry.list_mounted():
            if tool_def.name == tool_name:
                raw = tool_def.metadata.get("risk_class", "")
                try:
                    return RiskClass(raw)
                except ValueError:
                    return RiskClass.LOW_RISK_MUTATION
        return RiskClass.LOW_RISK_MUTATION
