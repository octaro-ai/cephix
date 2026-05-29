"""Command specifications a component advertises.

A :class:`CommandSpec` is the static declaration "this component can
perform this command". Components list their specs in the
``provides_commands`` class attribute (see
:class:`src.components.RobotComponent`); the
:func:`src.command.wiring.wire_commands` helper turns each spec into a
bus subscription at ``start`` time, and the ``CapabilityCollector``
serializes them into the retained capability manifest so UIs know what
they may render.

The spec is intentionally minimal for Chunk 1. ``risk_class`` is carried
already (the guard / consent layer in a later chunk reads it), but the
consent / rate-limit / context-mapping fields are deliberately left out
until those layers land -- adding them now would be speculative shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskClass(str, Enum):
    """How dangerous a command/action is, for the future guard layer.

    - ``READ_ONLY`` -- no side effects worth gating (list sessions,
      read history). Never prompts.
    - ``LOW_RISK_MUTATION`` -- a reversible or low-impact change.
    - ``HIGH_RISK_MUTATION`` -- an irreversible or high-impact change.

    Chunk 1 only ever uses ``READ_ONLY`` in practice (session commands
    are user-initiated and therefore self-consented), but the full
    vocabulary is here so tool definitions and the consent layer share
    one enum.
    """

    READ_ONLY = "read_only"
    LOW_RISK_MUTATION = "low_risk_mutation"
    HIGH_RISK_MUTATION = "high_risk_mutation"


@dataclass(frozen=True)
class CommandSpec:
    """Declaration of one command a component handles.

    Fields:

    - ``action`` -- the command verb in ``<domain>.<entity>.<verb>``
      form (``"chat.session.new"``). The domain prefix keeps actions
      unambiguous across components.
    - ``handler`` -- the name of the method on the owning component
      that runs the command. Bound once at wire time via ``getattr``
      (a typo is a boot-time ``AttributeError``, never a hot-path
      surprise). The method signature is
      ``async def (self, request: CommandRequest) -> dict``.
    - ``label`` / ``description`` -- human-facing strings for UIs.
    - ``args_schema`` -- a lightweight description of the expected
      payload keys (``{"session_id": "string"}``). Not enforced in
      Chunk 1; carried for the manifest so UIs can build forms.
    - ``risk_class`` -- gating hint for the future guard layer.
    - ``discriminator`` -- optional routing suffix when several
      instances of the same component coexist (``"gmail"`` /
      ``"yahoo"``). ``None`` means the single, undiscriminated
      instance.
    - ``ui_hints`` -- free-form hints for renderers (``{"shortcut":
      "/new", "group": "session", "icon": "plus"}``).
    """

    action: str
    handler: str
    label: str = ""
    description: str = ""
    args_schema: dict[str, Any] = field(default_factory=dict)
    risk_class: RiskClass = RiskClass.READ_ONLY
    discriminator: str | None = None
    ui_hints: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("CommandSpec requires a non-empty action")
        if not self.handler:
            raise ValueError(
                f"CommandSpec({self.action!r}) requires a non-empty handler"
            )

    def manifest_entry(
        self, *, owner_component: str, owner_instance_id: str
    ) -> dict[str, Any]:
        """Serialize this spec for the capability manifest.

        ``owner_component`` / ``owner_instance_id`` identify which
        component (and which instance of it) advertises the command,
        so a UI can disambiguate two instances and so audit can
        attribute a command to its owner.
        """
        return {
            "action": self.action,
            "label": self.label,
            "description": self.description,
            "args_schema": dict(self.args_schema),
            "risk_class": self.risk_class.value,
            "discriminator": self.discriminator,
            "ui_hints": dict(self.ui_hints),
            "owner_component": owner_component,
            "owner_instance_id": owner_instance_id,
        }
