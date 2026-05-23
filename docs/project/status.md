# Status — actual vs. target

> What is wired, what exists only as a port or in docs, and what is missing entirely.

## Legend

- **Wired** = code exists *and* is used in the production wiring (`src/app.py`)
- **Port / code present** = Protocol + implementation exist, but **not** wired in `app.py`
- **Docs / prompt only** = mentioned in the planner prompt or in docstrings, but not deterministically enforced
- **Missing** = neither code nor port present

---

## 1. Runtime

| Component | Status | Details |
|---|---|---|
| Event ingress (WebSocket) | Wired | `WebSocketChannel` in `build_websocket_service` |
| Event ingress (Telegram) | Demo / code present | `TelegramChannel` exists and is used by demo wiring, but the production `build_websocket_service` currently registers only WebSocket. |
| Heartbeat | Wired | `FirmwareHeartbeat` with `HEARTBEAT.md` |
| KernelPort | Wired | `DigitalRobotKernel`, replaceable via `_kernel_factory` |
| Kernel phases (Observe / Plan / Execute / Respond) | Wired | Fully implemented |
| Control plane (status / onboarding) | Wired | `RobotControlPlane` |
| Config layering | Wired | Host config `~/.cephix/cephix.yaml` plus robot-local `~/.cephix/robots/<id>/robot.yaml`; secrets resolve instance `.env` → global `.env` → OS env. |

## 2. Context

| Component | Status | Details |
|---|---|---|
| Load firmware | Wired | `MarkdownFirmwareStore` with `AGENTS.md`, `POLICY.md`, `CONSTITUTION.md` |
| Load memory documents | Wired | `MarkdownMemoryDocumentStore` with `IDENTITY.md`, `USER.md`, etc. |
| Memory context (facts, interactions) | Wired | `PersistentMemoryStore.build_context()` |
| Compaction / `conversation_summary` | Wired | `PersistentMemoryStore` performs truncation |
| **SOP resolver** | **Port / code present, NOT wired** | `DefaultSOPResolver` exists, but `sop_resolver` is not passed to `DefaultContextAssembler` in `app.py`. Consequence: `active_sops` is always empty; SOP-bound tool mounting and notebook loading never engage. |
| **Skill resolver** | **Port / code present, NOT wired** | `SkillResolverPort` exists, but no resolver is passed in `app.py`. |
| Tool mounting by AutonomyLevel | Wired | `_mount_tools()` works; without SOPs always falls into "general mode" (full catalogue) |
| Notebook loading | Wired (limited) | `_load_notebook_entries()` works, but always empty without active SOPs |
| System tools (`memory.*`, `notebook.*`, `task.*`) | Wired | `SystemToolDriver` in `_build_demo_drivers` |

## 3. Planner

| Component | Status | Details |
|---|---|---|
| LLM planner | Wired | `LLMPlanner` with Anthropic / OpenAI |
| System prompt assembly | Wired | Firmware + memory + SOPs + skills + notebooks + governance block |
| Streaming | Wired | Token callback + `chunk_clear` on tool calls |
| Keyword planner (fallback) | Port / code present | `KeywordPlanner` exists, not in production wiring |

## 4. Governance

| Component | Status | Details |
|---|---|---|
| RiskClass + MetadataRiskClassifier | Wired | `read_only`, `low_risk_mutation`, `high_risk_mutation` |
| PolicyToolExecutionGuard | Wired | Risk check + system-tool bypass + approval check |
| ApprovalStore | Wired | `FileApprovalStore` with JSONL rules |
| Approval flow (prompt + buttons + decision) | Wired | End-to-end: guard → prompt → user button → deterministic storage |
| ActorResolver | Wired (limited) | `ConfigBasedActorResolver` is wired, only basic roles |
| **SOP `safe_actions` as hard policy** | **Docs / prompt only** | `safe_actions` exists in `SOPDefinition` and is mentioned in the planner prompt. The `PolicyToolExecutionGuard` docstring claims to enforce `safe_actions`, but the `check()` implementation does **not**. It is only an LLM hint. |
| **InputGuardPort** | **Port / code present, NOT wired** | `InputGuardPort`, `CompositeInputGuard` exist. Never invoked from `DigitalRobotKernel` or `app.py`. |
| **OutputGuardPort** | **Port / code present, NOT wired** | `OutputGuardPort`, `CompositeOutputGuard` exist. Never invoked. |
| **Sandboxing** | **Partial** | `DockerWorkstationBackend` isolates workstation tools in a Docker container. No general sandbox concept for all tool executions. |

## 5. Audit

