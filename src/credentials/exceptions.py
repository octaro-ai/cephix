"""Exceptions raised by the credential subsystem.

Two error kinds, on purpose:

- :class:`CredentialNotFound` -- *expected* failure mode. The key
  was looked up; no store knew it. This is the fail-fast signal
  during boot when a YAML references a secret nobody can resolve.
  Carries the key, the names of the stores that were tried, and
  the requester (for audit attribution).

- :class:`CredentialStoreError` -- *unexpected* failure mode. A
  store could not even attempt the lookup: the ``.env`` file was
  malformed, the network call to a Vault backend timed out, the
  KeePass database needed an unlock pin we don't have. Wraps the
  underlying exception so callers can decide whether to retry or
  surface the problem.

Both inherit a common base :class:`CredentialError` so callers can
catch ``except CredentialError`` for the broad case while the
specific subtypes stay distinguishable.
"""

from __future__ import annotations


class CredentialError(Exception):
    """Base class for every error raised by the credential subsystem."""


class CredentialNotFound(CredentialError):
    """Raised when no store can resolve the requested key.

    The exception carries:

    - :attr:`key` -- the credential reference that failed to resolve.
    - :attr:`stores_tried` -- the names of the stores walked, in
      resolution order. Useful for audit notes and diagnostic
      messages ("set ``OPENAI_KEY`` in ``.env`` or in
      ``~/.cephix/.env``").
    - :attr:`requester` -- the component (or ``"builder"``) that
      asked for the key. Empty string when not provided.
    """

    def __init__(
        self,
        key: str,
        *,
        stores_tried: tuple[str, ...] = (),
        requester: str = "",
    ) -> None:
        self.key = key
        self.stores_tried = tuple(stores_tried)
        self.requester = requester
        if stores_tried:
            tried = ", ".join(stores_tried)
            msg = (
                f"credential {key!r} not found "
                f"(tried stores: {tried})"
            )
        else:
            msg = f"credential {key!r} not found (no stores configured)"
        if requester:
            msg = f"{msg}; requested by {requester!r}"
        super().__init__(msg)


class CredentialStoreError(CredentialError):
    """Raised when a store fails to perform a lookup.

    Distinct from :class:`CredentialNotFound`: this signals that
    the store could not even attempt the lookup (parse error,
    network failure, unauthorised access). The original exception
    is preserved on :attr:`__cause__` via ``raise ... from exc``.
    """

    def __init__(self, store_name: str, message: str) -> None:
        self.store_name = store_name
        super().__init__(f"store {store_name!r}: {message}")
