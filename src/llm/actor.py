"""LLMActor: an :class:`LLMActorPort` backed by a provider.

Wires three things together:

- a :class:`~src.llm.ports.LLMProviderPort` that knows how to talk
  to the actual model;
- an optional :class:`~src.llm.ports.ModelCatalogPort` for spec
  validation during ``start`` (fails fast if the provider's model
  identity is unknown to the catalog);
- the curated *actor context* the kernel hands in on every call,
  shaped into a list of :class:`~src.llm.types.ChatMessage`.

Both :meth:`run` and :meth:`stream` are first-class. ``run``
delegates to :meth:`LLMProviderPort.chat`; ``stream`` to
:meth:`LLMProviderPort.stream_chat`. Because
:class:`~src.llm.providers.base.BaseLLMProvider` synthesises one
direction from the other, a streaming-only provider gets ``run``
for free, and a non-streaming provider gets ``stream`` for free.
The actor itself never assumes which one is native.

The actor stays *off the bus* (it inherits from
:class:`~src.actor.ports.ActorPort` which is plain
:class:`~src.components.RobotComponent`, not
:class:`~src.components.BusComponent`). Audit attribution is the
kernel's job: the actor packs provider, model, token counts and
cost into :attr:`ActorResponse.metadata`, and the kernel emits a
:class:`~src.bus.messages.RobotAuditNote` on the actor's behalf
during ``finalize``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from src.actor.types import ActorResponse
from src.bus.messages import ErrorInfo
from src.components import ComponentCategory
from src.llm.ports import LLMActorPort, LLMProviderPort, ModelCatalogPort
from src.llm.types import ActorChunk, ChatMessage, LLMUsage

logger = logging.getLogger(__name__)


class LLMActor(LLMActorPort):
    """Actor that consults an LLM provider for every :meth:`run` call.

    Constructor:

    - ``provider`` -- an :class:`LLMProviderPort`. Owned by the actor:
      :meth:`start` calls ``provider.open()``, :meth:`stop` calls
      ``provider.close()``.
    - ``catalog`` -- optional :class:`ModelCatalogPort`. When
      provided, :meth:`start` validates that the provider's model
      exists in the catalog. Highly recommended; without it,
      typos in the model id silently boot the actor.
    - ``default_system_prompt`` -- the system message to inject when
      the kernel hands in no ``system`` key. Default ``""`` (no
      system message). The :class:`~src.kernel.base.BaseKernel`
      never curates a system prompt, so this default is what the
      base-kernel-plus-LLMActor combination ships to the model.
    - ``component_name`` -- override the actor's bus identity (audit
      attribution). Default ``"llm"``.

    Constructed responses always carry rich metadata on
    :attr:`ActorResponse.metadata`:

    - ``provider`` -- e.g. ``"openai"``
    - ``model_id`` -- e.g. ``"gpt-5"``
    - ``tokens_in`` / ``tokens_out`` / ``cost_usd``
    - ``finish_reason`` -- ``"stop"``, ``"length"``, ...
    - ``request_id`` -- provider-side request id when the provider
      surfaces it
    - ``extras`` -- pass-through of provider-specific fields the
      audit trail might want (system_fingerprint, cached_tokens, ...)

    The base kernel merges this metadata into ``ctx.phase_details``
    in ``act``, so it shows up on the next
    :class:`~src.bus.messages.KernelPhase` event.
    """

    component_name = "llm"
    component_category = ComponentCategory.ACTOR
    component_description = (
        "LLM actor: turns the curated actor context into a model "
        "call. Streaming-aware (via stream()) and run() returns a "
        "complete reply. Provider-agnostic; the actual model client "
        "is injected as an LLMProviderPort."
    )

    def __init__(
        self,
        *,
        provider: LLMProviderPort,
        catalog: ModelCatalogPort | None = None,
        default_system_prompt: str = "",
        component_name: str | None = None,
    ) -> None:
        self._provider = provider
        self._catalog = catalog
        self._default_system_prompt = default_system_prompt
        # Pinning a per-instance name lets two LLMActors coexist in
        # the same robot (e.g. "llm.fast", "llm.deep") without
        # subclassing. The default lives on the class so registry
        # introspection works.
        if component_name is not None:
            self.component_name = component_name

    # ---- LLMActorPort identity --------------------------------------------

    @property
    def model_id(self) -> str:
        return self._provider.model_id

    @property
    def provider(self) -> str:
        return self._provider.provider

    def count_tokens(self, text: str) -> int:
        return self._provider.count_tokens(text)

    # ---- Lifecycle --------------------------------------------------------

    async def start(self) -> None:
        await self._provider.open()
        if self._catalog is not None:
            spec = self._catalog.lookup(self.model_id, self.provider)
            if spec is None:
                # Validation failure short-circuits start. The robot's
                # owner-pattern surfaces this as a failed
                # ``ComponentLifecycle(phase="failure")``.
                raise LookupError(
                    f"LLMActor: model {self.provider}/{self.model_id} not "
                    f"found in catalog. Either register it in the catalog "
                    f"or remove the catalog injection if you accept "
                    f"unverified models."
                )
            logger.info(
                "LLMActor %s online: %s/%s, ctx=%d, max_out=%d",
                self.component_name,
                self.provider,
                self.model_id,
                spec.context_window_tokens,
                spec.max_output_tokens,
            )
        else:
            logger.info(
                "LLMActor %s online: %s/%s (no catalog validation)",
                self.component_name,
                self.provider,
                self.model_id,
            )

    async def stop(self) -> None:
        await self._provider.close()

    # ---- ActorPort.run / LLMActorPort.stream ------------------------------

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        messages = self._build_messages(actor_context)
        if not messages:
            return ActorResponse(
                message=None,
                status="error",
                error=ErrorInfo(
                    code="actor.context.empty",
                    message="actor_context did not contain a user message",
                ),
                metadata=self._identity_metadata(),
            )

        try:
            reply = await self._provider.chat(
                messages,
                max_output_tokens=actor_context.get("max_output_tokens"),
                temperature=actor_context.get("temperature"),
            )
        except Exception as exc:  # noqa: BLE001 -- provider failure
            return ActorResponse(
                message=None,
                status="error",
                error=ErrorInfo(
                    code="provider.error",
                    message=str(exc),
                    details={"exception_type": type(exc).__name__},
                ),
                metadata=self._identity_metadata(),
            )

        return ActorResponse(
            message=reply.text,
            status="ok",
            metadata=self._reply_metadata(reply.usage, reply.finish_reason, reply.request_id, reply.extras),
        )

    async def stream(
        self, actor_context: dict[str, Any]
    ) -> AsyncIterator[ActorChunk]:
        messages = self._build_messages(actor_context)
        if not messages:
            yield ActorChunk(
                delta="",
                final=True,
                response=ActorResponse(
                    message=None,
                    status="error",
                    error=ErrorInfo(
                        code="actor.context.empty",
                        message="actor_context did not contain a user message",
                    ),
                    metadata=self._identity_metadata(),
                ),
            )
            return

        text_parts: list[str] = []
        finish_reason: str | None = None
        usage: LLMUsage | None = None
        provider_extras: dict[str, Any] = {}

        try:
            async for delta in self._provider.stream_chat(
                messages,
                max_output_tokens=actor_context.get("max_output_tokens"),
                temperature=actor_context.get("temperature"),
            ):
                if delta.text:
                    text_parts.append(delta.text)
                    yield ActorChunk(delta=delta.text, final=False)
                if delta.finish_reason is not None:
                    finish_reason = delta.finish_reason
                if delta.usage is not None:
                    usage = delta.usage
                if delta.extras:
                    provider_extras.update(delta.extras)
        except Exception as exc:  # noqa: BLE001 -- provider failure
            yield ActorChunk(
                delta="",
                final=True,
                response=ActorResponse(
                    message="".join(text_parts) or None,
                    status="error",
                    error=ErrorInfo(
                        code="provider.error",
                        message=str(exc),
                        details={"exception_type": type(exc).__name__},
                    ),
                    metadata=self._identity_metadata(),
                ),
            )
            return

        yield ActorChunk(
            delta="",
            final=True,
            response=ActorResponse(
                message="".join(text_parts) or None,
                status="ok",
                metadata=self._reply_metadata(
                    usage or LLMUsage(),
                    finish_reason or "stop",
                    "",
                    provider_extras,
                ),
            ),
        )

    # ---- Context shaping --------------------------------------------------

    def _build_messages(
        self, actor_context: dict[str, Any]
    ) -> list[ChatMessage]:
        """Curate ``actor_context`` into a list of :class:`ChatMessage`.

        Accepted keys (in priority order):

        - ``messages`` -- already-shaped list of
          :class:`ChatMessage` (or dicts with ``role`` / ``content``).
          Used verbatim. The kernel's job is to honour this if it
          already speaks LLM-native; the BaseKernel never produces
          this shape.
        - ``system`` -- system prompt to prepend. Falls back to
          :attr:`_default_system_prompt`.
        - ``history`` -- prior turns as a list of dicts /
          :class:`ChatMessage`.
        - ``message`` or ``input.message`` -- the user message. The
          BaseKernel produces ``input.message``; ad-hoc callers
          often use the flat ``message`` shape.
        """
        # 1. Already shaped: pass through.
        explicit = actor_context.get("messages")
        if isinstance(explicit, list) and explicit:
            return [
                m if isinstance(m, ChatMessage) else ChatMessage(**m)
                for m in explicit
            ]

        out: list[ChatMessage] = []
        system = actor_context.get("system") or self._default_system_prompt
        if system:
            out.append(ChatMessage(role="system", content=system))

        history = actor_context.get("history")
        if isinstance(history, list):
            for entry in history:
                if isinstance(entry, ChatMessage):
                    out.append(entry)
                elif isinstance(entry, dict):
                    role = entry.get("role")
                    content = entry.get("content")
                    if isinstance(role, str) and isinstance(content, str):
                        out.append(ChatMessage(role=role, content=content))

        user_text = self._extract_user_message(actor_context)
        if user_text:
            out.append(ChatMessage(role="user", content=user_text))
        return out

    @staticmethod
    def _extract_user_message(actor_context: dict[str, Any]) -> str:
        text = actor_context.get("message")
        if isinstance(text, str):
            return text
        nested = actor_context.get("input")
        if isinstance(nested, dict):
            inner = nested.get("message")
            if isinstance(inner, str):
                return inner
        return ""

    # ---- Metadata helpers -------------------------------------------------

    def _identity_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
        }

    def _reply_metadata(
        self,
        usage: LLMUsage,
        finish_reason: str,
        request_id: str,
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "provider": self.provider,
            "model_id": self.model_id,
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "cost_usd": usage.cost_usd,
            "finish_reason": finish_reason,
        }
        if request_id:
            metadata["request_id"] = request_id
        if extras:
            metadata["provider_extras"] = dict(extras)
        return metadata
