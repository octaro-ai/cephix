"""Session store: append-only OCF-compatible chat history.

Boot category: :attr:`~src.components.ComponentCategory.UTILITY`
(boot priority 5). Off-bus, persistence-backed, consumed by the
:class:`~src.kernel.chat.ChatKernel` via reference injection.

Public surface:

- :class:`SessionStorePort` -- the ABC consumers implement against.
- :class:`JsonlSessionStore` -- the default implementation, one
  ``<session_id>.jsonl`` per session under ``<workspace>/sessions/``.
- :class:`SessionMessage` -- the OCF ``message_envelope``-shaped
  record persisted to disk. The inner ``message`` field is the
  existing :class:`~src.actor.llm.types.ChatMessage` (OpenAI Chat
  Completions wire shape), so an exported JSONL line goes straight
  to ``chat.completions.create()`` with no mapping.

Working format is the "Append-only event stream" mode of the
Open Conversation Format (one JSON object per line). A future
export adapter wraps the JSONL into ``.ocf.json`` (a single
``{ocf_version, conversation, messages}`` object) without touching
the field shapes -- the structural alignment is already there.
"""

from src.utility.session_store.ports import SessionStorePort
from src.utility.session_store.store import JsonlSessionStore
from src.utility.session_store.types import SessionMessage, new_message_id

__all__ = [
    "JsonlSessionStore",
    "SessionMessage",
    "SessionStorePort",
    "new_message_id",
]
