"""``CredentialProvider`` -- the BUS_UTILITY that owns secret resolution.

The provider is the runtime face of the credential subsystem. It
holds an ordered list of
:class:`~src.credentials.ports.CredentialStorePort` instances and
exposes :meth:`resolve` (async, runtime) plus :meth:`resolve_sync`
(sync, used by the builder during the YAML substitution pass).

Lifecycle
---------

The provider is a :class:`~src.components.BusComponent` of category
:attr:`~src.components.ComponentCategory.BUS_UTILITY`. Its
:meth:`start` is called by the robot *after* the bus, telemetry,
audit and the off-bus utilities are up, and *before* any
consumer (actor, kernel, channel) starts. That ordering is what
makes the runtime ``resolve`` path safe: by the time an LLM actor
or a future tool reaches into the provider, every audit subscriber
is already listening.

The builder constructs the provider eagerly -- *before* the
robot's lifecycle even runs -- because the YAML substitution pass
needs synchronous lookups. At that point there is no bus and no
audit subscriber. :meth:`resolve_sync` therefore degrades
gracefully: it logs a debug line locally, still walks the stores,
returns the value (or raises :class:`CredentialNotFound`), and
records the audit note via the bus *only* if a bus has been
attached. Once the robot runs ``start()``, the bus is wired up and
every subsequent call -- whether sync or async -- emits a real
audit note.

Audit attribution
-----------------

Every resolve attempt produces a
:class:`~src.bus.messages.RobotAuditNote` with action
``"credential.resolved"`` (success) or ``"credential.not_found"``
(failure). The note records the key, the store that served the
value (or the list of stores that were tried), and the requester.
*The note never carries the resolved value.* Telemetry and audit
sinks may persist the note safely; the secret only flows through
the heap of the requesting component.

Why both sync and async
-----------------------

- Sync :meth:`resolve_sync`: builder substitution (no event loop),
  component ``start()`` hooks that need a secret eagerly, future
  CLI subcommands that want to dump-and-resolve a config without
  starting a bot.
- Async :meth:`resolve`: runtime callers in the middle of an
  async request. The provider's contract is async-first because
  a future networked backend (Vault, web KeyStore) will require
  it; today every store is sync-backed, so the async path is a
  thin awaitable wrapper.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from src.bus.ports import BusPort
from src.components import BusComponent, ComponentCategory
from src.credentials.exceptions import CredentialNotFound
from src.credentials.ports import CredentialProviderPort, CredentialStorePort

logger = logging.getLogger(__name__)


class CredentialProvider(BusComponent, CredentialProviderPort):
    """The bus-attached credential broker.

    Constructor:

    - ``stores`` -- ordered list of
      :class:`CredentialStorePort` instances. Resolution walks
      them in order; the first hit wins. An empty list is
      accepted; every lookup then raises
      :class:`CredentialNotFound`.

    Lifecycle:

    - :meth:`start` records the bus reference. Subsequent resolves
      emit a :class:`~src.bus.messages.RobotAuditNote` per attempt.
    - :meth:`stop` drops the bus reference; resolves still work
      (the stores stay loaded) but stop emitting audit notes.
    """

    component_name = "credentials"
    component_category = ComponentCategory.BUS_UTILITY
    component_description = (
        "Credential broker. Holds an ordered list of credential "
        "stores (.env files, process env, future Vault/KeyStore "
        "backends) and serves secret lookups for the builder "
        "(boot-time substitution) and for runtime components "
        "(LLM drivers, tools, channels). Audit-trails every "
        "resolve attempt without ever putting the value on the bus."
    )

    def __init__(self, *, stores: Sequence[CredentialStorePort] = ()) -> None:
        self._stores: tuple[CredentialStorePort, ...] = tuple(stores)
        for index, store in enumerate(self._stores):
            if not isinstance(store, CredentialStorePort):
                raise TypeError(
                    f"CredentialProvider stores[{index}] does not "
                    f"implement CredentialStorePort: "
                    f"{type(store).__name__}"
                )
        self._bus: BusPort | None = None

    @property
    def store_names(self) -> tuple[str, ...]:
        """Names of the configured stores in resolution order."""
        return tuple(s.name for s in self._stores)

    # ---- Lifecycle --------------------------------------------------------

    async def start(self, bus: BusPort) -> None:  # type: ignore[override]
        """Attach the bus so later resolves can emit audit notes."""
        self._bus = bus
        await self.announce_lifecycle(bus, "ready")

    async def stop(self) -> None:
        """Detach the bus. Stores stay loaded; audits go silent."""
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        self._bus = None

    # ---- Lookup -----------------------------------------------------------

    def has_key(self, key: str) -> bool:
        """``True`` if any configured store holds ``key``."""
        return any(store.has_key(key) for store in self._stores)

    def resolve_sync(self, key: str, *, requester: str = "") -> str:
        """Synchronous resolve. Raises :class:`CredentialNotFound`.

        Used by the builder during the YAML substitution pass and
        by component ``start()`` hooks that want a secret without
        going async. When the provider has been attached to a bus
        (i.e. the robot is running), this still emits an audit
        note via :meth:`_audit_sync`; before that, it logs a
        diagnostic line locally and proceeds.
        """
        for store in self._stores:
            value = store.lookup(key)
            if value is not None:
                self._audit_sync(
                    action="credential.resolved",
                    key=key,
                    requester=requester,
                    served_by=store.name,
                )
                return value
        self._audit_sync(
            action="credential.not_found",
            key=key,
            requester=requester,
            served_by=None,
        )
        raise CredentialNotFound(
            key,
            stores_tried=self.store_names,
            requester=requester,
        )

    async def resolve(self, key: str, *, requester: str = "") -> str:
        """Asynchronous resolve. Raises :class:`CredentialNotFound`.

        The runtime path. Stores are still synchronous today, so
        the implementation matches :meth:`resolve_sync` modulo the
        audit emission, which goes through
        :meth:`~src.components.RobotComponent.publish_audit` (a
        coroutine) instead of the synchronous fallback.
        """
        for store in self._stores:
            value = store.lookup(key)
            if value is not None:
                await self._audit(
                    action="credential.resolved",
                    key=key,
                    requester=requester,
                    served_by=store.name,
                )
                return value
        await self._audit(
            action="credential.not_found",
            key=key,
            requester=requester,
            served_by=None,
        )
        raise CredentialNotFound(
            key,
            stores_tried=self.store_names,
            requester=requester,
        )

    # ---- Audit emission ---------------------------------------------------

    def _audit_sync(
        self,
        *,
        action: str,
        key: str,
        requester: str,
        served_by: str | None,
    ) -> None:
        """Best-effort synchronous audit emission.

        Boot-time path: when no bus is attached yet, log a debug
        line and return. Runtime path: schedule the async
        publish on the bus's loop. We deliberately do *not* block
        on the publish completion -- the caller cares about the
        value, not the audit note's wall-clock latency.
        """
        if self._bus is None:
            logger.debug(
                "credential %s %s for requester=%r (no bus yet)",
                action,
                key,
                requester or "<anonymous>",
            )
            return
        # We're inside a coroutine the caller chose to run sync.
        # Avoid spawning a task into an unrelated loop: if there's
        # a running loop, schedule a fire-and-forget; if not, fall
        # back to the debug log.
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug(
                "credential %s %s for requester=%r (no running loop)",
                action,
                key,
                requester or "<anonymous>",
            )
            return
        loop.create_task(
            self._audit(
                action=action,
                key=key,
                requester=requester,
                served_by=served_by,
            )
        )

    async def _audit(
        self,
        *,
        action: str,
        key: str,
        requester: str,
        served_by: str | None,
    ) -> None:
        if self._bus is None:
            return
        details: dict[str, Any] = {"key": key}
        if requester:
            details["requester"] = requester
        if served_by is not None:
            details["served_by"] = served_by
        else:
            details["stores_tried"] = list(self.store_names)
        await self.publish_audit(self._bus, action=action, details=details)
