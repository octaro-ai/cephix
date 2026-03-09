from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolParameter:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    enum: list[str] | None = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_schema(self) -> dict[str, Any]:
        """Convert to JSON Schema format suitable for LLM function calling."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in self.parameters:
            prop: dict[str, Any] = {"type": param.type, "description": param.description}
            if param.enum is not None:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict[str, Any]
