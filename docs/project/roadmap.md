# Roadmap

> What is coming, in what order, and with what motivation.
> For what's *currently in flight*, see [Current State](../state.md).
> For what *exists vs. is missing today*, see [Status](status.md).

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

Without it, the harness always runs in "general mode" — the central
dynamic-harness idea (SOPs constraining the tool set) does not engage.

See [Status — F1](status.md#f1-wire-the-sop-resolver-into-the-production-runtime).

## Later

- **Harness Intervention Catalog from audit trails** — turn recurring
  trajectory failures into versioned runtime interventions: environment
  contracts, SOP learnings, action canonicalizers, and recovery rules. This
  is the cephix analogue to Life-Harness in
  [Adapting the Interface, Not the Model](https://arxiv.org/abs/2605.22166):
  adapt the model-environment interface without coupling behaviour to one
  model checkpoint.
- **Memory / Notebook API finalisation** — migrate from
  `memory.write_document` / `notebook.task` / `notebook.sop` to the target
  API (`memory.write(scope, content)` / `notebook.work / audit`). See
  [Status — F4](status.md#f4-finalise-the-memory-notebook-api).
- **Input / output guards in the kernel** — wire `InputGuardPort` into
  `_observe()` and `OutputGuardPort` into `_respond()`. See
  [Status — F2](status.md#f2-connect-the-kernel-to-input-output-guards).
- **`safe_actions` as deterministic policy** — make the guard actually
  enforce SOP `safe_actions` instead of just hinting in the prompt. See
  [Status — F3](status.md#f3-enforce-safe_actions-deterministically-in-the-guard).
- **Pre-compaction memory flush** — give the model a chance to preserve
  important conversation tail content before it is compacted.
- **`memory.search` (subconsciousness)** — search over archived
  conversations, rotated memory, and inactive notebook entries.
- **Dreaming** — background process promoting recurring notebook entries
  into memory.

## Considered and parked

- **Structurizr DSL for C4 architecture views** — adopt when the next
  major subsystem lands (approval self-learning or toolbuilder), not
  before. See [ADR 0002](../adr/0002-diagrams-mermaid-and-c4.md).
- **General sandboxing across all tools** — Docker workstation is enough
  for the MVP. General sandboxing is an infrastructure topic, not an
  architecture one.
- **Skill resolver wiring** — meaningful only once SOPs with skills are
  actually used in production.
- **Convergence API (`remember` / `forget` / `recall`)** — design goal,
  not blocking. See
  [Memory › Convergence](../concepts/memory.md#convergence-a-unified-knowledge-store).

## How this list is maintained

- An item leaves **Next up** when it ships → moves to [Changelog](changelog.md).
- An item moves **down** when priorities shift; reason is noted in a new
  Current State entry.
- A new item enters **Next up** only with an associated ADR or a written
  reason in the body.
