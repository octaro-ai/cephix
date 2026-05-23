# Glossary

The cephix vocabulary, in one place. When a term reappears, this is the
authoritative definition.

## A

**ActorContext** — The resolved identity of an event sender:
`principal` / `delegate` / `counterparty`. Used by the guard to key approval
rules. See [Governance](../concepts/governance.md#actors-and-roles).

**ActorResolver** — Port that maps a `RobotEvent` to an `ActorContext`.
The wired implementation is `ConfigBasedActorResolver`.

**ApprovalPrompt** — A four-button prompt sent to the user when a tool
call requires approval (German UI labels / English meaning):
**Einmal** / Once, **Immer so** / Always, **Nein** / No, **Nie so** / Never.

**ApprovalStore** — Persistent store of approval rules
(`FileApprovalStore` writes JSONL). See [Governance](../concepts/governance.md#the-approval-store).

**AutonomyLevel** — Per-run mode that, combined with the active SOP,
determines which tools are mounted: `SCRIPTED`, `GUIDED`, `AUTONOMOUS`,
`CREATIVE`. See [SOPs](../concepts/sops.md#sop-autonomylevel-matrix).

## C

**ChannelHub** — Aggregator that fans out ingress and egress across all
channels (WebSocket, Telegram, future WhatsApp).

**Composition root** — Single place where ports are wired to concrete
implementations: `src/app.py` (`build_websocket_service`, `build_demo_robot`).

**Config layering** — Host-level defaults and robot registry in
`~/.cephix/cephix.yaml`, plus per-robot overrides in
`~/.cephix/robots/<robot_id>/robot.yaml`.

**Consciousness** — Everything currently loaded into the LLM prompt
(firmware + memory + active notebooks + chat history). Opposite of
*subconsciousness*. See [Memory](../concepts/memory.md#model-of-consciousness).

**ContextAssembler** — Builds a `PlanningContext` from firmware, memory,
SOPs, notebooks, and tool catalogue. Lives in `src/context.py`.

## D

**DigitalRobot** — The composition facade. Wraps kernel + runtime + control
plane into one object. Built in `src/robot.py`.

**DigitalRobotKernel** — The deterministic core that runs
Observe → Plan → Execute → Respond for every event.

## E

**EventSinkPort** — Replaceable interface for telemetry sinks: `EventLog`,
`LoggingEventSink`, `FanoutEventSink`.

## F

**Firmware** — Immutable robot guardrails (`AGENTS.md`, `POLICY.md`,
`CONSTITUTION.md`, `HEARTBEAT.md`). Always loaded into consciousness.
See [Harness Model](../concepts/harness-model.md#2-context-the-dynamic-harness-context).

## G

**GuardDecision** — Outcome of `PolicyToolExecutionGuard.check()`:
`allow`, `deny`, or `require_approval`.

**GovernedToolExecutor** — Wraps tool execution with a guard check before
delegating to the `ToolCollector`.

## H

**Harness** — The deterministic frame that decides which LLM operates
with which context, tools, and limits. See [Harness Model](../concepts/harness-model.md).

**Heartbeat** — Periodic robot self-check that re-reads firmware and emits
a `heartbeat.tick` event.

## K

**KernelPort** — Replaceable interface for the kernel. Concrete impl:
`DigitalRobotKernel`.

## L

**LLMPlanner** — Production planner that builds the system prompt and
calls Anthropic / OpenAI / LiteLLM.

## M

**Memory** — Global long-term store of stable, cross-context recollections.
Always loaded into consciousness. See [Memory](../concepts/memory.md).

**MetadataRiskClassifier** — Reads `risk_class` from `ToolDefinition.metadata`.
Wired implementation of `RiskClassifierPort`.

## N

**Notebook** — Artefact-bound notes. Two modes (`work`, `audit`), three
targets (`sop`, `skill`, `tool`). See [Notebooks](../concepts/notebooks.md).

## P

**Plan** — Output of the planner: an ordered list of `PlanStep`s.

**PlanStep** — One unit in a plan. Kinds: `tool_call` or `finalize`.

**PlanningContext** — The complete worldview handed to the planner: firmware,
memory, active SOPs, mounted tools, recent interactions, etc.

**PolicyToolExecutionGuard** — Wired implementation of
`ToolExecutionGuardPort`. Checks risk → system flag → approval rule.

**Port** — A `Protocol` class declaring an interface. Always lives next to
its layer (`src/ports.py`, `src/sop/ports.py`, etc.).

**Principal** — The owner / authorised operator role in `ActorContext`.

## R

**RiskClass** — `read_only`, `low_risk_mutation`, `high_risk_mutation`.
Attached to each tool's metadata.

**RobotControlPlane** — Side-channel API for status / onboarding / pairings.
Separate from the chat channel.

**RobotEvent** — The only way into the harness. Carries event type, sender,
text, reply target, optional actor context.

**RobotService** — Async host. `run_forever` loop with control-request
dispatch.

**RuntimeEventLoop** — Polls the queue and invokes `kernel.handle_event`.

## S

**`safe_actions`** — A field on `SOPDefinition` listing tools that should
execute without approval inside the SOP. **Currently docs-only**: the
docstring claims enforcement, the guard does not enforce it. See
[Status](../project/status.md).

**SOP** — Standard Operating Procedure. A named, versioned procedure with
trigger patterns, required tools, steps, and learnings. See [SOPs](../concepts/sops.md).

**SOPResolver** — Matches events against SOP `trigger_patterns`.
`DefaultSOPResolver` exists but is **not currently wired** in production.

**Subconsciousness** — Archived chat tails, rotated memory, inactive
notebook entries. Reachable only via `memory.search`. See [Memory](../concepts/memory.md).

**System tool** — Tool flagged `system_tool=true` in its metadata
(`memory.*`, `notebook.*`, `task.*`). Bypasses the approval flow.

## T

**Telemetry** — The structured audit pipeline. Emits `WideEvent`s through
registered `EventSinkPort`s.

**ToolCollector** — Aggregates `ToolDriver`s into one catalogue +
registry + execution surface.

**ToolDriverPort** — A backend providing one or more tools (e.g.
`IMAPMailToolDriver`, `WorkstationToolDriver`).

## W

**WebSocketChannel** — Default channel. WebSocket server, client connections,
chat protocol.

**WideEvent** — The unified telemetry event format. JSONL on disk.
