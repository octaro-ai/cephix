"""Codec layer of the persistence stack.

Codecs translate domain records (``Mapping[str, Any]``) into line-
sized bytes/strings the storage layer below can append. Reason for
their own subpackage: format (jsonl, json, parquet, msgpack, ...) is
orthogonal to storage layer (filesystem, database, S3). A future
DB-backed provider could reuse :class:`JsonlCodec` to write JSON
into a BLOB column without sharing any code with the filesystem
stack.

Codecs are plain libraries, not :class:`RobotComponent`s -- they
have no lifecycle, no state, no DI. Each provider picks one in its
constructor.
"""

from src.persistence.codec.jsonl import JsonlCodec, RecordCodec

__all__ = [
    "JsonlCodec",
    "RecordCodec",
]
