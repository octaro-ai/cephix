# Run Flow — Detailed Trace

> A concrete walk-through of a single event with files, classes, and methods.
> Example: the user sends "Sort my new mail" via WebSocket.
>
> For the conceptual lifecycle (what each phase *means*), see
> [Concepts › Runtime Lifecycle](../concepts/runtime-lifecycle.md).

## Overview

```
User message
  │
  ▼
WebSocketChannel.drain_events()          [src/gateways/websocket.py]
  │
  ▼
ChannelHub.collect_new_events()          [src/gateways/hub.py]
  │
  ▼
RuntimeEventLoop.run_once()              [src/runtime/event_loop.py]
  │
  ▼
DigitalRobotKernel.handle_event(event)   [src/runtime/kernel.py]
  │
  ├─► _observe()       → PlanningContext
  ├─► _plan()          → Plan
  ├─► _execute_plan()  → _RunResult
  └─► _respond()       → reply + persistence
```

---

## Phase 1 — Event ingress

**Files:** `src/gateways/websocket.py`, `src/gateways/hub.py`

1. The user sends a WebSocket message.
2. `WebSocketChannel` produces a `RobotEvent`:
    - `event_type = "user.message"`
    - `source_channel = "websocket"`
    - `sender_id = "owner"`
    - `text = "Sort my new mail"`
    - `reply_target = ReplyTarget(channel="websocket", recipient_id="owner")`
3. `ChannelHub.collect_new_events()` aggregates events from all channels.
4. `RuntimeEventLoop.run_once()` pulls the event from the queue.
5. Calls `kernel.handle_event(event)`.

**Telemetry:** none yet.

---

## Phase 2 — Observe

**File:** `src/runtime/kernel.py` → `_observe()`

1. An `ExecutionContext` is created with `run_id`, `trace_id`, `user_id`.
2. **Telemetry:** `input.received` — event metadata.
3. If `actor_resolver` is set: `ActorResolver.resolve(event)` → `ActorContext`.
   The result is attached to `event.actor_context` and `ctx.actor_context`.
4. `context_assembler.assemble(event, user_id)` is invoked.

**File:** `src/context.py` → `DefaultContextAssembler.assemble()`

5. **Load firmware:** `MarkdownFirmwareStore.get_base_guidance()` →
   `AGENTS.md`, `POLICY.md`, `CONSTITUTION.md`.
6. **Load memory documents:** `MarkdownMemoryDocumentStore.get_documents()` →
   `IDENTITY.md`, `USER.md`, `MEMORY.md`, `BOOTSTRAP.md`.
7. **Load memory context:** `PersistentMemoryStore.build_context()` →
   facts, `conversation_summary`, `recent_interactions`.
8. **Resolve SOPs:** `sop_resolver.resolve(event, user_id)` → matching SOPs.
   (Currently: `sop_resolver` is **not wired** in the production wiring →
   `active_sops = []`.)
9. **Mount tools:** `_mount_tools()` decides based on `AutonomyLevel`. Without
   active SOPs and at CREATIVE level: full catalogue + system tools.
10. **Load notebook entries:** `_load_notebook_entries()` → entries for active SOPs.
    (Currently empty without SOPs.)
11. Result: **PlanningContext** with all fields populated.
12. **Telemetry:** `memory.context_loaded`, `tools.mounted`.

---

## Phase 3 — Plan

**File:** `src/runtime/kernel.py` → `_plan()`

1. `planner.create_initial_plan(ctx, event, planning_context)` is invoked.

**File:** `src/planners/llm.py` → `LLMPlanner`

2. `_build_messages()` assembles the LLM request:
    - System prompt from:
        - Firmware documents
        - Memory documents
        - Memory context (core_memory, facts, conversation_summary)
        - Active SOPs (steps, learnings, safe_actions)
        - Active skills
        - Notebook entries (user-task notes)
        - Governance block (AutonomyLevel, mounted tools)
    - User turn from `event.text` + `recent_interactions`
    - Tool schemas from `planning_context.tool_schemas`
3. LLM call (Anthropic / OpenAI) with streaming.
4. Response is parsed into a `Plan` of `PlanStep`s:
    - `tool_call` steps: tool name + arguments
    - `finalize` step: response text
5. **Telemetry:** `plan.created` with steps overview.

---

## Phase 4 — Execute

**File:** `src/runtime/kernel.py` → `_execute_plan()`, `_act_on_tool_calls()`

For each `tool_call` step:

1. **Telemetry:** `tool.requested` — tool name + arguments.
2. `tool_executor.execute(ctx, tool_name, arguments)` is invoked.

**File:** `src/tools/executor.py` → `GovernedToolExecutor.execute()`

3. Check: is the tool mounted? Otherwise `RuntimeError`.
4. `guard.check(ctx, tool_name, arguments)` is invoked.

**File:** `src/governance/tool_guard.py` → `PolicyToolExecutionGuard.check()`

5. **RiskClass check:** `risk_classifier.classify(tool_name)`.
    - `read_only` → `GuardDecision.allow()`
    - System-tool flag → `GuardDecision.allow()`
6. **ApprovalStore check:** `approval_store.check(principal_id, action, source, target)`.
    - Existing rule found → allow or deny.
7. **No rule** → `GuardDecision.require_approval()`.

Three possible outcomes:

- **Allowed:** `ToolCollector.execute()` delegates to the matching `ToolDriverPort`.
- **Approval required:** returns `{"status": "approval_required", ...}`. Later,
  in `_respond()`, an `ApprovalPrompt` is sent to the user.
- **Denied:** returns `{"status": "denied", ...}`.

8. **Telemetry:** `tool.completed`.
9. After all tool calls: `planner.revise_plan_after_tool()` — the LLM evaluates the results.
10. **Telemetry:** `plan.revised`.
11. Loop until a `finalize` step appears.

---

## Phase 5 — Respond

**File:** `src/runtime/kernel.py` → `_respond()`

1. **Send reply:** `message_delivery.send(target, OutboundMessage)`.
2. **Send approval prompts:** if any tool result contains `approval_required`,
   `_maybe_send_approval_prompt()` is invoked → `ApprovalPrompt` with buttons
   (Once / Always / No / Never).
3. **Persist memory:** `memory.remember_interaction()`.
4. **Telemetry:** `memory.updated`, `output.sent`.
5. **Telemetry:** `run.completed`.

---

## Special case — Approval decision

When the user clicks an approval button:

1. An event with `event_type = "approval.decision"` arrives.
2. `handle_event()` recognises the type → **early return**, no LLM call.
3. `_handle_approval_decision()` stores the rule in the `ApprovalStore`.
4. A short confirmation is sent ("Approval saved.").
5. **Telemetry:** `approval.granted` or `approval.denied`.

Next time the same tool runs with the same parameters: the guard finds the
stored rule → the tool executes without prompting.
