# Cephix

**The first digital robot.**

---

Physical robots transformed manufacturing. Digital robots will transform everything else.

Every company has that one employee. The one who knows every process. Who is always available. Who still has the right answer at 3 AM. Who remembers exactly what was discussed last Tuesday.

Cephix builds exactly that employee -- only digital, infinitely scalable, and entirely under your control.

And that is just the beginning.

A digital robot is an autonomous unit that can **perceive, plan, act, learn, and be held accountable**. The same architecture that powers a digital customer service worker can just as easily power:

- a **sentinel** living inside your log streams -- detecting anomalies, healing infrastructure, adapting to system changes autonomously,
- an **autonomous crawler** that traverses APIs and websites, adapts to structural changes on its own, and makes semantic decisions about what matters,
- a **bridge into a physical body**, controlling actuators through the same tool/skill/SOP stack,
- a **data pipeline operator**, making context-aware routing and quality decisions that evolve with every run.

Wherever a problem carries semantic complexity that outgrows hand-coded rules -- that is where a digital robot belongs.

[Octaro](https://octaro.io) is building the company around this insight. The digital worker -- for customer service, sales, onboarding, marketing, and internal operations -- is the first application on top of the platform. Infrastructure sentinels, autonomous crawlers, data stream operators, and cyber-physical bridges are next. Open source, GDPR-compliant, hosted in Europe.

Cephix is the first prototype that shows what all of these look like -- built, running, and ready to learn.

> *We are building the new digital robot industry.*

---

## The Problem

Anyone can build an impressive AI demo in 20 minutes. Prompt in, answer out, done.

Building a productive digital worker is a fundamentally different challenge. An LLM excels at understanding language, deriving next steps, and summarizing information. A productive worker demands more: reliable state management, deterministic execution control, auditing, governance, and a memory that grows over time.

Today's frameworks -- OpenClaw, nanobot, OpenFang, and many others -- recognize this challenge. They all follow the same model: **the agent lives inside its platform.** Its capabilities come from the platform's integrations. Its memory stays on the host. Its identity belongs to the deployment.

We call this the appliance model. A Thermomix cooks -- with Thermomix recipes, on a Thermomix countertop, within Thermomix boundaries.

Cephix follows the industrial robot model. You swap the tool head. You load a different program. You follow a different work instruction. Same robot, completely different capability. And the robot's brain -- its firmware, its learned behaviors, its memory -- travels with it. Every skill, every tool, every work instruction is a **portable, composable, standardized part** that exists independently of the runtime.

## The Core Insight

A physical robot on a factory floor consists of three things: a **tool** (the welding head), a **program** (the welding routine), and a **work instruction** (the SOP). The interplay of these three creates the capability. The capability itself is emergent.

Cephix applies exactly this principle to digital work:

| Physical Robot | Digital Robot (Cephix) |
|----------------|------------------------|
| Tool on the arm | **Tool** -- atomic action (`mail.list`, `crm.search`) |
| Learned program | **Skill** -- instructions + declared tools |
| Work instruction | **SOP** -- Decision Graph that governs the workflow |
| Control unit | **Executive Kernel** -- deterministic state machine |
| Safety cage | **Governance** -- guards at every system boundary |
| Maintenance log | **Telemetry** -- every action as a wide event |
| Experience store | **Memory** -- four-layer memory system |

Every component is a swappable port. Just like a real robot: standard parts, plugged together.

---

## In Action

A user writes via Telegram:

> "What's new in my inbox?"

What happens:

1. The **Gateway** receives the message and normalizes it into a `RobotEvent`
2. The **SOPResolver** detects "inbox" and activates `inbox.check.v2`
3. The **SkillResolver** loads the `email-reading` skill
4. The **ToolRegistry** mounts exactly `mail.list` and `mail.read`
5. The **Memory Layer** builds the relevant context for this user
6. The **Planner** (LLM) creates the plan: `mail.list_new_messages(limit=10)`
7. The **GovernedToolExecutor** validates guards and executes the tool
8. The **Planner** receives the results and formulates the summary
9. The response goes back through the **Telegram Gateway**
10. **Memory** updates itself, every step is recorded as a **Wide Event**

The user receives: number of new messages, subjects, senders, summaries, optional drilldown.

At every point in time, the LLM saw exactly the tools the current workflow step prescribed. Governance validated every execution. The kernel steered the entire flow deterministically. And everything is auditably logged.

---

## Architecture

### The Central Design Decision

Cephix gives each component exactly the responsibility that fits:

- **The LLM decides** what is semantically meaningful -- as planner, reasoner, and summarizer.
- **The Kernel decides** when something is allowed to execute.
- **The Tool Layer decides** how the outside world is accessed.
- **Governance decides** whether an action is permitted.
- **Telemetry records** what actually happened.
- **Memory ensures** the robot improves over time.

This is the clean separation of **probabilistic and deterministic**: the LLM stays strong where it is strong. System responsibility lives in deterministic components.

### Executive Kernel

A deterministic state machine. Every run passes through:

```
IDLE -> OBSERVING -> PLANNING -> ACTING -> FINALIZING -> RESPONDING -> DONE
```

The LLM proposes. The kernel decides, controls, and logs.

### Two Loops

**Outer Runtime Loop** -- keeps the system alive, incurs zero cost at idle, starts a run exactly when an event arrives (**Zero Cost Idle**).

**Inner Run Loop** -- processes exactly one work case: Input -> Memory -> Plan -> Tools -> Revision -> Response -> Delivery -> Events.

### SOP-Driven Tool Mounting

The SOP determines what gets loaded. The LLM receives exactly the tools the current workflow prescribes:

```
Event arrives
  -> SOPResolver determines: "inbox.check.v2"
  -> SOP defines: required_skills=["email-reading"], required_tools=["mail.list", "mail.read"]
  -> SkillResolver loads skill instructions
  -> ToolRegistry mounts exactly these tools
  -> LLM sees exactly the mounted tools
  -> SOPNavigator constrains further per step
```

This is **Progressive Disclosure** -- the robot always has exactly the focus it needs.

### Governance: Guards at Three Boundaries

Security is built in from day one -- as a transparent decorator at every system boundary:

| Boundary | Guard | Examples |
|----------|-------|----------|
| **Input** | `InputGuardPort` | PII detection, prompt injection protection |
| **Execution** | `ToolExecutionGuardPort` | ACL, rate limiting, circuit breaker |
| **Output** | `OutputGuardPort` | Content policy, response sanitization |

Composite pattern: a list of guards per boundary, evaluated sequentially, first deny stops. Empty list = everything allowed (prototype mode).

```python
robot = DigitalRobot(
    ...
    input_guard=CompositeInputGuard([
        PiiDetectionGuard(),
        PromptInjectionGuard(policy=strict_policy),
    ]),
    tool_execution_guard=CompositeToolExecutionGuard([
        AclGuard(permissions=user_permissions),
        RateLimitGuard(limits=rate_config),
    ]),
    output_guard=CompositeOutputGuard([
        ContentPolicyGuard(rules=robot_rules),
    ]),
)
```

### Seven Building Blocks

| Block | Responsibility |
|-------|----------------|
| **Gateway Layer** | Unifies input/output channels (Telegram, email, webhooks, UI) into `RobotEvent` / `OutboundMessage` |
| **Semantic Bus** | Internal nerve system (`event`, `query`, `command`, `action_result`) |
| **Executive Kernel** | Deterministic state machine for a single run |
| **Planner / Reasoner** | LLM-based: interprets, plans, reacts, formulates. Execution stays with the kernel |
| **Tool Layer** | Encapsulates world access (APIs, databases, email, CRM). MCS drivers mountable via adapter |
| **Memory Layer** | Builds context per run, updates memory after completion |
| **Telemetry** | Every action as a wide event -- foundation for auditing, replay, debugging, distillation |

### ROS-Inspired

The architecture follows the same principles as ROS in physical robotics -- at a higher abstraction level, tailored for digital work: clear communication semantics, separation of state machine and communication, separation of perception, action, and execution, event-driven lifecycle. Rigorously **SOLID**, rigorously **Dependency Inversion**.

---

## Memory: The Robot's Mind

The robot transforms experiences over time into different forms of memory -- from raw events to stable, versioned work patterns.

### Four Layers

| Layer | Function | Examples |
|-------|----------|----------|
| **Working Memory** | Temporary runtime context for a single run | Current plan, tool results, intermediate states |
| **Episodic Memory** | Condensed completed work cases | "Inbox -> 3 mails read -> summary sent" |
| **Profile Memory** | Stable user facts with confidence and evidence | "prefers concise answers", "favorite channel: Telegram" |
| **Procedural Memory** | Learned, versioned work patterns | `mail-summary.concise.v1`, `inbox.check.v2` |

### Memory Pipeline

```
Event Store (append-only source of truth)
  -> Episode Builder (condenses events into episodes)
  -> Memory Distiller (produces profile facts + procedure candidates)
  -> Profile Store (stable user facts)
  -> Procedure Store (named, versioned procedures)
  -> Memory Builder (loads relevant context per run)
```

The Event Store is the primary source of truth. Chat history is a **projection** onto a subset of these events. A **Procedure Resolver** selects the relevant work patterns per run -- passive guidelines or formal SOP/DAG building blocks.

---

## MCS Integration

MCS (Model Context Standard) fits directly into the tool layer. Standardization focuses on **system boundaries**: interfaces, tool contracts, discovery, execution boundaries, and telemetry. Internal reasoning, prompt format, memory backend, and planning strategy remain intentionally open.

The **MCS Adapter** wraps MCS drivers into Cephix ports -- every driver is automatically mounted as a namespaced tool and immediately protected by governance.

---

## Wide Events

Every action is stored as a structured wide event (`event_id`, `event_type`, `timestamp`, `run_id`, `trace_id`, `robot_id`, `conversation_id`, `actor`, `payload`). The foundation for auditing, debugging, analysis, memory distillation, replay, and observability.

```
input.received -> memory.context_loaded -> plan.created -> tool.requested ->
tool.completed -> plan.revised -> response.created -> memory.updated ->
output.sent -> run.completed
```

---

## Module Structure

```
src/
  tools/                    # Tool Layer (Actuators)
    models.py               ports.py            registry.py
    executor.py             file_catalog.py     mcs_adapter.py
    write_ports.py
  skills/                   # Skill Layer (Learned Behaviors)
    models.py               ports.py            file_repo.py
    resolver.py             cache.py
  sop/                      # SOP Layer (Work Instructions)
    models.py               ports.py            file_repo.py
    resolver.py             navigator.py        compiler.py
  governance/               # Governance (Guards)
    models.py               ports.py            composite.py
    guards/
  runtime/                  # Kernel + Event Loop
    kernel.py               event_loop.py
  planners/                 # Planner Implementations
  memory/                   # Memory Stack
  gateways/                 # Channel Adapters (Telegram, WebSocket)
  domain.py                 # Domain Objects
  ports.py                  # Port Definitions (Protocols)
  context.py                # ContextAssembler
  robot.py                  # DigitalRobot Aggregate
  service.py                # RobotService Lifecycle
  toolbuilder.py            # ToolBuilder Robot
  telemetry.py              # Wide Event Logging
  bus.py                    # Semantic Bus
  app.py                    # Composition Root
```

---

## Quick Start

```powershell
# Demo
python cephix-drp.py

# Start robot service
python -m src serve --host 127.0.0.1 --port 8765

# Connect chat client (separate process, optional live telemetry)
python -m src chat --url ws://127.0.0.1:8765/ws --debug

# Tests
python -m pytest tests/ -v
```

---

## LLM Freedom: SOPs Are Focus Control, Not a Muzzle

A digital robot must be capable of more than following scripts. The LLM is the most powerful component in the system -- restricting it to predefined workflows wastes its core strength: semantic reasoning across novel situations.

### The Autonomy Dial

Cephix does not have an on/off switch for LLM autonomy. It has a dial with four positions:

| Level | LLM Gets | Use Case |
|-------|----------|----------|
| **SCRIPTED** | Only SOP-required tools. No system tools. | Certified workflows, compliance-critical processes |
| **GUIDED** | SOP tools + memory tools (read/write/search). No procedure proposals. | Standard operations with learning |
| **AUTONOMOUS** | If SOP matches: SOP tools. Otherwise: full catalog. Memory tools included. | Open-ended tasks, customer service |
| **CREATIVE** | Like AUTONOMOUS, plus `procedure.propose`. Full self-learning. | Training phase, exploration, adaptation |

The level is set per robot (constructor injection). Governance guards remain active at every boundary regardless of the level. The kernel still controls execution. The difference is **focus, not permission**.

On CREATIVE, the robot is almost as powerful as a pure agent framework -- but it still cannot create its own tools or modify its own config files. Those capabilities can be provided via tools if desired.

### System Tools: Always Available

Four tools are always mounted, regardless of mode:

| Tool | Purpose |
|------|---------|
| `memory.read` | Read stored facts and recent interactions |
| `memory.write` | Store new observations and preferences |
| `memory.search` | Search memory by content |
| `procedure.propose` | Suggest a new reusable work pattern |

These are the foundation for self-learning: the LLM can actively read and update its memory during a run, and propose new procedures based on observed patterns.

### The Self-Learning Loop

```
User interacts -> Robot observes patterns
  -> Robot proposes procedure (status: proposed)
  -> Human reviews and approves (status: active)
  -> SOPResolver can now match the new procedure
  -> Robot improves over time
```

The LLM suggests. The human decides. The robot improves. This follows the core policy: **"Do not mutate human-owned firmware without explicit approval."**

### Heartbeat: Proactive Behavior

When idle, the robot checks memory for pending items and acts on them -- without being asked. The `HEARTBEAT.md` firmware document controls what the robot does on each tick. Silent completion is the default; notifications only when something is actionable.

---

## Principles

1. **Ports & Protocols** -- The kernel knows only abstractions.
2. **Constructor Injection** -- Everything is wired from the outside.
3. **Determinism at the Core** -- The LLM advises. The kernel decides.
4. **Progressive Disclosure** -- SOPs determine focus. The LLM sees exactly what it needs.
5. **Governance by Design** -- Guards are decorators, built in from day one.
6. **Zero Cost Idle** -- At rest, the system is fully dormant.
7. **Portable Brain** -- Firmware and learned knowledge can be exported and cloned independently.
8. **Think big, start simple** -- The architecture carries large systems and starts as a robust monolith with clean boundaries.

---

## Guiding Principle

> The Kernel says **when** something is allowed to happen.
> The LLM says **what** makes sense next.
> The Tool says **how** the world is changed or read.
> Governance says **whether** an action is permitted.
> Telemetry says **what actually happened**.
> Memory ensures the robot **improves over time**.
