# Runtime Lifecycle

The kernel runs an explicit, deterministic lifecycle for each event:
**Observe → Plan → Execute → Respond**. This page explains the *idea* of each
phase. For a concrete file-by-file trace, see
[Architecture › Run Flow](../architecture/run-flow.md).

## The lifecycle in one picture

```
            ┌──────────────┐
 event ───► │   OBSERVE    │  build PlanningContext
            └──────┬───────┘
                   ▼
            ┌──────────────┐
            │     PLAN     │  ask LLM for a Plan
            └──────┬───────┘
                   ▼
            ┌──────────────┐
            │   EXECUTE    │  run tool calls through the guard
            └──────┬───────┘
                   ▼          (loop: revise plan after each batch)
            ┌──────────────┐
            │   RESPOND    │  deliver reply + persist memory
            └──────────────┘
```

## Phase 1 — Observe

The kernel takes the incoming event and builds the *world the planner will see*.

What happens:

- An `ExecutionContext` is created with `run_id`, `trace_id`, `user_id`.
- The `ActorResolver` (if configured) maps the sender to an `ActorContext`
  (principal, delegate, counterparty).
- The `ContextAssembler` produces a `PlanningContext` by loading:
    - Firmware documents
    - Memory documents and structured memory context
    - SOPs matching the event
    - Notebook entries for active artefacts
    - Tool mount set (filtered by `AutonomyLevel`)

Telemetry emitted: `input.received`, `memory.context_loaded`, `tools.mounted`.

## Phase 2 — Plan

The planner turns the PlanningContext into concrete steps.

What happens:

- `LLMPlanner.create_initial_plan` assembles the system prompt from firmware,
  memory, active SOPs, skills, notebooks, and a governance block.
- The user turn comes from `event.message` plus `recent_interactions`.
- The LLM is invoked (Anthropic or OpenAI) with streaming.
- The response is parsed into a `Plan` of `PlanStep`s. Each step is either a
  `tool_call` (tool name + arguments) or a `finalize` (response text).

Telemetry emitted: `plan.created`.

## Phase 3 — Execute

Each `tool_call` step is sent through the governed executor.

What happens for each tool call:

1. The `GovernedToolExecutor` first checks the guard.
2. The `PolicyToolExecutionGuard` decides one of three outcomes:
    - **Allow** — risk-class is `read_only`, or it is a system tool, or an
      existing approval rule applies.
    - **Approval required** — no matching rule; the kernel prepares an
      `ApprovalPrompt` to be sent to the user in the response phase.
    - **Deny** — an explicit deny rule applies.
3. On allow, the `ToolCollector` delegates to the appropriate `ToolDriverPort`.
4. After all tool calls in the batch, the planner is asked to revise the plan
   in light of the results (`revise_plan_after_tool`).
5. The loop continues until a `finalize` step is emitted.

Telemetry emitted: `tool.requested`, `tool.completed`, `plan.revised`.

## Phase 4 — Respond

The kernel delivers the answer and persists what was learned.

What happens:

- The reply is sent via `MessageDeliveryPort` to the original `reply_target`.
- If any tool call returned `approval_required`, an `ApprovalPrompt` is sent
  with four buttons (Once / Always / No / Never).
- The interaction is written to memory (`remember_interaction`).
- Final telemetry: `output.sent`, `memory.updated`, `run.completed`.

## Special case — Approval decisions

Approval button clicks are **not** plan-driven; they are handled
deterministically:

1. An event with `event_type = "approval.decision"` arrives.
2. `handle_event` recognises the type and returns early — no LLM call.
3. The decision is written to the `ApprovalStore` as a rule (`once`,
   `persistent`, `deny_once`, `deny_persistent`).
4. A short confirmation is sent ("Approval saved.").

On the next run with the same tool and the same arguments, the guard finds
the stored rule and lets the tool through without prompting.

Telemetry emitted: `approval.granted` or `approval.denied`.

## Why this lifecycle matters

The phase boundaries are intentional. Each transition is a place where the
harness can intervene independently of the LLM:

| Boundary | What can intervene |
|---|---|
| Before Plan | Input guard (not yet wired) |
| Before Execute | Tool guard, approval store |
| Before Respond | Output guard (not yet wired), approval prompt routing |
| After Respond | Memory persistence, telemetry sinks |

If you ever want to swap in a different planner, harden a guard, or change
the audit pipeline, you do it at one of these boundaries — not by editing
the prompt.
