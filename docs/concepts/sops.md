# SOPs — Standard Operating Procedures

> An SOP is a **named, versioned procedure** the robot can follow when it
> recognises a matching situation. SOPs are the harness's main mechanism
> for making the robot *task-adaptive* instead of *generic*.

## Why SOPs exist

Without SOPs, every event is processed in "general mode": the planner sees
the full tool catalogue and decides freely. That works for one-off questions,
but for **recurring operational tasks** it has three problems:

1. **No memory of how it should be done.** The planner re-derives the
   procedure each time from prompt + tools.
2. **No way to constrain tools.** A task that should only touch IMAP and
   the file system can in principle call any tool.
3. **No identity for learnings.** Hard-won fixes stay in chat history
   instead of attaching to the procedure they belong to.

An SOP turns a procedure into a first-class artefact: a YAML file with
trigger patterns, required tools, steps, and a learnings document.

## Anatomy of an SOP

A real example from the repo: [`robot/sops/order-export.yaml`](https://github.com/your-org/cephix/blob/main/robot/sops/order-export.yaml)

```yaml
name: order-export
description: Daily order export — fetch, convert XML → PDF, mail, archive.
version: "1.0"

trigger_patterns:
  - "order.?export"
  - "start export"
  - "daily export"

required_tools:
  - shell.exec
  - file.get
  - workstation.start
  - memory.write

learnings_document: LEARNINGS_order-export.md

steps:
  - id: prepare
    name: Prepare environment
    instructions: |
      Ensure the workstation is running. Check that PHP, Composer and the
      Laravel app are present. Read the learnings document so known issues
      are at hand.

  - id: fetch_orders
    name: Fetch orders
    instructions: |
      Run `php artisan orders:export`. Inspect output for errors.

  # ... more steps
```

### Fields that matter for the harness

| Field | Used for |
|---|---|
| `trigger_patterns` | The SOP resolver matches these against the event text |
| `required_tools` | Tool subset the planner is restricted to during this SOP |
| `required_skills` | Skills the planner may compose from |
| `steps` | Ordered guidance handed to the planner as part of the system prompt |
| `safe_actions` | Tools that may execute without approval inside this SOP (target model; not yet deterministic — see [Status](../project/status.md)) |
| `learnings_document` | The associated audit notebook the LLM appends to |

## How an SOP enters the run

```
Event arrives
  │
  ▼
ContextAssembler.assemble()
  │
  ├─► sop_resolver.resolve(event)        → matched SOPs
  ├─► _mount_tools()                     → only required_tools (+ system tools)
  └─► _load_notebook_entries()           → notebooks bound to the SOP
  │
  ▼
PlanningContext.active_sops = [...]
  │
  ▼
LLMPlanner builds system prompt
  → steps, learnings, safe_actions are injected
```

The planner sees the SOP, the constrained tool set, and the notebook
entries from prior runs of the same SOP — all in one assembled prompt.

## SOP × AutonomyLevel matrix

The mount logic in `_mount_tools()` combines SOP presence with the
`AutonomyLevel`:

| AutonomyLevel | With matched SOP | Without SOP |
|---|---|---|
| **SCRIPTED** | Only `required_tools` of the SOP | (nothing useful — needs an SOP) |
| **GUIDED** | `required_tools` + system tools | (degenerates to system tools only) |
| **AUTONOMOUS** | `required_tools` + system tools | Full catalogue + system tools |
| **CREATIVE** | as AUTONOMOUS + `procedure.propose` | as AUTONOMOUS + `procedure.propose` |

The dynamic harness idea is: a SCRIPTED robot in production runs only what
its SOP allows; the same robot in CREATIVE mode at development time can
propose new procedures.

## Authoring an SOP

Where SOPs live:

- **Per-robot:** `robot/sops/*.yaml` (committed to the repo for a given
  robot configuration)
- **Per-instance:** `~/.cephix/robots/<robot_id>/sops/*.yaml` (after
  `cephix init <robot_id>`)

Authoring guidelines that have proven useful:

1. **Make `trigger_patterns` regex-safe and specific.** Overly general
   patterns trigger the SOP for events it cannot actually handle.
2. **List only the tools the SOP really uses in `required_tools`.** The
   smaller the surface, the safer the run.
3. **Write steps as if explaining to a colleague.** The LLM is the
   audience; it benefits from the same clarity a human would.
4. **Treat the learnings document as the SOP's memory.** When the LLM
   discovers a fix or workaround, it appends to that document so the next
   run starts smarter.

## Current limitations

!!! warning "SOP resolver not wired"
    `DefaultSOPResolver` exists but is not currently wired into
    `build_websocket_service`. Consequence: `active_sops` is always empty in
    production, and the SOP machinery does not engage. See
    [Project › Status](../project/status.md) for the foundation
    decisions needed to fix this.

## Related

- [Memory](memory.md) — global, always loaded
- [Notebooks](notebooks.md) — artefact-bound, including `work:sop:<id>` and `audit:sop:<id>`
- [Governance & Approvals](governance.md) — `safe_actions` and the guard
