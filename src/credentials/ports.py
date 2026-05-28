"""Ports for the credential subsystem.

Two abstract base classes that define the contract every credential
backend and broker must satisfy. Implementations subclass them
explicitly: ``class EnvCredentialStore(CredentialStorePort): ...``.
The explicit inheritance makes the dependency between consumer
and interface visible at the class declaration site (SOLID's
Dependency Inversion Principle: depend on the abstraction, not
the concrete class).

We use :class:`abc.ABC` instead of :class:`typing.Protocol` here
on purpose:

- Protocol is *structural* typing: any class with the right shape
  satisfies the contract whether it knows about the protocol or
  not. That's powerful, but in our codebase the contract has a
  designated owner (this module) and concrete implementations
  should declare the dependency explicitly. ``isinstance`` checks
  also become unambiguous (no ``runtime_checkable`` machinery).
- ABC's :func:`abc.abstractmethod` makes "you forgot to implement
  ``lookup``" a *construction-time* error instead of a runtime
  surprise.
- Adding a new method to the interface forces concrete classes
  to opt in (or fail fast). Protocol would silently accept the
  old shape.

Two ports:

- :class:`CredentialStorePort` -- a synchronous credential lookup
  backend. Stores are *value objects*, not
  :class:`~src.components.RobotComponent` instances. The builder
  constructs them once at boot and hands them to the
  :class:`~src.credentials.provider.CredentialProvider`.

- :class:`CredentialProviderPort` -- the consumer-facing port.
  Components hold one of these via constructor injection and call
  :meth:`resolve` (async, runtime) or :meth:`resolve_sync` (sync,
  builder substitution / startup hooks). Both raise
  :class:`~src.credentials.exceptions.CredentialNotFound` if no
  store has the key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class CredentialStorePort(ABC):
    """Abstract base class for credential lookup backends.

    Stores are tiny: a name, a sync :meth:`lookup`, and a
    :meth:`has_key` peek used for diagnostics. The provider
    iterates stores in construction order and returns the first
    hit. Subclasses are expected to implement all three abstracts.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable, human-readable identifier used in audit notes.

        Convention: backend kind plus a discriminator, e.g.
        ``"env:robot"``, ``"env:cephix-home"``, ``"process-env"``,
        ``"vault:cephix"``. The provider records this verbatim
        in :class:`~src.bus.messages.RobotAuditNote.details` so
        the audit log can answer "which store served this key?".
        """

    @abstractmethod
    def lookup(self, key: str) -> str | None:
        """Return the value for ``key`` or ``None`` if not held.

        Implementations should not raise on the ordinary "not
        held" outcome -- that is the dominant case and the
        provider relies on ``None`` to walk to the next store.
        A store may raise
        :class:`~src.credentials.exceptions.CredentialStoreError`
        when the backend itself fails (parse error, IO failure,
        auth failure); the provider treats that as a hard error.
        """

    @abstractmethod
    def has_key(self, key: str) -> bool:
        """Cheap existence check used by diagnostics.

        Default expectation: ``has_key(k) == (lookup(k) is not None)``.
        Provided as a separate method so a backend that can answer
        existence cheaply (without unsealing the value) can do so.
        """


class CredentialProviderPort(ABC):
    """Abstract base class for the consumer-facing credential broker.

    Two methods, intentionally similar but not identical:

    - :meth:`resolve_sync` -- synchronous. Used by the builder
      during the YAML substitution pass (no event loop yet) and
      by component ``start()`` hooks that want a secret eagerly
      without going async.
    - :meth:`resolve` -- async. Used by runtime callers (an LLM
      driver mid-request, a future tool reaching for its API key,
      a future channel fetching an auth token).

    Both raise :class:`~src.credentials.exceptions.CredentialNotFound`
    when no store has the key. Both emit a
    :class:`~src.bus.messages.RobotAuditNote` for every resolve
    attempt -- successful or not -- *unless* the provider has
    not yet been attached to a bus (boot-time substitution path).

    The audit note never carries the resolved value. It carries
    the key, the store that served it (or "not_found"), and the
    requester. The value flows over the heap, not over the bus.
    """

    @abstractmethod
    def resolve_sync(self, key: str, *, requester: str = "") -> str:
        """Resolve ``key`` synchronously. Raises ``CredentialNotFound``."""

    @abstractmethod
    async def resolve(self, key: str, *, requester: str = "") -> str:
        """Resolve ``key`` asynchronously. Raises ``CredentialNotFound``."""

    @abstractmethod
    def has_key(self, key: str) -> bool:
        """``True`` if any configured store holds ``key``."""