| Component | Status | Details |
|---|---|---|
| WideEvent telemetry | Wired | Continuous `telemetry.emit()` calls along the whole run |
| EventLog (JSONL) | Wired | `EventLog` as sink |
| LoggingEventSink | Wired | Writes to Python logging |
| FanoutEventSink | Wired | Combines multiple sinks |
| **Notebook as audit trail** | **Fragmentary** | `NotebookEntryKind.APPROVAL_LOG` exists in the enum but is unused. The real audit trail runs through WideEvent telemetry, not through notebooks. |

## 6. Memory / Notebook — target vs. actual

| Component | Status | Details |
|---|---|---|
| Current tools | Wired | `memory.write_document`, `memory.read_document`, `memory.delete_document`, `notebook.task`, `notebook.sop` |
| Target API (from [concepts/memory.md](../concepts/memory.md)) | Docs only | `memory.write(scope, content)`, `memory.delete(scope, id)`, `memory.search(query)` |
| Target API (from [concepts/notebooks.md](../concepts/notebooks.md)) | Docs only | `notebook.work(content, target?)`, `notebook.audit(content, target?)` |
| Convergence API | Docs only | `remember(scope, content)`, `forget(scope, id)`, `recall(query)` |
| Pre-compaction flush | Missing | Planned, no code yet |
| Dreaming | Missing | Planned, no code yet |
| `memory.search` (subconsciousness) | Missing | Port and implementation not present yet |

---

## The three biggest gaps

1. **SOP resolver is not wired** — without it, the harness always runs in
   "general mode". SOPs, SOP-bound notebooks, and tool subset mounting have
   no effect. This is the central harness dynamic, and it currently does not
   engage.

2. **Input / output guards are not wired** — the ports exist, but the kernel
   does not call them. Incoming events and outgoing messages do not pass
   through any deterministic check.

3. **`safe_actions` is not deterministic** — the guard docstring claims to
   check SOP `safe_actions`, but the implementation does not. It is only an
   LLM-prompt hint, not a hard safety mechanism.

---

## Foundational decisions before more feature work

What needs to be stabilised before new features can rest on it safely?

### F1: Wire the SOP resolver into the production runtime

**Why first:** without SOPs, the harness is static. Tool mounting, notebook
loading, and the entire "dynamic harness" idea only come alive once SOPs
are actually loaded. As long as the resolver is missing, cephix is a
generic agent with the full tool catalogue, not a task-adaptive harness.

**Decisions needed:**

- Should `DefaultSOPResolver` be wired in `build_websocket_service`, or do
  we need a new resolver (e.g. semantic instead of pattern-based)?
- Where do SOP definitions live in production? `FileSOPRepository` or
  repository-backed?

### F2: Connect the kernel to input / output guards

**Why first:** prompt-injection protection and output filtering are not
optional for an enterprise harness. The ports exist; the kernel just needs
to call them.

**Decisions needed:**

- Should `_observe()` check the input guard before the LLM call?
- Should `_respond()` check the output guard before `message_delivery.send()`?
- Which concrete guards land first? (rate-limiting, content filter,
  injection detection)

### F3: Enforce `safe_actions` deterministically in the guard

**Why first:** the docstring promises it; the implementation doesn't.
That's dangerous: anyone reading the docstring trusts a guarantee that
does not exist. Either implement it or correct the docstring.

**Decisions needed:**

- Should `PolicyToolExecutionGuard` use the active SOP's `safe_actions` as
  a deterministic allow-list?
- If yes: how does the active SOP reach the guard? (the guard does not
  currently know the `PlanningContext`)

### F4: Finalise the memory / notebook API

**Why first:** current tool names (`memory.write_document`, `notebook.task`,
`notebook.sop`) deviate sharply from the documented target. The longer both
variants coexist, the more tests and SOPs are written against the old API
and have to migrate later.

**Decisions needed:**

- Migrate directly to the target API (`memory.write(scope, content)` +
  `notebook.work/audit`)? Or keep the current tools for now and only rename?
- When does `memory.search` (subconsciousness) ship?

### F5: Priority and order

Recommended order:

1. **F1 (SOP resolver)** — unlocks the harness dynamics without breaking
   existing code. Can run in parallel with everything else.
2. **F3 (`safe_actions`)** — small fix, large effect. Either implement it
   or fix the docstring so no false safety assumption is made.
3. **F4 (memory / notebook API)** — prerequisite for clean SOP-notebook
   integration.
4. **F2 (input / output guards)** — important for enterprise, but not
   blocking for the test phase.

### What is NOT foundational

These can wait without endangering the foundation:

- **Dreaming / pre-compaction flush** — feature, not foundation.
- **Convergence API (remember / forget / recall)** — design goal, not blocking.
- **General sandboxing** — Docker workstation is enough for the MVP. General
  sandboxing is an infrastructure topic, not an architecture topic.
- **Skill resolver** — only relevant once SOPs with skills are actually used.
