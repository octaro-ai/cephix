"""Value types for the session store.

One dataclass: :class:`SessionMessage`. It is intentionally
structured as the Open Conversation Format ``message_envelope``
(see ``schema/ocf-v0.1.0.schema.json``), so a future export from
the working JSONL into a single ``.ocf.json`` document is a wrap
(``{ocf_version, conversation, messages}``) without touching the
field shapes.

The inner ``message`` field is the **existing**
:class:`~src.actor.llm.types.ChatMessage` -- OpenAI Chat Completions
wire shape. ``[json.loads(line)["message"] for line in jsonl]`` is
therefore directly schickbar an ``chat.completions.create()``. No
custom ``WireMessage`` type, no per-driver re-mapping.

OCF ``usage`` carries the six well-known token fields
(``input``, ``output``, ``thinking``, ``cache_read``,
``cache_write``, ``total``) plus ``additionalProperties: true``;
we use the standard names directly and stash ``cost_usd`` as an
additional property. The mapping from internal Cephix names
(``tokens_in`` / ``tokens_out`` / ``reasoning_tokens`` /
``cache_read_tokens`` / ``cache_write_tokens``) happens at the
boundary where the kernel builds the ``usage`` dict for the
assistant message.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from src.actor.llm.types import ChatMessage


_MESSAGE_ID_PREFIX = "msg_"
_MESSAGE_ID_HEX_LENGTH = 12


def new_message_id() -> str:
    """Mint a fresh message id of the OCF-friendly form ``msg_<12-hex>``."""
    return f"{_MESSAGE_ID_PREFIX}{uuid.uuid4().hex[:_MESSAGE_ID_HEX_LENGTH]}"


@dataclass(frozen=True, kw_only=True)
class SessionMessage:
    """One record persisted to the session JSONL file.

    Maps 1:1 to ``schema/ocf-v0.1.0.schema.json#/definitions/message_envelope``:

    - ``id`` -- stable identifier for this record. Convention:
      ``msg_<12-hex>``.
    - ``created_at`` -- ISO-8601 UTC timestamp string.
    - ``parent_id`` -- OCF branching marker. ``None`` for linear
      conversations.
    - ``model`` -- the model that produced ``message`` (only for
      assistant records).
    - ``duration_ms`` -- wall-clock latency of the producing call
      (only for assistant records).
    - ``usage`` -- token counts and (as an additional property)
      ``cost_usd``. ``None`` when the producer is not an LLM (user
      messages, system messages).
    - ``message`` -- the OpenAI Chat Completions message body. The
      existing :class:`~src.actor.llm.types.ChatMessage` carries
      exactly the standard ``role`` + ``content`` shape; tool
      fields will dock here when the tool layer arrives.

    The dataclass is ``frozen`` so a constructed record is
    immutable; mutations would only happen on the wire and need a
    new instance anyway.
    """

    id: str
    created_at: str
    message: ChatMessage
    parent_id: str | None = None
    model: str | None = None
    duration_ms: int | None = None
    usage: dict[str, int | float] | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SessionMessage requires a non-empty id")
        if not self.created_at:
            raise ValueError(
                "SessionMessage requires a non-empty created_at"
            )
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError(
                "SessionMessage.duration_ms must be >= 0 when set"
            )

    # ------------------------------------------------------------------
    # JSONL (de)serialisation
    # ------------------------------------------------------------------

    def to_jsonl_line(self) -> str:
        """Serialise this record to a single newline-terminated JSON line.

        Keys are written in a stable order so a textual diff between
        two store snapshots stays readable. ``None``-valued envelope
        fields are dropped to keep lines compact and match OCF's
        ``required: [id, created_at, message]`` floor.
        """
        payload: dict[str, Any] = {
            "id": self.id,
            "created_at": self.created_at,
        }
        if self.parent_id is not None:
            payload["parent_id"] = self.parent_id
        if self.model is not None:
            payload["model"] = self.model
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        if self.usage is not None:
            payload["usage"] = dict(self.usage)
        payload["message"] = asdict(self.message)
        return json.dumps(payload, ensure_ascii=False) + "\n"

    @classmethod
    def from_jsonl_line(cls, line: str) -> "SessionMessage":
        """Parse a JSONL line back into a :class:`SessionMessage`.

        Tolerates trailing whitespace / newline; raises
        :class:`ValueError` for structurally invalid input
        (missing ``id``, missing ``message`` block, ...). The
        store reader catches and skips bad lines to keep
        crash-tolerance for partial tail writes.
        """
        if not line.strip():
            raise ValueError("empty JSONL line")
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ValueError("JSONL line must decode to an object")
        msg = data.get("message")
        if not isinstance(msg, dict):
            raise ValueError("missing 'message' object")
        chat = ChatMessage(
            role=str(msg.get("role", "")),
            content=str(msg.get("content", "")),
        )
        usage = data.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("'usage' must be an object when present")
        return cls(
            id=str(data["id"]),
            created_at=str(data["created_at"]),
            parent_id=(
                str(data["parent_id"]) if data.get("parent_id") is not None else None
            ),
            model=(
                str(data["model"]) if data.get("model") is not None else None
            ),
            duration_ms=(
                int(data["duration_ms"])
                if data.get("duration_ms") is not None
                else None
            ),
            usage=dict(usage) if usage is not None else None,
            message=chat,
        )


