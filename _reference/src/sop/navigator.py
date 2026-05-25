from __future__ import annotations

from src.sop.models import SOPDefinition, SOPEdge, SOPNode


class SOPNavigator:
    """Walks through a SOP decision graph at runtime.

    Tracks the current node and restricts available tools to those
    declared by the current node.  The LLM or kernel advances the
    navigator by selecting an outgoing edge.
    """

    def __init__(self, sop: SOPDefinition) -> None:
        self._sop = sop
        self._nodes: dict[str, SOPNode] = {n.node_id: n for n in sop.nodes}
        self._edges_from: dict[str, list[SOPEdge]] = {}
        for edge in sop.edges:
            self._edges_from.setdefault(edge.from_node, []).append(edge)
        self._current_node_id: str = sop.entry_node

    @property
    def current_node(self) -> SOPNode | None:
        return self._nodes.get(self._current_node_id)

    @property
    def available_tools(self) -> list[str]:
        node = self.current_node
        return list(node.available_tools) if node else []

    @property
    def current_skill(self) -> str | None:
        node = self.current_node
        return node.skill_name if node else None

    def outgoing_edges(self) -> list[SOPEdge]:
        return list(self._edges_from.get(self._current_node_id, []))

    def advance(self, condition: str | None = None) -> SOPNode | None:
        """Move to the next node along an outgoing edge.

        If *condition* is given only edges whose condition matches are
        considered.  If *condition* is ``None`` the first unconditional
        edge (or the sole outgoing edge) is taken.
        """
        edges = self.outgoing_edges()
        if not edges:
            return None

        target_edge: SOPEdge | None = None
        if condition is not None:
            for edge in edges:
                if edge.condition == condition:
                    target_edge = edge
                    break
        else:
            for edge in edges:
                if edge.condition is None:
                    target_edge = edge
                    break
            if target_edge is None and len(edges) == 1:
                target_edge = edges[0]

        if target_edge is None:
            return None

        self._current_node_id = target_edge.to_node
        return self.current_node

    def reset(self) -> None:
        self._current_node_id = self._sop.entry_node

    @property
    def is_terminal(self) -> bool:
        return len(self.outgoing_edges()) == 0
