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

from src.sop.models import SOPDefinition, SOPEdge, SOPNode, SOPStep


class FileSOPRepository:
    """Discovers SOP definitions from YAML files on disk.

    Supports multiple directories — call ``add_directory`` to add
    additional sources.  SOPs from later directories do not overwrite
    earlier ones with the same name.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directories: list[Path] = [Path(directory)]
        self._cache: dict[str, SOPDefinition] | None = None

    def add_directory(self, directory: str | Path) -> None:
        """Register an additional directory to scan for SOPs."""
        self._directories.append(Path(directory))
        self._cache = None

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
        for directory in self._directories:
            self._scan_directory(directory, result)

        self._cache = result
        return result

    @staticmethod
    def _scan_directory(directory: Path, result: dict[str, SOPDefinition]) -> None:
        if not directory.exists():
            return
        for path in sorted(directory.glob("*.yaml")):
            defn = FileSOPRepository._parse_file(path)
            if defn is not None and defn.name not in result:
                result[defn.name] = defn
        for path in sorted(directory.glob("*.yml")):
            defn = FileSOPRepository._parse_file(path)
            if defn is not None and defn.name not in result:
                result[defn.name] = defn

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

        steps = [
            SOPStep(
                id=s["id"],
                name=s.get("name", s["id"]),
                instructions=s.get("instructions", ""),
            )
            for s in data.get("steps", [])
            if isinstance(s, dict) and "id" in s
        ]

        return SOPDefinition(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "0.1"),
            entry_node=data.get("entry_node", ""),
            nodes=nodes,
            edges=edges,
            steps=steps,
            required_skills=data.get("required_skills", []),
            required_tools=data.get("required_tools", []),
            trigger_patterns=data.get("trigger_patterns", []),
            learnings_document=data.get("learnings_document", ""),
            metadata=data.get("metadata", {}),
        )
