"""File-system-backed skill repository.

Reads skill definitions from YAML files or SKILL.md markdown files.

YAML format::

    name: email-reading
    description: Email triage and reading skill
    version: "1.0"
    instructions: |
      You are an expert at reading and categorising emails.
      Always summarise the key points and flag urgent items.
    required_tools:
      - mail.list
      - mail.read
    metadata:
      tags: [email, communication]

SKILL.md format::

    ---
    name: email-reading
    version: "1.0"
    required_tools: [mail.list, mail.read]
    ---
    # Email Reading Skill

    You are an expert at reading and categorising emails. ...
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.skills.models import SkillDefinition


class FileSkillRepository:
    """Discovers skill definitions from files on disk."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._cache: dict[str, SkillDefinition] | None = None

    def list_available(self) -> list[SkillDefinition]:
        return list(self._load_all().values())

    def get_skill(self, name: str) -> SkillDefinition | None:
        return self._load_all().get(name)

    def reload(self) -> None:
        self._cache = None

    def _load_all(self) -> dict[str, SkillDefinition]:
        if self._cache is not None:
            return self._cache

        result: dict[str, SkillDefinition] = {}
        if not self._directory.exists():
            self._cache = result
            return result

        for path in sorted(self._directory.glob("*.yaml")):
            defn = self._parse_yaml(path)
            if defn is not None:
                result[defn.name] = defn
        for path in sorted(self._directory.glob("*.yml")):
            defn = self._parse_yaml(path)
            if defn is not None and defn.name not in result:
                result[defn.name] = defn
        for path in sorted(self._directory.rglob("SKILL.md")):
            defn = self._parse_skill_md(path)
            if defn is not None and defn.name not in result:
                result[defn.name] = defn

        self._cache = result
        return result

    @staticmethod
    def _parse_yaml(path: Path) -> SkillDefinition | None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict) or "name" not in data:
            return None
        return SkillDefinition(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "0.1"),
            instructions=data.get("instructions", ""),
            required_tools=data.get("required_tools", []),
            metadata=data.get("metadata", {}),
        )

    @staticmethod
    def _parse_skill_md(path: Path) -> SkillDefinition | None:
        """Parse SKILL.md format: YAML frontmatter + markdown body as instructions."""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None

        if not text.startswith("---"):
            return None

        end = text.find("---", 3)
        if end == -1:
            return None

        try:
            frontmatter = yaml.safe_load(text[3:end])
        except Exception:
            return None

        if not isinstance(frontmatter, dict) or "name" not in frontmatter:
            return None

        instructions = text[end + 3:].strip()

        return SkillDefinition(
            name=frontmatter["name"],
            description=frontmatter.get("description", ""),
            version=frontmatter.get("version", "0.1"),
            instructions=instructions,
            required_tools=frontmatter.get("required_tools", []),
            metadata=frontmatter.get("metadata", {}),
        )
