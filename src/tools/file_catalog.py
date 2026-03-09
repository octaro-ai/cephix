"""File-system-backed tool catalog.

Reads tool definitions from YAML files in a directory.  Each YAML file
describes one tool.

Expected format::

    name: mail.list
    description: Lists emails from the inbox
    parameters:
      - name: folder
        type: string
        description: Mail folder to list
        required: false
    metadata:
      tags: [email, communication]
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from src.tools.models import ToolDefinition, ToolParameter


class FileToolCatalog:
    """Discovers tool definitions from YAML files on disk."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._cache: dict[str, ToolDefinition] | None = None

    def list_available(self, *, tags: list[str] | None = None) -> list[ToolDefinition]:
        definitions = list(self._load_all().values())
        if tags:
            tag_set = set(tags)
            definitions = [
                d for d in definitions
                if tag_set & set(d.metadata.get("tags", []))
            ]
        return definitions

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        return self._load_all().get(tool_name)

    def _load_all(self) -> dict[str, ToolDefinition]:
        if self._cache is not None:
            return self._cache

        result: dict[str, ToolDefinition] = {}
        if not self._directory.exists():
            self._cache = result
            return result

        for path in sorted(self._directory.glob("*.yaml")):
            defn = self._parse_file(path)
            if defn is not None:
                result[defn.name] = defn
        for path in sorted(self._directory.glob("*.yml")):
            defn = self._parse_file(path)
            if defn is not None and defn.name not in result:
                result[defn.name] = defn

        self._cache = result
        return result

    def reload(self) -> None:
        self._cache = None

    @staticmethod
    def _parse_file(path: Path) -> ToolDefinition | None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or "name" not in data:
            return None

        params = [
            ToolParameter(
                name=p["name"],
                type=p.get("type", "string"),
                description=p.get("description", ""),
                required=p.get("required", True),
                enum=p.get("enum"),
            )
            for p in data.get("parameters", [])
            if isinstance(p, dict) and "name" in p
        ]

        return ToolDefinition(
            name=data["name"],
            description=data.get("description", ""),
            parameters=params,
            metadata=data.get("metadata", {}),
        )
