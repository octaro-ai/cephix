"""``ProcessEnvCredentialStore`` -- ``os.environ`` as a credential backend.

Sits at the bottom of the default resolution chain so secrets
exported in the parent shell are still picked up without anyone
having to write a ``.env`` file. Common for CI: a workflow exposes
``OPENAI_KEY`` as a step env var, no ``.env`` needed.

Dual-use (same as :class:`~src.credentials.stores.env.EnvCredentialStore`):

- **Robot-side**: a :class:`RobotComponent` at boot level 3
  (UTILITY). ``start()`` is the boot-log marker, ``stop()`` drops
  the cached snapshot. The provider consults it at runtime.
- **Builder-side**: a plain value object during ``${KEY}``
  substitution. The snapshot is already in ``__init__`` so the
  substitution pass works without a lifecycle.

Snapshotting: by default, the store reads ``os.environ`` *eagerly*
at construction time. That keeps the substitution pass
deterministic (the builder sees a stable view) and protects the
resolver from late mutations to ``os.environ``. Pass
``snapshot=False`` for a live view; the runtime provider then
sees changes that happen after construction (rare; tests use it).
"""

from __future__ import annotations

import logging
import os

from src.components import ComponentCategory, RobotComponent
from src.credentials.ports import CredentialStorePort

logger = logging.getLogger(__name__)


class ProcessEnvCredentialStore(RobotComponent, CredentialStorePort):
    """A snapshot (or live view) of ``os.environ``.

    Constructor:

    - ``snapshot`` -- ``True`` (default): copy ``os.environ`` once
      at construction. ``False``: query ``os.environ`` on every
      lookup. The default matches the pattern of every other
      store in this subsystem (read once at construction).
    - ``name`` -- override the audit name. Default ``"process-env"``.
    """

    component_name = "process-env-credentials"
    component_category = ComponentCategory.UTILITY
    component_description = (
        "A snapshot (or live view) of os.environ as a synchronous "
        "credential backend. Default fallback so secrets exported in "
        "the parent shell are picked up without a .env file."
    )

    def __init__(
        self,
        *,
        snapshot: bool = True,
        name: str = "process-env",
    ) -> None:
        self._name = name
        self._snapshot = snapshot
        self._values: dict[str, str] | None = (
            dict(os.environ) if snapshot else None
        )

    @property
    def name(self) -> str:
        return self._name

    # ---- RobotComponent lifecycle ------------------------------------------

    async def start(self) -> None:
        mode = "snapshot" if self._snapshot else "live"
        held = len(self._values) if self._values is not None else len(os.environ)
        logger.info(
            "%s (%s) ready in %s mode, %d key(s) visible",
            type(self).__name__,
            self.instance_id,
            mode,
            held,
        )

    async def stop(self) -> None:
        if self._values is not None:
            self._values = {}

    # ---- CredentialStorePort ------------------------------------------------

    def lookup(self, key: str) -> str | None:
        if self._values is not None:
            return self._values.get(key)
        return os.environ.get(key)

    def has_key(self, key: str) -> bool:
        if self._values is not None:
            return key in self._values
        return key in os.environ
