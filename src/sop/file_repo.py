"""File-system-backed SOP repository.

Reads SOP definitions from YAML files.

Expected format::

    name: postkorb.check
    description: Check and triage incoming mail
    version: "2.0"
    entry_node: scan
    required_skills:
      - email-reading
    required_tools:
      - mail.list
      - mail.read
      - mail.flag
    trigger_patterns:
      - "check mail"
      - "postkorb"
    nodes:
      - node_id: scan
        description: List all unread emails
        available_tools: [mail.list]
      - node_id: read
        description: Read a specific email
        available_tools: [mail.read]
        skill_name: email-reading
      - node_id: triage
        description: Flag or archive the email
        available_tools: [mail.flag]
    edges:
      - from_node: scan
        to_node: read
      - from_node: read
        to_node: triage
      - from_node: triage
        to_node: scan
        condition: more_unread
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.sop.models import SOPDefinition, SOPEdge, SOPNode


class FileSOPRepository:
    """Discovers SOP definitions from YAML files on disk."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._cache: dict[str, SOPDefinition] | None = None

    def list_available(self) -> list[SOPDefinition]:
        return list(self._load_all().values())

    def get_sop(self, name: str) -> SOPDefinition | None:
        return self._load_all().get(name)

    def reload(self) -> None:
        self._cache = None

    def _load_all(self) -> dict[str, SOPDefinition]:
        if self._cache is not None:
            return self._cache

        result: dict[str, SOPDefinition] = {}
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

    @staticmethod
    def _parse_file(path: Path) -> SOPDefinition | None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or "name" not in data:
            return None

        nodes = [
            SOPNode(
                node_id=n["node_id"],
                description=n.get("description", ""),
                available_tools=n.get("available_tools", []),
                skill_name=n.get("skill_name"),
            )
            for n in data.get("nodes", [])
            if isinstance(n, dict) and "node_id" in n
        ]

        edges = [
            SOPEdge(
                from_node=e["from_node"],
                to_node=e["to_node"],
                condition=e.get("condition"),
            )
            for e in data.get("edges", [])
            if isinstance(e, dict) and "from_node" in e and "to_node" in e
        ]

        return SOPDefinition(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "0.1"),
            entry_node=data.get("entry_node", ""),
            nodes=nodes,
            edges=edges,
            required_skills=data.get("required_skills", []),
            required_tools=data.get("required_tools", []),
            trigger_patterns=data.get("trigger_patterns", []),
            metadata=data.get("metadata", {}),
        )
