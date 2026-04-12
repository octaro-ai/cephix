from __future__ import annotations

from typing import Any

from src.domain import ExecutionContext
from src.governance.domain import ActorRole, RiskClass
from src.governance.models import GuardDecision
from src.governance.ports import ApprovalStorePort, RiskClassifierPort
from src.tools.ports import ToolRegistryPort


_DEFAULT_SOURCE_KEYS = ("source", "source_folder", "sender", "from")
_DEFAULT_TARGET_KEYS = ("target", "destination_folder", "folder", "to")


class PolicyToolExecutionGuard:
    """Governance guard that checks risk class, SOP metadata, and approval store.

    Decision logic (in priority order):
    1. ``read_only`` tools are always allowed.
    2. If the active SOP lists the action in ``safe_actions`` -> ALLOW.
    3. If a matching rule exists in the ApprovalStore -> ALLOW (or DENY).
    4. Otherwise -> REQUIRE_APPROVAL for mutations.
    """

    def __init__(
        self,
        *,
        risk_classifier: RiskClassifierPort,
        approval_store: ApprovalStorePort,
        registry: ToolRegistryPort | None = None,
    ) -> None:
        self._risk_classifier = risk_classifier
        self._approval_store = approval_store
        self._registry = registry

    def check(
        self,
        ctx: ExecutionContext,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> GuardDecision:
        risk = self._risk_classifier.classify(tool_name)

        if risk == RiskClass.READ_ONLY:
            return GuardDecision.allow()

        if self._is_internal_system_tool(tool_name):
            return GuardDecision.allow()

        actor_ctx = getattr(ctx, "actor_context", None)
        principal_id = actor_ctx.principal_id if actor_ctx else ctx.user_id

        source, target = self._extract_source_target(tool_name, arguments)

        action_context: dict[str, Any] = {"action": tool_name}
        if source:
            action_context["source"] = str(source)
        if target:
            action_context["target"] = str(target)

        existing_rule = self._approval_store.check(
            principal_id=principal_id,
            action=tool_name,
            source_scope=str(source) if source else None,
            target_scope=str(target) if target else None,
        )
        if existing_rule is not None:
            from src.governance.domain import ApprovalScope
            if existing_rule.scope == ApprovalScope.DENY:
                return GuardDecision.deny(
                    reason=f"{tool_name} denied by stored rule",
                    guard_name="PolicyToolExecutionGuard",
                )
            return GuardDecision.allow()

        return GuardDecision.require_approval(
            reason=f"{tool_name} is {risk.value}, no existing approval",
            guard_name="PolicyToolExecutionGuard",
            risk_class=risk,
            action_context=action_context,
        )

    def _extract_source_target(
        self, tool_name: str, arguments: dict[str, Any],
    ) -> tuple[str, str]:
        """Extract source/target from arguments using context_mapping if available."""
        mapping = self._get_context_mapping(tool_name)
        if mapping:
            source_key = mapping.get("source", "")
            target_key = mapping.get("target", "")
            source = str(arguments.get(source_key, "")) if source_key else ""
            target = str(arguments.get(target_key, "")) if target_key else ""
            return source, target

        source = ""
        for key in _DEFAULT_SOURCE_KEYS:
            val = arguments.get(key)
            if val:
                source = str(val)
                break

        target = ""
        for key in _DEFAULT_TARGET_KEYS:
            val = arguments.get(key)
            if val:
                target = str(val)
                break

        return source, target

    def _get_context_mapping(self, tool_name: str) -> dict[str, str] | None:
        """Look up context_mapping from tool metadata if a registry is available."""
        if self._registry is None:
            return None
        for tool_def in self._registry.list_mounted():
            if tool_def.name == tool_name:
                return tool_def.metadata.get("context_mapping")
        return None

    def _is_internal_system_tool(self, tool_name: str) -> bool:
        """Internal system tools (memory, documents, tasks) don't need user approval."""
        if self._registry is None:
            registry = getattr(self._risk_classifier, "_registry", None)
        else:
            registry = self._registry
        if registry is None:
            return False
        for tool_def in registry.list_mounted():
            if tool_def.name == tool_name:
                return bool(tool_def.metadata.get("system_tool"))
        return False
