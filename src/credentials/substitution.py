"""``${KEY}``-style secret substitution for YAML configuration values.

Built on top of :class:`string.Template` from the stdlib instead
of hand-rolled regex. ``Template`` already implements the exact
grammar we want (``${name}`` substitution, ``$$`` escape for a
literal dollar sign, partial substitution via
:meth:`safe_substitute`); we only narrow the identifier pattern
to UPPER_SNAKE_CASE so configuration values like ``log_level: INFO``
are not accidentally interpreted as credential references.

Why stdlib instead of regex
===========================

The substitution grammar is small but full of edge cases (escape
handling, longest-match, identifier boundaries, non-greedy braces,
balanced backslash quoting). Each of those is a potential subtle
bug in a hand-rolled regex; :class:`string.Template` has them
sorted out for two decades. The only knob we tweak is
:attr:`~string.Template.idpattern` to enforce the
``[A-Z][A-Z0-9_]*`` rule so the substitution surface stays
predictable in YAML diffs.

Grammar
=======

- ``${KEY}`` -- substitute the value of credential ``KEY``. The
  key must match ``[A-Z][A-Z0-9_]*`` (uppercase letters, digits,
  underscores; first character must be a letter).
- ``$$`` -- literal dollar sign.
- ``${unmatched_grammar}`` -- left as-is. ``Template.safe_substitute``
  with our narrowed identifier pattern simply does not recognise
  it as a placeholder.
- ``$KEY`` (no braces) -- *also* recognised by ``string.Template``
  with our identifier pattern. We accept that as a bonus syntax;
  it lets users write ``Bearer $OPENAI_KEY`` without braces when
  the surrounding text makes the boundary unambiguous.

Recursive walk
==============

:func:`resolve_secrets` walks any nested structure of ``dict``,
``list``, ``tuple`` and primitives, returning a new object with
strings substituted in place. Non-string leaves are returned
unchanged.

Fail-fast
=========

When a key reference cannot be resolved, the resolver raises
:class:`~src.credentials.exceptions.CredentialNotFound`. The
substitution path propagates that verbatim so the builder seam
aborts the whole boot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from string import Template
from typing import Any

from src.credentials.exceptions import CredentialNotFound


class _UpperCaseTemplate(Template):
    """A :class:`string.Template` that only recognises UPPER_SNAKE_CASE keys.

    Default :class:`Template` accepts the Python identifier rule
    (``[a-zA-Z_][a-zA-Z0-9_]*``). We narrow it to
    ``[A-Z][A-Z0-9_]*`` so configuration values like ``${log.level}``
    or ``${PascalCase}`` are *not* interpreted as credentials. Any
    placeholder that does not match the narrowed pattern is left
    verbatim by :meth:`safe_substitute`.
    """

    # Note: ``Template`` evaluates ``idpattern`` with the regex's
    # ``re.IGNORECASE`` flag by default. We disable that by also
    # overriding ``flags``.
    idpattern = r"[A-Z][A-Z0-9_]*"
    flags = 0  # case-sensitive


# ``Template`` exposes the recognised pattern as a class attribute
# named ``pattern`` (a compiled regex). We expose it under a more
# obvious name for callers that want to introspect the grammar.
SECRET_REFERENCE_PATTERN = _UpperCaseTemplate.pattern
"""Compiled regex that :class:`string.Template` uses internally.

Re-exported so tests and tooling can inspect the grammar without
importing ``string`` themselves.
"""

# Resolver signature: takes a key, returns the resolved string.
# Implementations raise :class:`CredentialNotFound` on failure.
ResolverFn = Callable[[str], str]


def iter_secret_references(value: Any) -> Iterator[str]:
    """Yield every ``${KEY}`` reference in ``value`` (deep walk).

    Useful for diagnostics ("which secrets does this config
    require?") and for the wizard ("warn the user about secrets
    they haven't filled in yet"). Order is depth-first traversal
    order; duplicates are *not* deduplicated -- callers can wrap
    in ``set()`` if they want unique keys.
    """
    if isinstance(value, str):
        for match in _UpperCaseTemplate.pattern.finditer(value):
            named = match.group("named") or match.group("braced")
            if named is not None:
                yield named
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from iter_secret_references(v)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_secret_references(item)


def resolve_secrets(value: Any, resolver: ResolverFn) -> Any:
    """Recursively substitute ``${KEY}`` references in ``value``.

    Walks any structure of ``dict`` / ``list`` / ``tuple`` and
    primitives. Strings have every recognised ``${KEY}`` reference
    replaced with ``resolver(KEY)``; ``$$`` collapses to a literal
    ``$``; placeholders that do not match the
    ``[A-Z][A-Z0-9_]*`` grammar are left untouched.

    Returns a *new* structure: the input is not mutated. Tuples
    stay tuples; dicts stay dicts; lists stay lists. ``None``,
    booleans, numbers and other primitives pass through verbatim.

    The ``resolver`` callable is the seam:

    - In production, the builder wires it to
      :meth:`~src.credentials.ports.CredentialProviderPort.resolve_sync`.
      A missing key surfaces as :class:`CredentialNotFound`,
      aborting the build.
    - In tests, the resolver is usually a ``dict.__getitem__`` or
      a small lambda for full control.
    """
    if isinstance(value, str):
        return _substitute_in_string(value, resolver)
    if isinstance(value, dict):
        return {k: resolve_secrets(v, resolver) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_secrets(v, resolver) for v in value]
    if isinstance(value, tuple):
        return tuple(resolve_secrets(v, resolver) for v in value)
    return value


def _substitute_in_string(text: str, resolver: ResolverFn) -> str:
    if not text:
        return text
    template = _UpperCaseTemplate(text)
    # ``safe_substitute`` leaves unmatched/unsupported placeholders
    # alone (e.g. ``${log.level}`` stays literal because our
    # idpattern doesn't accept dots). It still raises on invalid
    # syntax (a stray unescaped ``$``), so we map ``ValueError``
    # to ``CredentialNotFound`` for a uniform builder error.
    try:
        return template.safe_substitute(_LookupMapping(resolver))
    except KeyError as exc:
        raise CredentialNotFound(str(exc.args[0])) from exc


class _LookupMapping:
    """Adapter that lets a resolver callable act as a Mapping for
    :meth:`string.Template.safe_substitute`.

    ``safe_substitute`` walks the placeholder list and calls
    ``mapping[key]`` for each one. We translate that into the
    resolver and let :class:`CredentialNotFound` propagate.
    Wrapping in a class (instead of a dict) means we don't have
    to know all keys up front -- the resolver is consulted lazily.
    """

    def __init__(self, resolver: ResolverFn) -> None:
        self._resolver = resolver

    def __getitem__(self, key: str) -> str:
        try:
            return self._resolver(key)
        except CredentialNotFound:
            raise
        except Exception as exc:  # noqa: BLE001 -- resolver contract
            raise CredentialNotFound(key) from exc
