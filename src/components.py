"""Self-describing component metadata.

Components that should be assembled from configuration carry three
class-level attributes:

- ``component_type``: registry key, must be unique within a category
- ``component_category``: bus / kernel / channel / ...
- ``component_description``: human-readable one-liner used by the
  onboarding wizard

The :class:`RobotComponent` mixin formalises that contract. It is
*orthogonal* to the existing :class:`BusComponent` / :class:`KernelPort`
/ :class:`ChannelPort` ``Protocol``s in :mod:`src.bus.ports`,
:mod:`src.kernel.ports` and :mod:`src.channels.ports`: the protocols
describe runtime behaviour (start, stop, ...), this mixin describes
self-identification for the registry and wizard.

Pydantic spec models are deliberately not used yet -- ``inspect.signature``
on the constructor covers the current set of parameters and lets us
ship without an extra dependency. When a use-case (wizard schema
reflection, JSON-Schema export) actually demands it, a ``Spec`` class
attribute can be added without changing the registry contract.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar


class ComponentCategory(str, Enum):
    """Coarse role buckets used by the registry and the wizard."""

    BUS = "bus"
    KERNEL = "kernel"
    CHANNEL = "channel"
    # Future categories: AUDIT, GOVERNANCE, ACTOR, TOOL, ...


class RobotComponent:
    """Mixin marking a class as a configurable component of a robot.

    Subclasses must define :attr:`component_type` and
    :attr:`component_category` as class-level attributes.

    :attr:`component_description` is shown by the onboarding wizard.

    :attr:`component_wizard_fields` is the *allow-list* of constructor
    parameters that the wizard prompts the user for. Any other
    constructor parameter is treated as plumbing/wiring and uses its
    default. ``None`` means the wizard falls back to "ask for every
    parameter" (safe default for external components that don't opt
    in). An empty tuple means "ask nothing" (all defaults).
    """

    component_type: ClassVar[str]
    component_category: ClassVar[ComponentCategory]
    component_description: ClassVar[str] = ""
    component_wizard_fields: ClassVar[tuple[str, ...] | None] = None
