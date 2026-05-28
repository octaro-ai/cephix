"""``ProcessEnvCredentialStore`` -- ``os.environ`` as a credential backend.

Sits at the bottom of the default resolution chain so secrets
exported in the parent shell are still picked up without anyone
having to write a ``.env`` file. Common for CI: a workflow exposes
``OPENAI_KEY`` as a step env var, no ``.env`` needed.

Snapshotting: by default, the store reads ``os.environ`` *eagerly*
at construction time. That keeps the substitution pass
deterministic (the builder sees a stable view) and protects the
resolver from late mutations to ``os.environ``. Pass
``snapshot=False`` for a live view; the runtime provider then
sees changes that happen after construction (rare; tests use it).
"""

from __future__ import annotations

import os

from src.credentials.ports import CredentialStorePort


class ProcessEnvCredentialStore(CredentialStorePort):
    """A snapshot (or live view) of ``os.environ``.

    Constructor:

    - ``snapshot`` -- ``True`` (default): copy ``os.environ`` once
      at construction. ``False``: query ``os.environ`` on every
      lookup. The default matches the pattern of every other
      store in this subsystem (read once at construction).
    - ``name`` -- override the audit name. Default ``"process-env"``.
    """

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

    def lookup(self, key: str) -> str | None:
        if self._values is not None:
            return self._values.get(key)
        return os.environ.get(key)

    def has_key(self, key: str) -> bool:
        if self._values is not None:
            return key in self._values
        return key in os.environ
