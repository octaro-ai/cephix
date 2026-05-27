"""Base kernel: a directly-usable five-phase run loop.

The :class:`BaseKernel` is the cephix equivalent of an init-style
kernel: a complete, working orchestrator with sensible defaults for
every phase, that another kernel can specialize by overriding the
phase methods rather than the loop.

What the base kernel does out of the box:

1. **Observe** -- store the incoming :class:`RobotInput` on the run
   context. No history, no memory.
2. **Plan** -- assemble a minimal, neutral *actor context* the actor
   can consume: the input text, principal, run id and the raw input
   payload. No history, no memory, no tool schemas. A
   ``ChatKernel`` is expected to override this and populate the
   context with a session history; an ``LLMKernel`` extends it with
   tool schemas; etc.
3. **Act** -- call :meth:`ActorPort.run` on the configured actor
   directly (no bus traffic) and store the :class:`ActorResponse`
   on the run context. The actor is whoever was injected into the
   kernel during construction; subprocess actors, HTTP-driven
   actors, scripted actors and human-in-the-loop actors all look
   the same from here.
4. **Finalize** -- pull the response text from
   ``actor_response.text`` into ``ctx.output_text`` and copy the
   structured ``payload`` into ``ctx.output_payload``. Failure
   responses (``ok=False``) propagate as :class:`RuntimeError`.
5. **Respond** -- publish a :class:`RobotOutput` on the configured
   output topic with the reply text. A future tool-execution layer
   would override this to publish a :class:`ComponentRequest` instead
   when the actor returned a tool intent.

Telemetry: on every phase entry (and on ``done`` / ``error``) the
kernel publishes a :class:`KernelPhase` event on
:data:`KERNEL_PHASE_TOPIC`. Telemetry sinks pick these up alongside
the rest of the bus traffic.

Audit: deliberately *not* used per phase -- audit is for curated
notes about consequential actions (LLM calls, external requests),
not for the bookkeeping of an internal pipeline.

The base kernel cooperates with :class:`Robot`: it inherits from
:class:`BusComponent` (via :class:`KernelPort`) so the robot starts
it after the bus is online and stops it during shutdown. While
running it owns exactly one input subscription. The actor it talks
to is a separate :class:`RobotComponent` the robot also bootstraps,
but the actor never touches the bus -- the kernel is the only
mediator.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from src.actor.ports import ActorPort
from src.bus.messages import (
    INPUT_TOPIC,
    KERNEL_PHASE_TOPIC,
    OUTPUT_TOPIC,
    KernelPhase,
    RobotEvent,
    RobotInput,
    RobotOutput,
)
from src.bus.ports import BusPort, Subscription
from src.components import ComponentCategory
from src.kernel.ports import KernelPort
from src.kernel.run import RunContext, RunPhase

logger = logging.getLogger(__name__)


def _new_correlation_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class BaseKernel(KernelPort):
    """Default kernel: a working five-phase run loop with neutral defaults.

    Construction is bus-free: the kernel only learns about the bus
    when the robot calls :meth:`start`. The *actor* however is
    injected at construction time, because kernel and actor are a
    fixed pair for the lifetime of the kernel; the builder wires
    them up.

    Configuration knobs that matter for users:

    - ``input_topic`` -- where channels publish :class:`RobotInput`.
    - ``output_topic`` -- where channels listen for
      :class:`RobotOutput`.
    - ``actor_timeout`` -- seconds to wait for the actor's
      :meth:`ActorPort.run` call. ``None`` waits forever; the
      default of 30 seconds keeps a misbehaving actor from
      wedging the kernel indefinitely.

    Subclassing: override one or more phase methods. The loop, the
    phase events, the error handling and the input subscription are
    all owned by the base class.
    """

    component_name = "base"
    component_category = ComponentCategory.KERNEL
    component_description = (
        "Five-phase actor-driven kernel. Calls the configured actor "
        "for every input and emits the reply as a RobotOutput."
    )

    def __init__(
        self,
        *,
        actor: ActorPort,
        input_topic: str = INPUT_TOPIC,
        output_topic: str = OUTPUT_TOPIC,
        actor_timeout: float | None = 30.0,
    ) -> None:
        if not isinstance(actor, ActorPort):
            raise TypeError(
                f"{type(self).__name__} requires an ActorPort instance; "
                f"got {type(actor).__name__}"
            )
        self._actor = actor
        self._input_topic = input_topic
        self._output_topic = output_topic
        self._actor_timeout = actor_timeout

        self._bus: BusPort | None = None
        self._subscription: Subscription | None = None

    # ---- BusComponent lifecycle -------------------------------------------

    async def start(self, bus: BusPort) -> None:
        if self._bus is not None:
            return
        self._bus = bus
        self._subscription = bus.subscribe(self._input_topic, self._on_input)

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        self._bus = None

    # ---- run loop ---------------------------------------------------------

    async def _on_input(self, event: RobotEvent) -> None:
        """Bus-handler for the input topic.

        Filters non-:class:`RobotInput` events (the robot publishes
        plenty of other lifecycle traffic) and otherwise hands off
        to :meth:`_run`.
        """
        if not isinstance(event, RobotInput):
            return
        await self._run(event)

    async def _run(self, event: RobotInput) -> None:
        """Walk one input through the five phases.

        Owns the phase transitions, the per-phase :class:`KernelPhase`
        emission, and the error path. Phase methods themselves stay
        purely about their own work and may write analytics into
        ``ctx.phase_details``; the loop emits one wide-event per
        phase using whatever the phase wrote there, then clears it.
        """
        ctx = RunContext(run_id=event.run_id or _new_correlation_id())
        ctx.input = event

        try:
            await self._do_phase(ctx, RunPhase.OBSERVING, self.observe, event)
            await self._do_phase(ctx, RunPhase.PLANNING, self.plan)
            await self._do_phase(ctx, RunPhase.ACTING, self.act)
            await self._do_phase(ctx, RunPhase.FINALIZING, self.finalize)
            await self._do_phase(ctx, RunPhase.RESPONDING, self.respond)

            ctx.phase = RunPhase.DONE
            ctx.ended_at = _utcnow()
            self._populate_done_details(ctx)
            await self._emit_phase(ctx)
        except Exception as exc:
            ctx.phase = RunPhase.ERROR
            ctx.error = f"{type(exc).__name__}: {exc}"
            ctx.ended_at = _utcnow()
            ctx.phase_details["error_type"] = type(exc).__name__
            await self._emit_phase(ctx)
            logger.exception(
                "%s: run %s failed",
                type(self).__name__,
                ctx.run_id,
            )

    async def _do_phase(
        self,
        ctx: "RunContext",
        phase: RunPhase,
        work: Any,
        *args: Any,
    ) -> None:
        """Run one phase: time it, capture details, emit a wide event.

        ``work`` is the phase method (``observe``, ``plan``, ...) and
        ``*args`` are the extra positional arguments it expects on
        top of ``ctx``. The phase method may write structured fields
        into ``ctx.phase_details`` while it runs; the loop adds
        ``phase_duration_ms`` automatically and emits a
        :class:`KernelPhase` event after the work returns. Phase
        details are cleared between phases so each event reflects
        exactly its own phase.

        Phase-event semantics: post-completion. A ``KernelPhase``
        event is the *report* of a finished phase, not an entry
        marker -- analogous to an OpenTelemetry span emitted at
        completion. This is intentional: the wide-event analytics
        (``phase_duration_ms``, ``actor_duration_ms``,
        ``output_text_len``, ...) only exist *after* the work ran.
        Practical consequence to remember: in a run where ``respond``
        publishes a :class:`RobotOutput`, the output appears on the
        bus *before* the corresponding ``responding`` phase event
        that documents it. The phase event is the closing report,
        not the announcement. A hung phase produces no event for
        itself; the previous phase's event is the last one in the
        log, and the absence of the next is the hang signal.
        """
        ctx.phase = phase
        ctx.phase_started_at = _utcnow()
        await work(ctx, *args)
        if ctx.phase_started_at is not None:
            duration_ms = (
                _utcnow() - ctx.phase_started_at
            ).total_seconds() * 1000.0
            ctx.phase_details["phase_duration_ms"] = round(duration_ms, 3)
        await self._emit_phase(ctx)
        ctx.phase_details = {}

    # ---- phase methods ----------------------------------------------------

    async def observe(self, ctx: RunContext, event: RobotInput) -> None:
        """Bring the input into the run context.

        Default: ``ctx.input`` is already populated by the loop;
        we just attach a few wide-event fields about the input so
        the ``observing`` event is queryable. Override when a kernel
        must consult external state on input arrival (route the
        input, look up a session, refuse on policy grounds, ...).
        """
        del event  # already on ctx.input
        assert ctx.input is not None
        ctx.phase_details["input_event_id"] = ctx.input.event_id
        ctx.phase_details["input_text_len"] = len(ctx.input.text or "")
        ctx.phase_details["input_payload_keys"] = sorted(ctx.input.payload)

    async def plan(self, ctx: RunContext) -> None:
        """Build the neutral actor context the actor will consume.

        Default: a minimal envelope with the input text, principal,
        run id and the raw payload. No history, no memory, no tool
        schemas -- those are the responsibility of specializing
        kernels (a ``ChatKernel`` adds session history, an
        ``LLMKernel`` adds tool schemas, etc.).

        Implementations should keep the context JSON-serialisable so
        every actor (LLM, scripted, human, subprocess, ...) can
        read it.
        """
        assert ctx.input is not None  # set by _run
        ctx.actor_context = {
            "input": {
                "text": ctx.input.text,
                "principal": ctx.input.principal,
                "run_id": ctx.input.run_id,
                "payload": dict(ctx.input.payload),
            },
        }
        ctx.phase_details["context_keys"] = sorted(ctx.actor_context)

    async def act(self, ctx: RunContext) -> None:
        """In-process call to the actor, bounded by ``actor_timeout``.

        Default: hand the curated ``actor_context`` to
        :meth:`ActorPort.run` and store the resulting
        :class:`ActorResponse` on ``ctx.actor_response``. The actor
        is not on the bus -- this is a direct method call, no
        correlation id, no topic routing.

        Wide-event fields written into ``ctx.phase_details``:

        - ``actor_name`` -- the actor's ``component_name``.
        - ``actor_duration_ms`` -- wall-clock latency of the call.
        - ``actor_ok`` -- ``True`` for a successful response.
        - ``actor_error_type`` -- present only on failure.
        - any keys the actor placed into ``response.metadata`` are
          merged in too (LLM actors are expected to surface
          ``model``, ``tokens_in``, ``tokens_out``, ``cost_usd``).

        Failure responses (``ok=False``) and timeouts surface as
        :class:`RuntimeError` so the loop's error path emits an
        ``error`` phase event with a useful label.
        """
        assert ctx.input is not None
        actor_name = getattr(self._actor, "component_name", type(self._actor).__name__)
        ctx.phase_details["actor_name"] = actor_name
        started = _utcnow()
        try:
            response = await asyncio.wait_for(
                self._actor.run(dict(ctx.actor_context)),
                timeout=self._actor_timeout,
            )
        except asyncio.TimeoutError as exc:
            ctx.phase_details["actor_ok"] = False
            ctx.phase_details["actor_error_type"] = "TimeoutError"
            actor_ms = (_utcnow() - started).total_seconds() * 1000.0
            ctx.phase_details["actor_duration_ms"] = round(actor_ms, 3)
            ctx.total_actor_ms += actor_ms
            raise RuntimeError(
                f"actor {actor_name} timed out after "
                f"{self._actor_timeout}s"
            ) from exc
        actor_ms = (_utcnow() - started).total_seconds() * 1000.0
        ctx.phase_details["actor_duration_ms"] = round(actor_ms, 3)
        ctx.phase_details["actor_ok"] = response.ok
        if response.metadata:
            ctx.phase_details.update(dict(response.metadata))
        ctx.actor_response = response
        ctx.total_actor_ms += actor_ms
        if not response.ok:
            ctx.phase_details["actor_error_type"] = "ActorError"
            raise RuntimeError(
                f"actor {actor_name} returned an error: "
                f"{response.error or '<no message>'}"
            )

    async def finalize(self, ctx: RunContext) -> None:
        """Translate the actor response into output fields.

        Default: pull ``response.text`` into ``ctx.output_text`` and
        copy ``response.payload`` into ``ctx.output_payload``. A
        specializing kernel would inspect the payload here for tool
        intents and route accordingly; the wide-event ``path`` field
        documents which branch was taken (``"output"`` /
        ``"tool"`` / ``"empty"`` for the base kernel: always
        ``"output"`` or ``"empty"``).
        """
        if ctx.actor_response is None:
            raise RuntimeError("finalize called without an actor response")
        ctx.output_text = ctx.actor_response.text
        ctx.output_payload = dict(ctx.actor_response.payload)
        ctx.phase_details["path"] = (
            "output" if (ctx.output_text or ctx.output_payload) else "empty"
        )
        ctx.phase_details["is_tool_call"] = False

    async def respond(self, ctx: RunContext) -> None:
        """Publish whatever the run produced onto the bus.

        Default: a single :class:`RobotOutput` on
        ``output_topic``. Specializing kernels can publish
        :class:`ComponentRequest` instead (or in addition) when the
        run produced a tool intent.
        """
        bus = self._require_bus()
        assert ctx.input is not None
        output = RobotOutput(
            topic=self._output_topic,
            principal=ctx.input.principal,
            source=f"kernel.{self.component_name}",
            run_id=ctx.run_id,
            text=ctx.output_text,
            payload=dict(ctx.output_payload),
        )
        await bus.publish(output)
        ctx.phase_details["output_event_id"] = output.event_id
        ctx.phase_details["output_text_len"] = len(ctx.output_text or "")

    # ---- helpers ----------------------------------------------------------

    def _require_bus(self) -> BusPort:
        if self._bus is None:
            raise RuntimeError(
                f"{type(self).__name__} is not started; bus is not available"
            )
        return self._bus

    def _populate_done_details(self, ctx: RunContext) -> None:
        """Aggregate the run's wide-event fields onto the ``done`` phase.

        The ``done`` event is the canonical wide-event row for the
        whole run -- the row a query like "show me all failed runs
        in the last hour" hits on. Carries totals that the
        per-phase events alone don't expose.
        """
        run_ms = (
            (ctx.ended_at - ctx.started_at).total_seconds() * 1000.0
            if ctx.ended_at is not None
            else 0.0
        )
        outcome = "ok"
        if ctx.output_text is None and not ctx.output_payload:
            outcome = "empty"
        ctx.phase_details["run_duration_ms"] = round(run_ms, 3)
        ctx.phase_details["iterations"] = ctx.iteration + 1
        ctx.phase_details["total_actor_ms"] = round(ctx.total_actor_ms, 3)
        ctx.phase_details["outcome"] = outcome

    async def _emit_phase(self, ctx: RunContext) -> None:
        """Publish a :class:`KernelPhase` event for the current phase.

        Best-effort: failures to publish phase telemetry never abort
        the run -- they only log so the operator notices.

        ``ctx.phase_details`` is consumed as the event's wide-event
        ``details`` dict. The loop clears it between phases so each
        event reflects exactly the phase that just finished.
        """
        bus = self._bus
        if bus is None:
            return
        principal = (
            ctx.input.principal if ctx.input is not None else "system"
        )
        try:
            await bus.publish(
                KernelPhase(
                    topic=KERNEL_PHASE_TOPIC,
                    principal=principal,
                    source=f"kernel.{self.component_name}",
                    run_id=ctx.run_id,
                    phase=ctx.phase.value,
                    iteration=ctx.iteration,
                    kernel=self.component_name,
                    error=ctx.error,
                    details=dict(ctx.phase_details),
                )
            )
        except Exception:
            logger.exception(
                "%s: failed to emit phase %s",
                type(self).__name__,
                ctx.phase.value,
            )
