"""``ChatKernel`` -- persistent-history chat orchestrator.

Extends :class:`~src.kernel.base.BaseKernel` by overriding three
phases:

- :meth:`plan` resolves the session id from the incoming payload,
  lazy-creates the session in the
  :class:`~src.utility.session_store.ports.SessionStorePort`, loads
  the full history, asks the
  :class:`~src.utility.firmware_store.ports.FirmwareStorePort` for
  the system prompt, asks the
  :class:`~src.utility.model_catalog.ports.ModelCatalogPort` for
  the model spec (context window, max output tokens) and assembles
  the actor context the LLM actor will consume.
- :meth:`act` lets the base class run the actor, then computes the
  per-call ``cost_usd`` via the catalog's pricing -- the kernel,
  not the actor, owns cost knowledge. The fill ratio against the
  model's context window is also computed here so CLI stats can
  pick it up without re-deriving anything.
- :meth:`finalize` persists the user and assistant messages as
  OCF-shaped :class:`~src.utility.session_store.types.SessionMessage`
  records, mapping internal Cephix token names onto the OCF
  ``usage`` field vocabulary.

Aktor-Anforderung: the kernel requires a
:class:`~src.actor.llm.ports.LLMActorPort` so it has guaranteed
``model_id`` / ``provider`` properties and the cache/reasoning
token counts the catalog needs. Construction fails fast if the
passed actor lacks the port.

The base kernel's loop, error path, phase events and lifecycle
remain untouched -- this is the canonical extension model the
``BaseKernel`` documentation invites.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from src.actor.llm.ports import LLMActorPort
from src.actor.llm.types import ChatMessage
from src.bus.messages import CommandRequest
from src.command import CommandSpec, wire_commands
from src.components import ComponentCategory
from src.kernel.base import BaseKernel
from src.kernel.run import RunContext
from src.utility.firmware_store.ports import FirmwareStorePort
from src.utility.model_catalog.ports import ModelCatalogPort
from src.utility.session_store.ports import SessionStorePort
from src.utility.session_store.types import SessionMessage, new_message_id

if TYPE_CHECKING:
    from src.bus.ports import BusPort, Subscription

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """ISO-8601 UTC timestamp string, suitable for OCF ``created_at``."""
    return datetime.now(UTC).isoformat()


class ChatKernel(BaseKernel):
    """Persistent-history chat kernel.

    Constructor (in addition to :class:`BaseKernel`):

    - ``firmware`` -- :class:`FirmwareStorePort` providing the
      system prompt assembled from Markdown files.
    - ``sessions`` -- :class:`SessionStorePort` providing
      per-session append-only history.
    - ``model_catalog`` -- :class:`ModelCatalogPort` providing
      :class:`~src.utility.model_catalog.types.ModelSpec` (context
      window, max output tokens) and
      :class:`~src.utility.model_catalog.types.ModelPricing`
      (cost-per-token).

    Aktor: must implement :class:`LLMActorPort`. Construction with
    a non-LLM actor (e.g. :class:`~src.actor.echo.EchoActor`)
    raises :class:`TypeError` immediately so misconfiguration fails
    at build time, not on the first input.
    """

    component_name = "chat"
    component_category = ComponentCategory.KERNEL
    component_description = (
        "Persistent-history chat kernel. Assembles system prompt "
        "from firmware, replays session history, computes per-call "
        "cost via the model catalog, persists the round-trip as "
        "OCF-shaped JSONL."
    )

    provides_commands = (
        CommandSpec(
            action="chat.session.new",
            handler="cmd_session_new",
            label="New chat",
            description="Start a fresh conversation session.",
            ui_hints={"shortcut": "/new", "group": "session"},
        ),
        CommandSpec(
            action="chat.session.list",
            handler="cmd_session_list",
            label="Sessions",
            description="List existing conversation sessions.",
            ui_hints={"shortcut": "/sessions", "group": "session"},
        ),
        CommandSpec(
            action="chat.session.open",
            handler="cmd_session_open",
            label="Open chat",
            description="Open an existing session and load its history.",
            args_schema={"session_id": "string"},
            ui_hints={"shortcut": "/open", "group": "session"},
        ),
        CommandSpec(
            action="chat.session.rename",
            handler="cmd_session_rename",
            label="Rename chat",
            description="Set a human-friendly title for a session.",
            args_schema={"session_id": "string", "title": "string"},
            ui_hints={"shortcut": "/rename", "group": "session"},
        ),
    )

    def __init__(
        self,
        *,
        actor: LLMActorPort,
        firmware: FirmwareStorePort,
        sessions: SessionStorePort,
        model_catalog: ModelCatalogPort,
        **kernel_kwargs,
    ) -> None:
        if not isinstance(actor, LLMActorPort):
            raise TypeError(
                f"ChatKernel requires an LLMActorPort actor (the "
                "system prompt + history flow needs guaranteed "
                "model_id/provider/token-counts); got "
                f"{type(actor).__name__}"
            )
        super().__init__(actor=actor, **kernel_kwargs)
        self._firmware = firmware
        self._sessions = sessions
        self._model_catalog = model_catalog
        self._command_subs: list[Subscription] = []

    # ------------------------------------------------------------------
    # Lifecycle: wire the session commands on top of the base loop
    # ------------------------------------------------------------------

    async def start(self, bus: "BusPort") -> None:
        await super().start(bus)
        self._command_subs = wire_commands(self, bus)

    async def stop(self) -> None:
        for sub in self._command_subs:
            try:
                await sub.unsubscribe()
            except Exception:
                logger.exception("ChatKernel: failed to unsubscribe a command")
        self._command_subs = []
        await super().stop()

    # ------------------------------------------------------------------
    # Command handlers (deterministic, no actor/LLM involvement)
    # ------------------------------------------------------------------

    async def cmd_session_new(self, request: CommandRequest) -> dict:
        """Create a fresh session and return its id."""
        session_id = await self._sessions.new_session()
        await self._sessions.open(session_id)
        return {"session_id": session_id}

    async def cmd_session_list(self, request: CommandRequest) -> dict:
        """Return a summary per known session (most recent first)."""
        sessions = await self._sessions.list_sessions()
        return {"sessions": [asdict(s) for s in sessions]}

    async def cmd_session_open(self, request: CommandRequest) -> dict:
        """Open a session (lazy-create) and return its history."""
        session_id = str(request.payload.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("chat.session.open requires a 'session_id'")
        created = await self._sessions.open(session_id)
        history = await self._sessions.messages(session_id, limit=None)
        return {
            "session_id": session_id,
            "created": created,
            "messages": [self._serialize_message(m) for m in history],
        }

    async def cmd_session_rename(self, request: CommandRequest) -> dict:
        """Assign a human-friendly title to a session."""
        session_id = str(request.payload.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("chat.session.rename requires a 'session_id'")
        title = str(request.payload.get("title") or "")
        await self._sessions.set_title(session_id, title)
        return {"session_id": session_id, "title": title}

    @staticmethod
    def _serialize_message(message: SessionMessage) -> dict:
        """Render a stored message for transport to a channel/UI."""
        return {
            "id": message.id,
            "created_at": message.created_at,
            "role": message.message.role,
            "content": message.message.content,
            "model": message.model,
        }

    # ------------------------------------------------------------------
    # Phase overrides
    # ------------------------------------------------------------------

    async def plan(self, ctx: RunContext) -> None:
        """Resolve the session, load history + firmware, shape the actor context.

        Side effects worth knowing about:

        - The session is **lazy-created** in the store on first
          mention of an unknown id. The "fresh conversation" fact
          is surfaced via :attr:`RunContext.phase_message` and the
          structured ``session_new`` flag in
          :attr:`RunContext.phase_details`, so the planning
          :class:`~src.bus.messages.KernelPhase` event carries it
          out to the wide-event log.
        - The history list is loaded **fully** (no
          ``history_window``). Context-aware reduction (compacting,
          dreaming) is a future iteration; today we trust the
          model's context window plus an explicit ``max_output_tokens``
          from the spec.
        """
        assert ctx.input is not None
        session_id, created = await self._resolve_session_id(ctx)
        actor = self._llm_actor()
        spec = self._model_catalog.lookup_spec(actor.model_id, actor.provider)
        history = await self._sessions.messages(session_id, limit=None)
        system_prompt = self._firmware.system_prompt()

        wire_messages: list[dict[str, str]] = [
            {"role": m.message.role, "content": m.message.content}
            for m in history
        ]
        wire_messages.append(
            {"role": "user", "content": ctx.input.message or ""}
        )

        ctx.actor_context = {
            "session_id": session_id,
            "system_prompt": system_prompt,
            "messages": wire_messages,
            "max_output_tokens": (
                spec.max_output_tokens if spec is not None else None
            ),
        }
        ctx.phase_details["session_id"] = session_id
        ctx.phase_details["session_new"] = created
        ctx.phase_details["history_messages"] = len(history)
        ctx.phase_details["firmware_documents"] = list(
            self._firmware.documents().keys()
        )
        if created:
            ctx.phase_message = f"new session {session_id}"
        if spec is not None:
            ctx.phase_details["context_window_tokens"] = (
                spec.context_window_tokens
            )
            # ``phase_details`` is wiped between phases; stash the
            # window on ``ctx.metadata`` (the cross-phase scratchpad)
            # so ``act`` can compute the fill ratio without re-asking
            # the catalog.
            ctx.metadata["context_window_tokens"] = (
                spec.context_window_tokens
            )

    async def act(self, ctx: RunContext) -> None:
        """Run the actor (base class), then compute cost from the catalog.

        The actor is a dumb driver and reports only token counts;
        per-token pricing lives on the
        :class:`~src.utility.model_catalog.types.ModelPricing` row
        the catalog hands us. That keeps the actor SDK-thin and
        makes cost an architectural property of the kernel: a
        future limit monitor or usage-stats subscriber only needs
        to listen for ``KernelPhase`` events, not invent its own
        pricing book.
        """
        await super().act(ctx)
        actor = self._llm_actor()
        pricing = self._model_catalog.lookup_pricing(
            actor.model_id, actor.provider
        )
        tokens_in = int(ctx.phase_details.get("tokens_in", 0) or 0)
        tokens_out = int(ctx.phase_details.get("tokens_out", 0) or 0)
        cost_usd = 0.0
        if pricing is not None:
            cost_usd = (
                tokens_in * pricing.input_cost_per_token
                + tokens_out * pricing.output_cost_per_token
            )
            # Cache-Preise leben heute in pricing.extras
            # (``cache_read_cost_per_token`` / ``cache_write_cost_per_token``)
            # -- defensiv lesen, fehlt der Eintrag ergibt sich 0.
            cache_read = int(
                ctx.phase_details.get("cache_read_tokens", 0) or 0
            )
            cache_write = int(
                ctx.phase_details.get("cache_write_tokens", 0) or 0
            )
            extras = pricing.extras or {}
            cache_read_cost = float(
                extras.get("cache_read_cost_per_token", 0.0) or 0.0
            )
            cache_write_cost = float(
                extras.get("cache_write_cost_per_token", 0.0) or 0.0
            )
            cost_usd += (
                cache_read * cache_read_cost + cache_write * cache_write_cost
            )
        cost_usd_rounded = round(cost_usd, 6)
        ctx.phase_details["cost_usd"] = cost_usd_rounded
        # Stash for ``finalize`` -- ``phase_details`` resets between
        # phases, ``ctx.metadata`` does not.
        ctx.metadata["cost_usd"] = cost_usd_rounded

        # Pre-compute the context-window fill ratio for the CLI
        # stats consumer (chunk 2). Doing it here means the
        # wide-event log already carries it on every ``acting``
        # event; nothing else needs to recompute it later. The
        # window itself was stashed on ``ctx.metadata`` in ``plan``
        # because ``phase_details`` resets between phases.
        window = ctx.metadata.get("context_window_tokens")
        if isinstance(window, int) and window > 0:
            ctx.phase_details["context_window_tokens"] = window
            ctx.phase_details["context_fill_ratio"] = round(
                tokens_in / window, 4
            )

    async def finalize(self, ctx: RunContext) -> None:
        """Persist user + assistant turns as OCF-shaped JSONL records."""
        await super().finalize(ctx)
        assert ctx.input is not None
        session_id = ctx.actor_context.get("session_id")
        if not session_id:
            logger.warning(
                "ChatKernel.finalize: no session_id on actor_context; "
                "skipping persistence"
            )
            return

        now = _utcnow_iso()
        user_msg = SessionMessage(
            id=new_message_id(),
            created_at=now,
            message=ChatMessage(
                role="user", content=ctx.input.message or ""
            ),
        )

        actor_metadata = (
            dict(ctx.actor_response.metadata) if ctx.actor_response else {}
        )

        # ``actor_duration_ms`` is on ``phase_details`` only during the
        # ``acting`` phase; mirror it via the actor response if we
        # missed the snapshot.
        duration_ms_raw = (
            ctx.phase_details.get("actor_duration_ms")
            or actor_metadata.get("actor_duration_ms")
        )
        duration_ms: int | None = None
        if isinstance(duration_ms_raw, (int, float)):
            duration_ms = max(0, int(round(duration_ms_raw)))

        # OCF ``usage`` field names directly; ``cost_usd`` rides as
        # an additional property (``additionalProperties: true``).
        # Token counts come straight from ``actor_response.metadata``
        # (the actor's own report). ``cost_usd`` the kernel computed
        # in ``act`` above and stashed on ``ctx.metadata`` so it
        # survives the phase reset.
        usage = {
            "input": int(actor_metadata.get("tokens_in", 0) or 0),
            "output": int(actor_metadata.get("tokens_out", 0) or 0),
            "thinking": int(actor_metadata.get("reasoning_tokens", 0) or 0),
            "cache_read": int(
                actor_metadata.get("cache_read_tokens", 0) or 0
            ),
            "cache_write": int(
                actor_metadata.get("cache_write_tokens", 0) or 0
            ),
            "cost_usd": float(ctx.metadata.get("cost_usd", 0.0) or 0.0),
        }

        model_id = actor_metadata.get("model_id")
        assistant_msg = SessionMessage(
            id=new_message_id(),
            created_at=now,
            model=str(model_id) if model_id else None,
            duration_ms=duration_ms,
            usage=usage,
            message=ChatMessage(
                role="assistant", content=ctx.output_message or ""
            ),
        )
        await self._sessions.append(session_id, user_msg)
        await self._sessions.append(session_id, assistant_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_session_id(self, ctx: RunContext) -> tuple[str, bool]:
        """Return ``(session_id, created)`` for the current run.

        Priority: ``ctx.input.payload["session_id"]`` (the channel
        normally sets this). Empty / missing input -> the store
        mints a fresh id via :meth:`SessionStorePort.new_session`.
        Either way, :meth:`SessionStorePort.open` is called so the
        session exists on disk before we try to read its history;
        the boolean it returns says whether the session was just
        created (and thus has empty history).
        """
        assert ctx.input is not None
        payload = ctx.input.payload or {}
        raw = payload.get("session_id")
        sid = str(raw).strip() if isinstance(raw, str) and raw.strip() else ""
        if not sid:
            sid = await self._sessions.new_session()
        created = await self._sessions.open(sid)
        return sid, created

    def _llm_actor(self) -> LLMActorPort:
        """Return the actor cast to :class:`LLMActorPort`.

        Construction already enforced this; the helper centralises
        the cast so the call sites in :meth:`plan` and :meth:`act`
        stay readable.
        """
        actor = self._actor
        assert isinstance(actor, LLMActorPort)
        return actor
