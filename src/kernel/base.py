"""Base kernel: a directly-usable five-phase run loop.

The :class:`BaseKernel` is the cephix equivalent of an init-style
kernel: a complete, working orchestrator with sensible defaults for
every phase, that another kernel can specialize by overriding the
phase methods rather than the loop.

What the base kernel does out of the box:

1. **Observe** -- store the incoming :class:`RobotInput` on the run
   context. No history, no memory.
2. **Plan** -- assemble a minimal, neutral *actor context* the actor
   can consume: the input message, principal, run id and the raw input
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
4. **Finalize** -- pull the response message from
   ``actor_response.message`` into ``ctx.output_message`` and copy
   the structured ``payload`` into ``ctx.output_payload``. Failure
   responses (``status="error"``) propagate as :class:`RuntimeError`
   carrying the actor's :class:`ErrorInfo`.
5. **Respond** -- publish a :class:`RobotOutput` on the configured
   output topic with the reply message. A future tool-execution
   layer would override this to publish a :class:`ComponentRequest`
   instead when the actor returned a tool intent.

Telemetry: on every phase completion (and on the final ``done``) the
kernel publishes a :class:`KernelPhase` event on
:data:`KERNEL_PHASE_TOPIC`. Telemetry sinks pick these up alongside
the rest of the bus traffic. The event carries the inherited
:class:`Failable` ``status`` plus, on failure, a structured
:class:`ErrorInfo` -- queryable via ``rg '"status":"error"'`` across
every kernel without per-kernel knowledge.

User-facing error reporting: the base kernel does **not**
automatically publish a :class:`RobotOutput` (``status="error"``) for
the user when a run fails. Whether and how the failure surfaces to
the outside world is a kernel-design decision; a recovery path may
exist, the failure may be silently retried, or the kernel may publish
multiple structured outputs. Specializing kernels that want a default
"sorry, that did not work" message must publish a :class:`RobotOutput`
themselves, typically inside an overridden phase method or a
``__init__``-time decision tree.

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
from typing import Any, Literal

from src.actor.ports import ActorPort
from src.bus.messages import (
    INPUT_TOPIC,
    KERNEL_PHASE_TOPIC,
    OUTPUT_TOPIC,
    ComponentInfo,
    ErrorInfo,
    KernelPhase,
    MountEvent,
    RobotEvent,
    RobotInput,
    RobotOutput,
    component_mount_topic,
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


def _classify_exception(exc: BaseException) -> str:
    """Map a Python exception to a canonical :attr:`ErrorInfo.code`.

    Walks ``__cause__`` so wrappers like
    ``raise RuntimeError("actor X timed out") from asyncio.TimeoutError(...)``
    are still classified as ``"timeout"`` and not as a generic
    internal error.

    Conservative defaults; subclasses can override
    :meth:`BaseKernel._classify_phase_error` if they need richer
    mappings (e.g., distinguishing rate-limit errors from generic
    LLM-side internals).
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, asyncio.TimeoutError):
            return "timeout"
        if isinstance(current, asyncio.CancelledError):
            return "cancelled"
        current = current.__cause__
    return "internal"


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

        # Surface the actor-mount fact in two places so log readers,
        # telemetry sinks and future control-plane UIs all see which
        # actor sits in this kernel without having to cross-reference
        # boot order:
        # - a stable INFO log line ("X (id) injected into Y (id)")
        #   for the operator skimming the console;
        # - a :class:`MountEvent` on the kernel's mount topic so
        #   subscribers can react to swaps and so the wide-event log
        #   has a canonical "this kernel is wired to that actor"
        #   record. The retained snapshot lives on
        #   :class:`ComponentLifecycle` (later); ``MountEvent`` is
        #   the fire-and-forget transition stream.
        actor_class = type(self._actor).__name__
        kernel_class = type(self).__name__
        actor_id = getattr(self._actor, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s)",
            actor_class,
            actor_id,
            kernel_class,
            self.instance_id,
        )
        await self._publish_actor_mount(phase="mounted")

        # Self-announce: the kernel is online and ready. Carries its
        # ``provides_commands`` via ``component_info()`` so the
        # CapabilityCollector can add them to the manifest. A
        # subclass that wires more on top (e.g. ChatKernel's command
        # handlers) does so synchronously after ``super().start()``,
        # before the event loop hands the announce to the collector,
        # so no command can arrive ahead of its handler.
        await self.announce_lifecycle(bus, "ready")

    async def stop(self) -> None:
        # Tear down in reverse of ``start``: ``start`` mounted the
        # actor before announcing ``ready``, so ``stop`` unmounts the
        # actor before announcing ``shutdown``. The mount/unmount
        # pair frames the actor's lifetime; the ready/shutdown pair
        # frames the kernel's. The bus is still alive at this point
        # (it boots first / stops last), so both events go out.
        await self._publish_actor_mount(phase="unmounted")
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        self._bus = None

    async def _publish_actor_mount(
        self, *, phase: Literal["mounted", "unmounted"]
    ) -> None:
        """Emit a :class:`MountEvent` for the actor slot on this kernel.

        Called on both :meth:`start` (``phase="mounted"``) and
        :meth:`stop` (``phase="unmounted"``) so subscribers see a
        clean transition stream. The owner identifier mirrors the
        kernel's :attr:`RobotEvent.source` convention
        (``"kernel.<name>"``); the snapshot carries the actor's
        instance identity in :attr:`ComponentInfo.metadata` so a
        subscriber can correlate this event with later phase events
        from the actor without keeping a side index.

        Best-effort: a publish failure is logged but never aborts
        the lifecycle. Actor mount is incidental telemetry, not a
        precondition for the kernel running.
        """
        bus = self._bus
        if bus is None:
            return
        owner = f"kernel.{self.component_name}"
        actor_name = getattr(self._actor, "component_name", type(self._actor).__name__)
        actor_metadata: dict[str, Any] = {
            "kernel_instance_id": self.instance_id,
        }
        # ``ActorPort`` extends ``RobotComponent``, so a real actor
        # always exposes ``instance_id``. ``getattr`` keeps the
        # publish-side defensive against test doubles that fake an
        # ``ActorPort`` interface without the full
        # ``RobotComponent`` contract.
        actor_instance_id = getattr(self._actor, "instance_id", "")
        if actor_instance_id:
            actor_metadata["instance_id"] = actor_instance_id
        mounted_info: ComponentInfo | None
        if phase == "mounted":
            mounted_info = ComponentInfo(
                category=ComponentCategory.ACTOR.value,
                name=actor_name,
                description=getattr(self._actor, "component_description", ""),
                metadata=actor_metadata,
            )
        else:
            mounted_info = None
        try:
            await bus.publish(
                MountEvent(
                    topic=component_mount_topic(self.component_name),
                    principal=f"kernel:{self.component_name}",
                    source=owner,
                    source_id=self.instance_id,
                    run_id="",
                    phase=phase,
                    owner=owner,
                    slot="actor",
                    mounted=mounted_info,
                )
            )
        except Exception:
            logger.exception(
                "%s: failed to emit actor mount event (phase=%s)",
                type(self).__name__,
                phase,
            )

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

        Failure path: a phase that raises produces its own phase
        event with ``status="error"`` (carried in
        ``ctx.phase_error``) and the loop terminates the run with a
        ``done`` event likewise carrying ``status="error"``. Two
        events per failure: one says "this phase broke", the
        trailing ``done`` says "the run as a whole ended in error".
        """
        ctx = RunContext(run_id=event.run_id or _new_correlation_id())
        ctx.input = event

        run_failure: ErrorInfo | None = None
        try:
            await self._do_phase(ctx, RunPhase.OBSERVING, self.observe, event)
            await self._do_phase(ctx, RunPhase.PLANNING, self.plan)
            await self._do_phase(ctx, RunPhase.ACTING, self.act)
            await self._do_phase(ctx, RunPhase.FINALIZING, self.finalize)
            await self._do_phase(ctx, RunPhase.RESPONDING, self.respond)
        except Exception:
            # ``_do_phase`` already emitted the failing-phase event
            # and stashed the :class:`ErrorInfo` on the sticky
            # ``run_error`` slot before clearing per-phase scratch.
            run_failure = ctx.run_error
            logger.exception(
                "%s: run %s failed", type(self).__name__, ctx.run_id
            )

        ctx.phase = RunPhase.DONE
        ctx.ended_at = _utcnow()
        if run_failure is not None:
            ctx.phase_status = "error"
            ctx.phase_error = run_failure
        else:
            ctx.phase_status = "ok"
            ctx.phase_error = None
        self._populate_done_details(ctx, failed=run_failure is not None)
        await self._emit_phase(ctx)

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
        details / status / error / message are cleared between
        phases so each event reflects exactly its own phase.

        Phase-event semantics: post-completion. A ``KernelPhase``
        event is the *report* of a finished phase, not an entry
        marker -- analogous to an OpenTelemetry span emitted at
        completion. Practical consequence: in a run where ``respond``
        publishes a :class:`RobotOutput`, the output appears on the
        bus *before* the corresponding ``responding`` phase event
        that documents it. The phase event is the closing report,
        not the announcement.

        On exception: the phase event is still emitted, with
        ``status="error"`` and the structured :class:`ErrorInfo`
        attached, before the exception is re-raised. This keeps the
        wide-event log honest -- every started phase produces a
        single event that says how it ended.
        """
        ctx.phase = phase
        ctx.phase_started_at = _utcnow()
        try:
            await work(ctx, *args)
        except Exception as exc:
            self._record_phase_failure(ctx, exc)
            await self._emit_phase(ctx)
            # Preserve whatever the phase managed to write before it
            # blew up so the trailing ``done`` wide-event still
            # shows the partial state at the moment of failure.
            self._absorb_phase_into_run(ctx)
            # Promote phase_error to the sticky run-level slot so
            # the trailing ``done`` event in ``_run`` can mirror it,
            # then clear the per-phase scratch.
            ctx.run_error = ctx.phase_error
            self._reset_phase_slots(ctx)
            raise
        if ctx.phase_started_at is not None:
            duration_ms = (
                _utcnow() - ctx.phase_started_at
            ).total_seconds() * 1000.0
            ctx.phase_details["phase_duration_ms"] = round(duration_ms, 3)
        await self._emit_phase(ctx)
        self._absorb_phase_into_run(ctx)
        self._reset_phase_slots(ctx)

    @staticmethod
    def _absorb_phase_into_run(ctx: "RunContext") -> None:
        """Fold the just-finished phase's details into ``run_details``.

        Renames ``phase_duration_ms`` to ``<phase>_duration_ms`` so
        each phase's timing survives the merge; every other key is
        last-write-wins (today no two phases share a key).
        """
        phase_name = ctx.phase.value
        for key, value in ctx.phase_details.items():
            if key == "phase_duration_ms":
                ctx.run_details[f"{phase_name}_duration_ms"] = value
            else:
                ctx.run_details[key] = value

    def _record_phase_failure(
        self, ctx: "RunContext", exc: BaseException
    ) -> None:
        """Populate ``ctx`` with status / error info for a failed phase.

        Captures the duration so the phase event still reports how
        long the failing work ran, classifies the exception into a
        canonical error code, and stashes a structured
        :class:`ErrorInfo` on ``ctx.phase_error``. Subsequent
        ``_emit_phase`` reads these slots.
        """
        if ctx.phase_started_at is not None:
            duration_ms = (
                _utcnow() - ctx.phase_started_at
            ).total_seconds() * 1000.0
            ctx.phase_details["phase_duration_ms"] = round(duration_ms, 3)
        code = self._classify_phase_error(exc)
        ctx.phase_status = "error"
        ctx.phase_error = ErrorInfo(
            code=code,
            message=f"{type(exc).__name__}: {exc}",
            details={
                "failed_phase": ctx.phase.value,
                "exception_type": type(exc).__name__,
            },
        )

    def _classify_phase_error(self, exc: BaseException) -> str:
        """Hook for subclasses to map exceptions to canonical codes.

        Default delegates to :func:`_classify_exception`. Override to
        recognize provider-specific failure modes (rate limits,
        moderation rejections, ...) and emit them as namespaced
        codes (``actor.openai.rate_limited``, ...).
        """
        return _classify_exception(exc)

    @staticmethod
    def _reset_phase_slots(ctx: "RunContext") -> None:
        ctx.phase_details = {}
        ctx.phase_message = ""
        ctx.phase_status = "ok"
        ctx.phase_error = None

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
        ctx.phase_details["input_message_len"] = len(ctx.input.message or "")
        ctx.phase_details["input_payload_keys"] = sorted(ctx.input.payload)

    async def plan(self, ctx: RunContext) -> None:
        """Build the neutral actor context the actor will consume.

        Default: a minimal envelope with the input message, principal,
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
                "message": ctx.input.message,
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
        - ``actor_status`` -- ``"ok"`` / ``"error"`` from the
          actor response.
        - any keys the actor placed into ``response.metadata`` are
          merged in too (LLM actors are expected to surface
          ``model``, ``tokens_in``, ``tokens_out``, ``cost_usd``).

        Failure responses (``status="error"``) and timeouts surface
        as :class:`RuntimeError` so the loop's error path emits an
        ``acting`` phase event with ``status="error"`` and a
        canonical :class:`ErrorInfo`.
        """
        assert ctx.input is not None
        actor_name = getattr(self._actor, "component_name", type(self._actor).__name__)
        actor_instance_id = getattr(self._actor, "instance_id", "")
        ctx.phase_details["actor_name"] = actor_name
        if actor_instance_id:
            ctx.phase_details["actor_instance_id"] = actor_instance_id
        started = _utcnow()
        try:
            response = await asyncio.wait_for(
                self._actor.run(dict(ctx.actor_context)),
                timeout=self._actor_timeout,
            )
        except asyncio.TimeoutError as exc:
            actor_ms = (_utcnow() - started).total_seconds() * 1000.0
            ctx.phase_details["actor_duration_ms"] = round(actor_ms, 3)
            ctx.phase_details["actor_status"] = "error"
            ctx.total_actor_ms += actor_ms
            raise RuntimeError(
                f"actor {actor_name} timed out after "
                f"{self._actor_timeout}s"
            ) from exc
        actor_ms = (_utcnow() - started).total_seconds() * 1000.0
        ctx.phase_details["actor_duration_ms"] = round(actor_ms, 3)
        ctx.phase_details["actor_status"] = response.status
        if response.metadata:
            ctx.phase_details.update(dict(response.metadata))
        ctx.actor_response = response
        ctx.total_actor_ms += actor_ms
        if response.status == "error":
            err = response.error
            label = err.message if err is not None else "<no message>"
            raise RuntimeError(
                f"actor {actor_name} returned an error: {label}"
            )

    async def finalize(self, ctx: RunContext) -> None:
        """Translate the actor response into output fields.

        Default: pull ``response.message`` into ``ctx.output_message``
        and copy ``response.payload`` into ``ctx.output_payload``. A
        specializing kernel would inspect the payload here for tool
        intents and route accordingly; the wide-event ``path`` field
        documents which branch was taken (``"output"`` /
        ``"tool"`` / ``"empty"`` for the base kernel: always
        ``"output"`` or ``"empty"``).
        """
        if ctx.actor_response is None:
            raise RuntimeError("finalize called without an actor response")
        ctx.output_message = ctx.actor_response.message
        ctx.output_payload = dict(ctx.actor_response.payload)
        ctx.phase_details["path"] = (
            "output" if (ctx.output_message or ctx.output_payload) else "empty"
        )
        ctx.phase_details["is_tool_call"] = False

    async def respond(self, ctx: RunContext) -> None:
        """Publish whatever the run produced onto the bus.

        Default: a single :class:`RobotOutput` on
        ``output_topic`` with ``status="ok"``. Specializing kernels
        can publish :class:`ComponentRequest` instead (or in
        addition) when the run produced a tool intent, or set
        ``status="error"`` on the :class:`RobotOutput` if they want
        the channel to render the message as a failure.
        """
        bus = self._require_bus()
        assert ctx.input is not None
        output = RobotOutput(
            topic=self._output_topic,
            principal=ctx.input.principal,
            source=f"kernel.{self.component_name}",
            source_id=self.instance_id,
            run_id=ctx.run_id,
            message=ctx.output_message,
            payload=dict(ctx.output_payload),
        )
        await bus.publish(output)
        ctx.phase_details["output_event_id"] = output.event_id
        ctx.phase_details["output_message_len"] = len(ctx.output_message or "")

    # ---- helpers ----------------------------------------------------------

    def _require_bus(self) -> BusPort:
        if self._bus is None:
            raise RuntimeError(
                f"{type(self).__name__} is not started; bus is not available"
            )
        return self._bus

    def _populate_done_details(
        self, ctx: RunContext, *, failed: bool
    ) -> None:
        """Aggregate the run's wide-event fields onto the ``done`` phase.

        The ``done`` event is the canonical wide-event row for the
        whole run -- the row a query like "show me all failed runs
        in the last hour" hits on. It is built by starting from the
        cross-phase ``run_details`` accumulator (every key each
        phase wrote, with per-phase ``phase_duration_ms`` already
        renamed to ``<phase>_duration_ms``) and layering the
        run-level totals on top. A subscriber that listens only on
        ``done`` therefore sees the entire run in a single event;
        the per-phase events remain available for granular views.
        """
        run_ms = (
            (ctx.ended_at - ctx.started_at).total_seconds() * 1000.0
            if ctx.ended_at is not None
            else 0.0
        )
        if failed:
            outcome = "error"
        elif ctx.output_message is None and not ctx.output_payload:
            outcome = "empty"
        else:
            outcome = "ok"
        # Start from everything every phase produced...
        ctx.phase_details = dict(ctx.run_details)
        # ...then layer the run-level totals on top so they win over
        # any (unlikely) collision and sit at the end of the dict.
        ctx.phase_details["run_duration_ms"] = round(run_ms, 3)
        ctx.phase_details["iterations"] = ctx.iteration + 1
        ctx.phase_details["total_actor_ms"] = round(ctx.total_actor_ms, 3)
        ctx.phase_details["outcome"] = outcome

    async def _emit_phase(self, ctx: RunContext) -> None:
        """Publish a :class:`KernelPhase` event for the current phase.

        Best-effort: failures to publish phase telemetry never abort
        the run -- they only log so the operator notices.

        Reads ``ctx.phase_status`` / ``ctx.phase_error`` /
        ``ctx.phase_message`` / ``ctx.phase_details`` for the
        Failable-and-Wide-Event payload.
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
                    source_id=self.instance_id,
                    run_id=ctx.run_id,
                    phase=ctx.phase.value,
                    iteration=ctx.iteration,
                    kernel=self.component_name,
                    status=ctx.phase_status,  # type: ignore[arg-type]
                    error=ctx.phase_error,
                    message=ctx.phase_message,
                    details=dict(ctx.phase_details),
                )
            )
        except Exception:
            logger.exception(
                "%s: failed to emit phase %s",
                type(self).__name__,
                ctx.phase.value,
            )
