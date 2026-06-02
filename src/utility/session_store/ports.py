"""Port for the session store.

One interface: :class:`SessionStorePort`. ``abc.ABC`` (not
``typing.Protocol``) on purpose -- consistent with the rest of the
codebase's "explicit inheritance for Dependency Inversion" style,
so a missing method is a definition-time error rather than a
runtime ``AttributeError``.

The port is intentionally minimal:

- :meth:`new_session` mints a fresh, store-unique session id.
- :meth:`open` is **lazy-create**: it returns ``True`` if the
  session was just brought into existence, ``False`` if it was
  already known. The :class:`~src.kernel.chat.ChatKernel` uses the
  return value to surface a "new conversation began" wide-event on
  the planning phase.
- :meth:`append` adds one
  :class:`~src.utility.session_store.types.SessionMessage` to the
  given session's append-only log.
- :meth:`messages` reads back the persisted records. ``limit=None``
  returns the full history; unknown / brand-new sessions return
  ``[]`` without raising so the kernel sees an empty starting
  point.
- :meth:`list_sessions` enumerates every persisted session id (for
  future ``/sessions`` listing commands).

All methods are ``async`` so concrete stores can route their IO
through an async transport layer (``FilesystemConnection``,
later S3, ...). Even the trivially-CPU ones (``new_session``)
follow the contract so callers don't have to know which methods
hit the transport and which don't.

Concurrency: ``append`` may be called concurrently for the same
session_id from one event loop; the store guarantees ordered
writes. Cross-process safety is out of scope -- one robot owns
its home, period.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.utility.session_store.types import SessionMessage, SessionSummary


class SessionStorePort(ABC):
    """Read/write surface every session store implements."""

    @abstractmethod
    async def new_session(self) -> str:
        """Mint a brand-new, store-unique session id."""

    @abstractmethod
    async def open(self, session_id: str) -> bool:
        """Make sure ``session_id`` exists in the store; lazy-create.

        Returns ``True`` if the session was just created (caller can
        treat the conversation as fresh), ``False`` if it was already
        present (caller continues an existing conversation).
        """

    @abstractmethod
    async def append(
        self, session_id: str, message: SessionMessage
    ) -> None:
        """Append one record to ``session_id``'s history."""

    @abstractmethod
    async def messages(
        self, session_id: str, limit: int | None = None
    ) -> list[SessionMessage]:
        """Return the persisted records for ``session_id``.

        ``limit=None`` returns the full history (default). A
        positive integer returns the most-recent ``limit`` records
        (in chronological order). Unknown sessions return ``[]``.
        """

    @abstractmethod
    async def list_sessions(self) -> list[SessionSummary]:
        """Return a :class:`SessionSummary` per known session.

        Ordered most-recently-active first so a UI can render a chat
        sidebar directly. Sessions with no messages yet still appear
        (empty ``created_at``/``last_activity_at``).
        """

    @abstractmethod
    async def set_title(self, session_id: str, title: str) -> None:
        """Assign (or replace) the human-friendly title of ``session_id``.

        The title is the one piece of session metadata not derivable
        from the messages, so it is persisted out-of-band. An empty
        ``title`` clears any existing title.
        """
