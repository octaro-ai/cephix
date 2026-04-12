from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain import (
    ApprovalButton,
    ApprovalPrompt,
    DeliveryDirective,
    ExecutionContext,
    OutboundMessage,
    Plan,
    PlanStep,
    PlanningContext,
    ReplyTarget,
    RobotEvent,
    RobotState,
    ToolResult,
)
from src.governance.ports import ActorResolverPort, ApprovalStorePort
from src.notebooks.ports import NotebookStorePort
from src.ports import (
    BusPort,
    ContextAssemblerPort,
    MemoryPort,
    MessageDeliveryPort,
    PlannerPort,
    TelemetryPort,
)
from src.tools.ports import ToolExecutionPort
from src.utils import new_id


@dataclass
class _RunResult:
    """Intermediate result produced by the execute-plan phase."""

    final_response: str
    delivery_target: ReplyTarget | None
    tool_results: dict[str, Any]


class DigitalRobotKernel:
    def __init__(
        self,
        *,
        robot_id: str,
        default_output_target: ReplyTarget | None,
        message_delivery: MessageDeliveryPort,
        tool_executor: ToolExecutionPort,
        context_assembler: ContextAssemblerPort,
        planner: PlannerPort,
        memory: MemoryPort,
        telemetry: TelemetryPort,
        bus: BusPort,
        actor_resolver: ActorResolverPort | None = None,
        approval_store: ApprovalStorePort | None = None,
        notebook_store: NotebookStorePort | None = None,
    ) -> None:
        self.robot_id = robot_id
        self.default_output_target = default_output_target
        self.message_delivery = message_delivery
        self.tool_executor = tool_executor
        self.context_assembler = context_assembler
        self.planner = planner
        self.memory = memory
        self.telemetry = telemetry
        self.bus = bus
        self.actor_resolver = actor_resolver
        self.approval_store = approval_store
        self.notebook_store = notebook_store
        self._state = RobotState.IDLE
        self._thinking_callback: callable | None = None

    @property
    def state(self) -> RobotState:
        return self._state

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def handle_event(self, event: RobotEvent) -> None:
        user_id = event.sender_id or "system"
        ctx = ExecutionContext(
            run_id=new_id("run"),
            robot_id=self.robot_id,
            user_id=user_id,
            conversation_id=event.conversation_id,
            channel=event.source_channel,
            trace_id=new_id("trace"),
        )

        if event.event_type == "approval.decision" and self.approval_store is not None:
            self._handle_approval_decision(ctx, event)
            target = self._resolve_delivery_target(event, None)
            if target is not None:
                self.message_delivery.send(target, OutboundMessage(text="Freigabe gespeichert."))
            self._state = RobotState.DONE
            return

        try:
            planning_context = self._observe(ctx, event, user_id)
            current_plan = self._plan(ctx, event, planning_context)
            run_result = self._execute_plan(ctx, event, current_plan, planning_context)
            self._respond(ctx, event, user_id, run_result)

            self._state = RobotState.DONE
            self.telemetry.emit(
                ctx=ctx,
                event_type="run.completed",
                actor="executive.kernel",
                payload={"final_state": self._state.value},
            )

        except Exception as exc:
            self._state = RobotState.ERROR
            self.telemetry.emit(
                ctx=ctx,
                event_type="run.failed",
                actor="executive.kernel",
                payload={"final_state": self._state.value, "error": str(exc)},
            )
            # Deliver an error message back to the user so the chat doesn't hang.
            target = self._resolve_delivery_target(event, None)
            if target is not None:
                error_text = f"[Fehler] {exc.__class__.__name__}: {exc}"
                self.message_delivery.send(target, OutboundMessage(text=error_text))
            raise

    # ------------------------------------------------------------------
    # Phase: Observe – receive input & load context
    # ------------------------------------------------------------------

    def _observe(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        user_id: str,
    ) -> PlanningContext:
        self._state = RobotState.OBSERVING
        gateway_actor = f"gateway.{event.source_channel}"

        self.bus.publish(
            "event",
            "input.received",
            {"event_type": event.event_type, "text": event.text, "channel": event.source_channel},
        )
        self.telemetry.emit(
            ctx=ctx,
            event_type="input.received",
            actor=gateway_actor,
            payload={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "sender_id": event.sender_id,
                "sender_name": event.sender_name,
                "text": event.text,
                "channel": event.source_channel,
                "reply_target_channel": event.reply_target.channel if event.reply_target else None,
            },
        )

        if self.actor_resolver is not None:
            actor_ctx = self.actor_resolver.resolve(event)
            event.actor_context = actor_ctx
            ctx.actor_context = actor_ctx

        planning_context = self.context_assembler.assemble(event, user_id)
        self.telemetry.emit(
            ctx=ctx,
            event_type="memory.context_loaded",
            actor="memory.store",
            payload={
                "facts_count": len(planning_context.memory_context.get("facts", [])),
                "recent_interactions_count": len(planning_context.memory_context.get("recent_interactions", [])),
                "firmware_documents_count": len(planning_context.firmware_documents),
                "memory_documents_count": len(planning_context.memory_documents),
            },
        )

        registry = getattr(self.context_assembler, "tool_registry", None)
        if registry is not None:
            mounted = registry.list_mounted()
            self.telemetry.emit(
                ctx=ctx,
                event_type="tools.mounted",
                actor="context.assembler",
                payload={
                    "tools": [t.name for t in mounted],
                    "count": len(mounted),
                },
            )

        return planning_context

    # ------------------------------------------------------------------
    # Phase: Plan – create the initial plan
    # ------------------------------------------------------------------

    def _make_stream_callback(self, event: RobotEvent) -> callable | None:
        """Create a callback that streams response tokens directly to the client."""
        target = self._resolve_delivery_target(event, None)
        if target is None:
            return None
        def _on_token(token: str) -> None:
            self.message_delivery.send_chunk(target, token)
        return _on_token

    def _plan(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        planning_context: PlanningContext,
    ) -> Plan:
        self._state = RobotState.PLANNING

        # Stream text tokens directly to the client as they arrive.
        # If the plan turns out to include tool calls, we send a
        # chunk_clear signal so the client discards any preamble text.
        stream_cb = self._make_stream_callback(event)

        current_plan = self.planner.create_initial_plan(
            ctx, event, planning_context,
            token_callback=stream_cb,
            thinking_callback=self._thinking_callback,
        )

        # If the LLM called tools, discard any text that was streamed
        # before the tool calls (e.g. "Let me check that for you...").
        if current_plan.steps and current_plan.steps[0].kind != "finalize":
            target = self._resolve_delivery_target(event, None)
            if target is not None:
                self.message_delivery.send_chunk_clear(target)

        self._emit_thinking_telemetry(ctx)
        self.bus.publish("command", "plan.created", {"plan_id": current_plan.plan_id, "goal": current_plan.goal})
        self.telemetry.emit(
            ctx=ctx,
            event_type="plan.created",
            actor="planner.llm",
            payload={
                "plan_id": current_plan.plan_id,
                "goal": current_plan.goal,
                "steps": [self._step_summary(step) for step in current_plan.steps],
            },
        )
        return current_plan

    # ------------------------------------------------------------------
    # Phase: Execute – walk through plan steps
    # ------------------------------------------------------------------

    def _execute_plan(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        current_plan: Plan,
        planning_context: PlanningContext,
    ) -> _RunResult:
        tool_results: dict[str, Any] = {}

        while True:
            first_step = self._require_next_step(current_plan)

            if first_step.kind == "tool_call":
                # Execute ALL consecutive tool_call steps before revising.
                # LLM APIs (e.g. Anthropic) require every tool_use in an
                # assistant message to have a matching tool_result in the
                # immediately following user turn.
                current_plan = self._act_on_tool_calls(
                    ctx, event, current_plan, tool_results, planning_context,
                )
                continue

            if first_step.kind == "finalize":
                return self._finalize(ctx, event, first_step, tool_results)

            raise RuntimeError(f"Unknown step type: {first_step.kind}")

    def _act_on_tool_calls(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        current_plan: Plan,
        tool_results: dict[str, Any],
        planning_context: PlanningContext,
    ) -> Plan:
        """Execute all consecutive tool_call steps, then revise once."""
        self._state = RobotState.ACTING
        batch_results: list[ToolResult] = []

        for step in current_plan.steps:
            if step.kind != "tool_call":
                break
            tool_name = step.tool_name
            tool_arguments = step.tool_arguments or {}
            assert tool_name is not None

            self.bus.publish(
                "command",
                "tool.requested",
                {"tool": tool_name, "arguments": tool_arguments},
            )
            self.telemetry.emit(
                ctx=ctx,
                event_type="tool.requested",
                actor="executive.kernel",
                payload={
                    "tool": tool_name,
                    "arguments": tool_arguments,
                    "reason": step.reason,
                },
            )

            result = self.tool_executor.execute(ctx, tool_name, tool_arguments)
            tool_results[tool_name] = result
            batch_results.append(ToolResult(
                call_id=step.tool_call_id or step.step_id,
                tool_name=tool_name,
                result=result,
            ))

            self.telemetry.emit(
                ctx=ctx,
                event_type="tool.completed",
                actor="tool.layer",
                payload={
                    "tool": tool_name,
                    "success": True,
                    "result_count": len(result) if isinstance(result, list) else None,
                },
            )

            # Emit task checklist updates so clients can render them.
            if tool_name in ("task.plan", "task.update") and isinstance(result, dict):
                self.telemetry.emit(
                    ctx=ctx,
                    event_type="task.updated",
                    actor="tool.layer",
                    payload={
                        "items": result.get("items", []),
                        "total": result.get("total", 0),
                        "pending": result.get("pending", 0),
                        "in_progress": result.get("in_progress", 0),
                        "completed": result.get("completed", 0),
                        "cancelled": result.get("cancelled", 0),
                    },
                )

        stream_cb = self._make_stream_callback(event)
        revised = self.planner.revise_plan_after_tool(
            ctx, event, current_plan, batch_results, planning_context,
            token_callback=stream_cb,
            thinking_callback=self._thinking_callback,
        )
        self._emit_thinking_telemetry(ctx)
        self.telemetry.emit(
            ctx=ctx,
            event_type="plan.revised",
            actor="planner.llm",
            payload={
                "plan_id": revised.plan_id,
                "goal": revised.goal,
                "steps": [self._step_summary(s) for s in revised.steps],
            },
        )
        return revised

    def _finalize(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        step: PlanStep,
        tool_results: dict[str, Any],
    ) -> _RunResult:
        self._state = RobotState.FINALIZING
        final_response = step.response_text or ""
        delivery_target = self._resolve_delivery_target(event, step.delivery_directive)

        self.telemetry.emit(
            ctx=ctx,
            event_type="response.created",
            actor="planner.llm",
            payload={
                "text": final_response,
                "reason": step.reason,
                "delivery_channel": delivery_target.channel if delivery_target else None,
            },
        )
        return _RunResult(
            final_response=final_response,
            delivery_target=delivery_target,
            tool_results=tool_results,
        )

    # ------------------------------------------------------------------
    # Phase: Respond – deliver & persist
    # ------------------------------------------------------------------

    def _respond(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        user_id: str,
        run_result: _RunResult,
    ) -> None:
        self._state = RobotState.RESPONDING
        outbound = OutboundMessage(text=run_result.final_response)

        if run_result.delivery_target is not None:
            self.message_delivery.send(run_result.delivery_target, outbound)

        self._maybe_send_approval_prompt(ctx, event, run_result)

        self.memory.remember_interaction(
            user_id=user_id,
            conversation_id=event.conversation_id,
            user_text=event.text or event.event_type,
            robot_text=outbound.text,
        )
        self.telemetry.emit(
            ctx=ctx,
            event_type="memory.updated",
            actor="memory.store",
            payload={"user_id": user_id, "conversation_id": event.conversation_id},
        )

        if run_result.delivery_target is not None:
            self.telemetry.emit(
                ctx=ctx,
                event_type="output.sent",
                actor=f"gateway.{run_result.delivery_target.channel}",
                payload={
                    "recipient_id": run_result.delivery_target.recipient_id,
                    "channel": run_result.delivery_target.channel,
                    "text": outbound.text,
                    "mode": run_result.delivery_target.mode,
                },
            )

    # ------------------------------------------------------------------
    # Approval prompt sending
    # ------------------------------------------------------------------

    def _maybe_send_approval_prompt(
        self,
        ctx: ExecutionContext,
        event: RobotEvent,
        run_result: _RunResult,
    ) -> None:
        """If any tool result was approval_required, build and send an ApprovalPrompt."""
        approval_results = [
            v for v in run_result.tool_results.values()
            if isinstance(v, dict) and v.get("status") == "approval_required"
        ]
        if not approval_results:
            return

        target = run_result.delivery_target
        if target is None:
            return

        hub = self.message_delivery
        if not hasattr(hub, "send_approval_prompt"):
            return

        for result in approval_results:
            action = result.get("action", "unknown")
            action_context = result.get("action_context", {})
            prompt = ApprovalPrompt(
                prompt_id=new_id("aprv"),
                run_id=ctx.run_id,
                action_context={"action": action, **action_context},
                buttons=self._build_standard_buttons(action, action_context),
                companion_text=run_result.final_response or None,
            )
            hub.send_approval_prompt(target, prompt)
            self.telemetry.emit(
                ctx=ctx,
                event_type="approval.prompt_sent",
                actor="executive.kernel",
                payload={
                    "prompt_id": prompt.prompt_id,
                    "action": action,
                    "channel": target.channel,
                },
            )

    @staticmethod
    def _build_standard_buttons(action: str, action_context: dict[str, Any]) -> list[ApprovalButton]:
        base = {"decision": "approve", "action": action, **action_context}
        deny_base = {"decision": "deny", "action": action, **action_context}
        return [
            ApprovalButton(label="Einmal", payload={**base, "scope": "once"}),
            ApprovalButton(label="Immer so", payload={**base, "scope": "persistent"}),
            ApprovalButton(label="Nein", payload={**deny_base, "scope": "once"}),
            ApprovalButton(label="Nie so", payload={**deny_base, "scope": "deny_scoped"}),
        ]

    # ------------------------------------------------------------------
    # Approval decision handling (deterministic, no LLM)
    # ------------------------------------------------------------------

    def _handle_approval_decision(self, ctx: ExecutionContext, event: RobotEvent) -> None:
        """Process a button-press approval event deterministically."""
        from src.governance.domain import ApprovalRule, ApprovalScope

        payload = event.payload
        decision = payload.get("decision", "")
        scope_str = payload.get("scope", "once")
        action = payload.get("action", "")

        if not action:
            return

        principal_id = event.sender_id or ctx.user_id
        valid_scope_values = {s.value for s in ApprovalScope}

        if decision == "approve":
            scope = ApprovalScope(scope_str) if scope_str in valid_scope_values else ApprovalScope.ONCE
            rule = ApprovalRule(
                principal_id=principal_id,
                action=action,
                source_scope=payload.get("source"),
                target_scope=payload.get("target"),
                scope=scope,
                granted_by=principal_id,
            )
            self.approval_store.grant(rule)
            self.telemetry.emit(
                ctx=ctx,
                event_type="approval.granted",
                actor="governance.approval_store",
                payload={
                    "action": action,
                    "scope": scope.value,
                    "principal_id": principal_id,
                },
            )
        elif decision == "deny":
            rule = ApprovalRule(
                principal_id=principal_id,
                action=action,
                source_scope=payload.get("source"),
                target_scope=payload.get("target"),
                scope=ApprovalScope.DENY,
                granted_by=principal_id,
            )
            if scope_str == "deny_scoped":
                self.approval_store.grant(rule)
            self.telemetry.emit(
                ctx=ctx,
                event_type="approval.denied",
                actor="governance.approval_store",
                payload={
                    "action": action,
                    "scope": scope_str,
                    "principal_id": principal_id,
                },
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_next_step(plan: Plan | None) -> PlanStep:
        if plan is None or not plan.steps:
            raise RuntimeError("Planner returned a plan without steps")
        return plan.steps[0]

    def _emit_thinking_telemetry(self, ctx: ExecutionContext) -> None:
        thinking = getattr(self.planner, "last_thinking", None)
        if not thinking:
            return
        self.telemetry.emit(
            ctx=ctx,
            event_type="thinking.completed",
            actor="planner.llm",
            payload={"length": len(thinking), "text": thinking},
        )

    @staticmethod
    def _step_summary(step: PlanStep) -> dict[str, Any]:
        return {
            "step_id": step.step_id,
            "kind": step.kind,
            "reason": step.reason,
            "tool_name": step.tool_name,
        }

    @staticmethod
    def _resolve_event_delivery_target(
        event: RobotEvent,
        directive: DeliveryDirective | None,
    ) -> ReplyTarget | None:
        if directive is not None and directive.mode == "silent":
            return None

        available_by_channel = {target.channel: target for target in event.available_targets}

        if directive is not None and directive.channel is not None:
            target = available_by_channel.get(directive.channel)
            if target is None:
                raise RuntimeError(f"Delivery channel not available for this event: {directive.channel}")
            return target

        if event.reply_target is not None:
            return event.reply_target

        return None

    def _resolve_delivery_target(
        self,
        event: RobotEvent,
        directive: DeliveryDirective | None,
    ) -> ReplyTarget | None:
        if directive is not None and directive.mode == "silent":
            return None
        target = self._resolve_event_delivery_target(event, directive)
        if target is not None:
            return target
        if self.default_output_target is not None:
            return self.default_output_target
        # No target available — caller will treat None as "drop the response".
        return None
