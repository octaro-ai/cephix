# Roadmap

> What is coming, in what order, and with what motivation.
> For what's *currently in flight*, see [Current State](../state.md).
> For what *exists vs. is missing today*, see [Status](status.md).
> For *how* a planned capability will work, see [Design docs (FRD)](../design/index.md).
> A decision, once made, moves out of here into an [ADR](../adr/index.md).

## Next up

### 1. Approval workflow with self-learning

Build on top of the existing `PolicyToolExecutionGuard` and `FileApprovalStore`.
Promote one-off "Always" decisions into generalised rules; learn from
"Never" decisions to permanently block patterns.

**Why now:** the approval flow is fully wired end-to-end, but every rule is
stored verbatim. Two near-identical approvals create two rules. A small
self-learning pass would compress hundreds of rules into a handful and
reduce prompt fatigue.

**Sub-tasks:**

- Rule generalisation strategy (LLM-assisted? deterministic argument
  pattern detection?)
- Confidence scoring per rule
- Conflict detection when a new rule contradicts an existing one
- UX: how does the user *see* a learned rule, and how do they revoke it?

**Decisions needed before starting:** new ADR.

### 2. Tools, Skills & SOPs with repositories and toolbuilder chain

Lift tools, skills, and SOPs from "files in a folder" to versioned
repositories with discovery, versioning, and a toolbuilder chain that
synthesises and validates new artefacts.

**Why now:** the three-tier composition (tool → skill → SOP) is already
the harness vocabulary, but the iteration loop is manual. A repository
plus a toolbuilder chain turns artefact creation into a first-class flow
with governance and audit.

See [Concepts › Tools, Skills & Toolbuilder](../concepts/toolbuilder.md)
for the intended shape.

**Decisions needed before starting:** new ADR.

### 3. Wire the SOP resolver into the production runtime

Without it, the harness always runs in "general mode" - the central
dynamic-harness idea (SOPs constraining the tool set) does not engage.

See [Status - F1](status.md#f1-wire-the-sop-resolver-into-the-production-runtime).

### 4. Central clock port (bus architecture)

Turn the accepted UTC time principle ([ADR-0006](../adr/0006-central-time-source.md))
into a single injected `ClockPort`, and enforce it (no naive `datetime.now()`).

**Why now:** the principle is already de-facto in effect but unenforced; the
heartbeat bug showed the cost of every component deciding "now" on its own.

Design: [FRD-0002 - Central clock port](../design/0002-clock-port.md) (shape +
migration steps).

### 5. Mail-triage multi-mailbox tenancy (bus architecture)

The first non-trivial workload, on top of the accepted "one robot per user"
decision ([ADR-0005](../adr/0005-robot-per-user-and-multi-mailbox-tenancy.md)):
per-mailbox `tenant_id`, `RunStatus` retained events, channel-side state
assembly.

**Why now:** it is the concrete workload the bus architecture is being shaped
for; several open questions (run persistence across restart, status TTL) need
deciding as it lands.

Design: [FRD-0001 - Mail-triage tenancy](../design/0001-mail-triage-tenancy.md).

## Later

- **Compile mode (split build from boot)** - CI produces a deployable image;
  the image boots later on another host with a preboot credential step. The
  runtime layer is already shaped for it
  ([ADR-0004](../adr/0004-credential-layers.md)). Design:
  [FRD-0003 - Compile mode](../design/0003-compile-mode.md).
- **Agent workstation (a computer for the robot)** - hand each robot a
  persistent, pre-provisioned computer (shell, files, headed browser,
  optional desktop) behind one backend-neutral port, so the same robot can
  work on its own host, in a container, on a server, or on a home computer.
  MCS already has the port with Docker and SSH adapters; missing are a local
  adapter, the provisioning recipe, and the wiring into the tool layer.
  Design: [FRD-0005 - Agent workstation](../design/0005-agent-workstation.md).
- **Harness Intervention Catalog from audit trails** - turn recurring
  trajectory failures into versioned runtime interventions: environment
  contracts, SOP learnings, action canonicalizers, and recovery rules. This
  is the cephix analogue to Life-Harness in
  [Adapting the Interface, Not the Model](https://arxiv.org/abs/2605.22166):
  adapt the model-environment interface without coupling behaviour to one
  model checkpoint.
- **Memory / Notebook API finalisation** - migrate from
  `memory.write_document` / `notebook.task` / `notebook.sop` to the target
  API (`memory.write(scope, content)` / `notebook.work / audit`). See
  [Status - F4](status.md#f4-finalise-the-memory-notebook-api).
- **Input / output guards in the kernel** - wire `InputGuardPort` into
  `_observe()` and `OutputGuardPort` into `_respond()`. See
  [Status - F2](status.md#f2-connect-the-kernel-to-input-output-guards).
- **`safe_actions` as deterministic policy** - make the guard actually
  enforce SOP `safe_actions` instead of just hinting in the prompt. See
  [Status - F3](status.md#f3-enforce-safe_actions-deterministically-in-the-guard).
- **Pre-compaction memory flush** - give the model a chance to preserve
  important conversation tail content before it is compacted.
- **`memory.search` (subconsciousness)** - search over archived
  conversations, rotated memory, and inactive notebook entries.
- **Dreaming** - background process promoting recurring notebook entries
  into memory.

## Considered and parked

- **C4 architecture views (Structurizr DSL)** - adopt when the next
  major subsystem lands (approval self-learning or toolbuilder), not
  before. Concept + tool comparison in
  [FRD-0004 - C4 architecture views](../design/0004-c4-architecture-views.md);
  the deferral decision is [ADR-0002](../adr/0002-diagrams-mermaid-and-c4.md).
- **General sandboxing across all tools** - Docker workstation is enough
  for the MVP. General sandboxing is an infrastructure topic, not an
  architecture one.
- **Skill resolver wiring** - meaningful only once SOPs with skills are
  actually used in production.
- **Convergence API (`remember` / `forget` / `recall`)** - design goal,
  not blocking. See
  [Memory › Convergence](../concepts/memory.md#convergence-a-unified-knowledge-store).

## How this list is maintained

- An item leaves **Next up** when it ships → moves to [Changelog](changelog.md).
- An item moves **down** when priorities shift; reason is noted in a new
  Current State entry.
- A new item enters **Next up** only with an associated ADR or a written
  reason in the body.

### Epics

There is no issue tracker yet, so an **epic is just a text label** here that
groups related roadmap items and the design docs they draw on. Reference work
by its stable ID: **FRD-NNNN** for a design, **ADR-NNNN** for a made decision.
For example, an "Email assistant" epic would bundle
[FRD-0001](../design/0001-mail-triage-tenancy.md) (tenancy) with the
[ADR-0005](../adr/0005-robot-per-user-and-multi-mailbox-tenancy.md) /
[ADR-0007](../adr/0007-robot-home-and-workspace.md) decisions it rests on.
When a real tracker (GitHub Issues/Projects, Linear, ...) arrives, epics move
there as work items that link these IDs; the IDs themselves stay stable.
