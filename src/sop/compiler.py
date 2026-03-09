"""SOP compilers -- transform text-based SOP descriptions into SOPDefinition DAGs.

Two compilers are provided:

* ``YAMLSOPCompiler`` -- parses raw YAML text into a SOPDefinition.
* ``MarkdownSOPCompiler`` -- parses a simplified Markdown format where each
  ``## Step`` heading becomes a node and ``->`` arrows define edges.

Markdown format example::

    # Process: Postkorb Check (v2.0)

    ## scan
    List all unread emails
    tools: mail.list

    ## read
    Read a specific email
    tools: mail.read
    skill: email-reading

    ## triage
    Flag or archive the email
    tools: mail.flag

    edges:
    scan -> read
    read -> triage
    triage -> scan [more_unread]
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from src.sop.models import SOPDefinition, SOPEdge, SOPNode


class YAMLSOPCompiler:
    """Compiles raw YAML text into a SOPDefinition."""

    def compile(self, raw_text: str) -> SOPDefinition:
        data = yaml.safe_load(raw_text)
        if not isinstance(data, dict) or "name" not in data:
            raise ValueError("Invalid YAML SOP: missing 'name' field")

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
            entry_node=data.get("entry_node", nodes[0].node_id if nodes else ""),
            nodes=nodes,
            edges=edges,
            required_skills=data.get("required_skills", []),
            required_tools=data.get("required_tools", []),
            trigger_patterns=data.get("trigger_patterns", []),
            metadata=data.get("metadata", {}),
        )


class MarkdownSOPCompiler:
    """Compiles a simplified Markdown notation into a SOPDefinition.

    Parses ``## node_id`` sections for nodes and an ``edges:`` block
    for edge definitions.
    """

    _HEADING_RE = re.compile(r"^#\s+(.+?)(?:\((.+?)\))?$")
    _NODE_RE = re.compile(r"^##\s+(\S+)$")
    _EDGE_RE = re.compile(r"^(\S+)\s*->\s*(\S+)(?:\s*\[(.+?)\])?$")

    def compile(self, raw_text: str) -> SOPDefinition:
        lines = raw_text.strip().splitlines()

        name = ""
        version = "0.1"
        nodes: list[SOPNode] = []
        edges: list[SOPEdge] = []
        all_tools: set[str] = set()
        all_skills: set[str] = set()

        current_node_id: str | None = None
        current_desc_lines: list[str] = []
        current_tools: list[str] = []
        current_skill: str | None = None
        in_edges = False

        def _flush_node() -> None:
            nonlocal current_node_id, current_desc_lines, current_tools, current_skill
            if current_node_id is not None:
                nodes.append(SOPNode(
                    node_id=current_node_id,
                    description="\n".join(current_desc_lines).strip(),
                    available_tools=current_tools,
                    skill_name=current_skill,
                ))
                all_tools.update(current_tools)
                if current_skill:
                    all_skills.add(current_skill)
            current_node_id = None
            current_desc_lines = []
            current_tools = []
            current_skill = None

        for line in lines:
            stripped = line.strip()

            if stripped.lower().startswith("edges:"):
                _flush_node()
                in_edges = True
                continue

            if in_edges:
                m = self._EDGE_RE.match(stripped)
                if m:
                    edges.append(SOPEdge(
                        from_node=m.group(1),
                        to_node=m.group(2),
                        condition=m.group(3),
                    ))
                continue

            heading = self._HEADING_RE.match(stripped)
            if heading and not stripped.startswith("##"):
                name = heading.group(1).strip()
                if heading.group(2):
                    version = heading.group(2).strip().lstrip("v")
                continue

            node_match = self._NODE_RE.match(stripped)
            if node_match:
                _flush_node()
                current_node_id = node_match.group(1)
                continue

            if current_node_id is not None:
                if stripped.startswith("tools:"):
                    current_tools = [t.strip() for t in stripped[6:].split(",") if t.strip()]
                elif stripped.startswith("skill:"):
                    current_skill = stripped[6:].strip() or None
                elif stripped:
                    current_desc_lines.append(stripped)

        _flush_node()

        return SOPDefinition(
            name=name or "unnamed",
            description="",
            version=version,
            entry_node=nodes[0].node_id if nodes else "",
            nodes=nodes,
            edges=edges,
            required_skills=sorted(all_skills),
            required_tools=sorted(all_tools),
        )
