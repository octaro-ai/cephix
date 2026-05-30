# 0003 - Wire-format codec is not abstracted (for now)

- Status: accepted
- Date: 2026-05-30

## Context

A "codec" pattern is a recurring, generally useful piece of architecture:
separate **what** a component stores (records, messages, documents) from
**how** those records hit the byte layer (JSONL, JSON, MsgPack, Parquet,
...). The format is injected, the storage layer stays format-agnostic.
This is the right shape when more than one format is realistic and the
choice can change per deployment.

During the persistence DAO refactor we briefly built that shape:

- `RecordCodec` Protocol (`encode_line(record) -> str`, `extension: str`)
- `JsonlCodec` as the only implementation
- `_resolve_codec(name)` in the builder
- `codec: jsonl` field in every persistence entry of `defaults.yaml`
- `FilesystemEventStreamProvider(*, connection, directory, codec)`

The two stores that produce JSONL today (`FilesystemEventStreamProvider`,
`FilesystemSessionStore`) ended up *asymmetric*: only the event-stream
provider held an injected codec; the session store hardcoded the format
via `SessionMessage.to_jsonl_line()`. The asymmetry forced a choice:
**codec everywhere or codec nowhere**.

## Decision

**Codec nowhere.** JSONL is written inline in both stores. The
`src/persistence/codec/` subpackage, the `RecordCodec` Protocol, the
`JsonlCodec` class, the `_resolve_codec` builder helper, and the `codec:`
YAML field were removed.

Reasoning:

- Every config in tree writes JSONL. No test, no robot, no caller asks
  for anything else.
- For the session store, JSONL is not even a choice -- it is part of
  the Open Conversation Format (OCF) "Append-only event stream" spec.
  An abstracted codec there would be fake flexibility: swapping it
  would break the spec, not just the wire format.
- For the event-stream provider, JSONL is a choice, but a choice that
  has only ever had one answer. The "we might need it" overhead --
  Protocol + class + resolver + YAML field -- costs every reader of
  the code today against a benefit no caller is collecting.
- `JsonlCodec.encode_line` was four `json.dumps` options
  (`ensure_ascii=False`, compact separators, `default=str`). These now
  live in a private `_encode_line()` helper next to the provider, with
  the same defaults preserved.

## Consequences

What gets easier:

- One constructor argument fewer on the provider.
- One YAML field fewer per persistence entry.
- A whole subpackage and its Protocol are gone; the persistence stack
  is three levels (`Backend` -> `Connection` -> `Provider`) rather
  than four.
- The two filesystem stores are now symmetric: both write JSONL
  inline, neither pretends to be format-agnostic.

What gets harder:

- A future need to write something other than JSONL (Parquet,
  MsgPack, an external sink) is a non-trivial refactor. We accept
  that. When it happens, it will be against a concrete use case, not
  a speculative one, and the shape of the codec layer can match the
  real requirement instead of one we guessed.

## The codec pattern itself stays valid

This ADR rejects **adopting the pattern here, now**, not the pattern in
general. The codec pattern is a good tool when:

- two or more wire formats genuinely coexist in tree;
- the choice can vary per deployment without breaking a spec the
  records implement;
- the format is decoupled enough from the storage layer that a fresh
  storage backend (DB, S3, ...) could reuse the same codec.

Re-introducing the pattern in cephix is a matter of refactoring back
in -- the historical implementation lives in git history (search for
`JsonlCodec`, `RecordCodec`, `_resolve_codec`) and can serve as a
starting point.
