from __future__ import annotations

from typing import Any

from src.domain import RobotEvent
from src.governance.domain import ActorContext, ActorRole


class ConfigBasedActorResolver:
    """Resolves the actor context from a static configuration.

    The principal_id comes from the robot's configuration.  Known sender IDs
    matching the principal are tagged as ``principal``; senders listed in
    ``delegates`` become ``delegate``; everything else is ``counterparty``.
    System events (no sender) are tagged ``system``.
    """

    def __init__(
        self,
        *,
        principal_id: str | None = None,
        principal_ids: set[str] | None = None,
        delegates: set[str] | None = None,
        delegate_ids: set[str] | None = None,
        operator_ids: set[str] | None = None,
    ) -> None:
        all_principals = set(principal_ids or set())
        if principal_id:
            all_principals.add(principal_id)
        self._principal_ids = all_principals
        self._default_principal = principal_id or (next(iter(all_principals)) if all_principals else "owner")
        self._delegates = (delegates or set()) | (delegate_ids or set())
        self._operator_ids = operator_ids or set()

    def resolve(self, event: RobotEvent) -> ActorContext:
        sender = event.sender_id or ""
        authority = self._default_principal

        if not sender or event.event_type.startswith(("cron.", "system.", "heartbeat.")):
            return ActorContext(
                actor_id="system",
                actor_role=ActorRole.SYSTEM,
                principal_id=authority,
                approval_authority_id=authority,
            )

        if sender in self._principal_ids:
            return ActorContext(
                actor_id=sender,
                actor_role=ActorRole.PRINCIPAL,
                principal_id=sender,
                approval_authority_id=sender,
            )

        if sender in self._operator_ids:
            return ActorContext(
                actor_id=sender,
                actor_role=ActorRole.OPERATOR,
                principal_id=authority,
                approval_authority_id=authority,
            )

        if sender in self._delegates:
            return ActorContext(
                actor_id=sender,
                actor_role=ActorRole.DELEGATE,
                principal_id=authority,
                approval_authority_id=authority,
            )

        return ActorContext(
            actor_id=sender,
            actor_role=ActorRole.COUNTERPARTY,
            principal_id=authority,
            approval_authority_id=authority,
        )
