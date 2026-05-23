# Harness Model

> **What is a harness?** The deterministic frame that decides *which LLM*
> operates with *which context*, *which tools*, and *which limits*. The LLM
> is not the harness — it is a replaceable planner *inside* the harness.

## Five load-bearing layers

```
  Event in
      |
  [1. RUNTIME]      Outer loop, kernel, event contract
      |
  [2. CONTEXT]      Firmware, memory, SOPs, notebooks, tool mounting
      |
  [3. PLANNER]      LLM prompt assembly, plan generation
      |
  [4. GOVERNANCE]   Risk classification, approval, guard decision
      |
  [5. AUDIT]        Telemetry (WideEvents), approval store, logs
      |
  Effect on the world
```

### 1. Runtime — the outer frame

Accepts events, keeps the kernel alive, delivers responses.

- **RobotEvent** is the only way into the harness.
- **RuntimeEventLoop** polls the queue and calls `kernel.handle_event`.
- **DigitalRobotKernel** deterministically runs Observe → Plan → Execute → Respond.
- **DigitalRobot** is the composition facade: builds kernel, runtime, and control plane from injected ports.

### 2. Context — the dynamic harness context

Determines **what** the planner sees and **which tools** it may operate.

- **Firmware** (`AGENTS.md`, `POLICY.md`, `CONSTITUTION.md`) — immutable
  guardrails, always loaded.
- **Memory documents** (`IDENTITY.md`, `USER.md`, `MEMORY.md`, `BOOTSTRAP.md`) —
  global robot knowledge, always loaded.
- **Memory context** (facts, `conversation_summary`, `recent_interactions`) —
  structured knowledge from the memory store.
- **SOPs** — standard operating procedures the context assembler loads on
  event match. Determine `required_tools` and `required_skills`.
- **Notebooks** — artefact-bound notes (`work` / `audit`), loaded when the
  associated artefact is active.
- **Tool mounting** — the context assembler decides which tools are available
  to the planner based on `AutonomyLevel`:
    - SCRIPTED: only SOP tools
    - GUIDED: SOP tools + system tools
    - AUTONOMOUS: SOP tools or (without SOP) the full catalogue + system tools
    - CREATIVE: as AUTONOMOUS, plus `procedure.propose`

The result is a **PlanningContext** — the complete worldview for the planner.

### 3. Planner — the LLM layer

Turns a PlanningContext into a concrete plan.

- **PlannerPort** is the replaceable interface.
- **LLMPlanner** assembles the system prompt from firmware + memory + SOPs +
  skills + notebooks + governance hints.
- The result is a **Plan** with **PlanSteps** (`tool_call` or `finalize`).
- The planner can revise after tool results via `revise_plan_after_tool`.

### 4. Governance — deterministic limits

Checks **before** each tool execution whether the action is allowed.

- **GovernedToolExecutor** consults the guard before delegating to the ToolCollector.
- **PolicyToolExecutionGuard** decides based on:
    1. `RiskClass` (read_only → allowed)
    2. System-tool flag → allowed
    3. Existing approval rule → allowed or denied
    4. Otherwise → `approval_required`
- **ApprovalStore** persists user decisions (Once / Always / Never).
- **ActorResolver** determines the sender's role (principal, delegate, counterparty).
- The kernel processes `approval.decision` events deterministically, without
  invoking the LLM.

### 5. Audit — traceable runs

Every step emits structured telemetry.

- **WideEvent** is the unified audit format (JSONL).
- **Telemetry** writes via replaceable **EventSinkPorts** (EventLog, LoggingSink, FanoutSink).
- Events: `input.received`, `memory.context_loaded`, `tools.mounted`, `plan.created`,
  `tool.requested`, `tool.completed`, `plan.revised`, `response.created`,
  `approval.prompt_sent`, `approval.granted`, `approval.denied`,
  `run.completed`, `run.failed`.
- Memory interactions and notebook entries are persisted and flow back into
  the context on the next run.

## Why this is not a "personal agent"

A personal agent (Hermes, OpenClaw) bundles firmware, memory, tools, and
governance into one monolithic prompt. The harness is fixed.

Cephix separates these concerns into layers with ports:

- Same kernel, different planner? Swap the port.
- Same planner, different tools? Configure the context assembler.
- Same tools, different governance? Swap the guard.
- Same guard, different audit? Swap the EventSink.

The combination of SOPs, AutonomyLevel, and tool mounting makes the harness
**dynamic**: the same robot adapts per event, rather than requiring a new
agent for every use case.
