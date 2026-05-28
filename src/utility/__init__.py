"""Utility components: off-bus shared services.

The ``utility`` package collects components that boot at
:attr:`~src.components.ComponentCategory.UTILITY` (boot priority 5).
They are plain :class:`~src.components.RobotComponent` instances, not
bus participants, and are typically held by reference by exactly one
or two consumers (a kernel, an actor) that look them up via a port.

Shipping with cephix today:

- :mod:`src.utility.model_catalog` -- :class:`ModelCatalog`,
  read-side view of model specs and pricing for the LLM stack.
"""
