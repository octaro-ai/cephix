"""``JsonlCodec`` -- one JSON record per line, NDJSON.

Pure library: takes a ``Mapping[str, Any]``, returns the string the
storage layer is supposed to append (without trailing newline -- the
storage layer adds line termination).

NDJSON properties (the reason this is the default):

- One JSON object per line, separated by ``\\n``.
- Append-only friendly: never rewrites previous records.
- Crash-safe at line granularity: a half-written line is always at
  the tail and a robust reader skips it.
- Works out of the box with ``jq``, ``rg``, ``head``, ``cat``,
  ``less``, every log shipper.

The codec is intentionally tiny so future formats (``JsonCodec``,
``MsgpackCodec``, ``ParquetCodec``) can sit next to it with the same
shape -- :meth:`encode_line` for record-oriented codecs,
:meth:`encode_block` for batch-oriented ones.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RecordCodec(Protocol):
    """Shape providers consume.

    The codec is responsible for the *wire format*; the storage
    layer below handles delivery and durability. Codecs have no
    lifecycle: they're constructed once per provider and reused.
    """

    extension: str
    """File-name suffix used by storage backends, e.g. ``".jsonl"``."""

    def encode_line(self, record: Mapping[str, Any]) -> str:
        """Render ``record`` as a single line (without trailing newline)."""


class JsonlCodec:
    """JSON Lines / NDJSON codec.

    ``ensure_ascii`` defaults to ``False`` so non-ASCII characters
    (German umlauts, emoji in user messages) round-trip cleanly.
    ``default=str`` so the codec never raises on rich types like
    ``datetime`` or ``UUID`` -- they end up as their canonical
    string representation, which is what every reader expects.
    """

    extension = ".jsonl"

    def __init__(self, *, ensure_ascii: bool = False) -> None:
        self._ensure_ascii = ensure_ascii

    def encode_line(self, record: Mapping[str, Any]) -> str:
        return json.dumps(
            dict(record),
            ensure_ascii=self._ensure_ascii,
            separators=(",", ":"),
            default=str,
        )
