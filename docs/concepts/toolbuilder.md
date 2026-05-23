# Tools, Skills & Toolbuilder

!!! warning "Status: planned subsystem"
    This page describes a planned subsystem that is **not yet implemented**.
    It captures the intended shape so the foundations laid today
    (`src/toolbuilder.py`, `src/tools/`, `src/skills/`, `src/sop/`) point
    toward the same target. The current code has the *file* stubs but the
    repositories and the chain are not built.

## The three-tier composition

Cephix distinguishes three artefact tiers, from smallest to largest:

```
Tool   →  the atomic capability (one API call, one shell command)
Skill  →  a recipe that combines tools to achieve a sub-goal
SOP    →  a procedure that orchestrates skills to deliver an outcome
```

Each tier has its own repository (planned), its own iteration loop, and its
own notebook namespace. The harness already loads them in this order:

- `required_tools` of an SOP gate the available tools
- `required_skills` of an SOP unlock specific skill recipes
- The matched SOP itself is the top-level procedure

## Why tools, skills, and SOPs are separate

It would be possible to put everything into one giant SOP. We don't, because
of **reuse boundaries**:

| Tier | Reuse | Iteration cycle |
|---|---|---|
| Tool | Across many skills and SOPs | Slow — tools are infrastructure |
| Skill | Across multiple SOPs | Medium — skills are recipes |
| SOP | Per use case | Fast — SOPs evolve with operations |

A learning about a *tool* (e.g. "this API returns 500 when the timeout is
below 5s") belongs at the tool tier so every skill using it benefits.
A learning about an *SOP* (e.g. "for client X, route to Finance instead
of Archive") belongs at the SOP tier and does not pollute the tool.

This is also why notebook entries are scoped by artefact — see
[Notebooks](notebooks.md).

## Repositories (planned)

Each tier gets its own repository:

| Repository | Holds | Status |
|---|---|---|
| **ToolRepository** | Tool definitions + drivers, with risk class metadata | Partially: `ToolCollector` aggregates drivers; no central repo yet |
| **SkillRepository** | Skill recipes (name, required tools, parameters, steps) | `SkillResolverPort` exists; no concrete impl wired |
| **SOPRepository** | SOP YAML files + matching rules | `FileSOPRepository` exists; not in production wiring |

The repositories solve three problems:

1. **Discovery** — what tools/skills/SOPs are available right now?
2. **Versioning** — which version of an SOP did this run use?
3. **Distribution** — pull tools/skills/SOPs from a remote repo, not just from a local folder.

## The toolbuilder chain (planned)

The toolbuilder chain is the **production pipeline** for new artefacts.
The idea: instead of hand-writing every tool, derive it from a higher-level
spec and validate it before letting the robot use it.

```
┌─────────────────────┐
│  Specification      │   ← Markdown + JSON Schema
│  (what should it do?)│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Synthesis          │   ← LLM produces a candidate driver
│  (LLM + templates)  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Validation         │   ← unit tests, schema checks, dry-run
│  (deterministic)    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Approval gate      │   ← user signs off; rule lands in approval store
│  (governance reuse) │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Repository commit  │   ← tool/skill/SOP becomes available
└─────────────────────┘
```

Key design properties:

- **Reuses governance.** The approval gate is the same `ApprovalStore`
  used at runtime — no parallel mechanism.
- **Reuses telemetry.** Each chain step emits WideEvents, just like a
  normal run. The whole chain is auditable.
- **Idempotent.** Running the chain twice for the same spec converges to
  the same artefact (modulo LLM non-determinism, which is why the
  validation step is deterministic).

## Open design questions

| Question | Current thinking |
|---|---|
| Should tools and skills live in the same monorepo or separate ones? | Same repo, separate folders. Reuse the cephix layout for symmetry. |
| Does the synthesis step need a constrained DSL or free-form Python? | Likely a Python skeleton + constrained section, like SOP YAML today. |
| How are tool versions referenced from skills and SOPs? | Open — leaning toward semver, pinned in YAML, with optional `>=` ranges. |
| Where does the approval rule for a new tool come from? | Probably new rule kind: `tool_definition_approval` with the tool spec hash as the action key. |

## Related

- [Memory](memory.md) — `audit:tool:<id>` is where the toolbuilder reads from
- [Notebooks](notebooks.md) — `work:tool:<id>` and `audit:tool:<id>` capture iteration
- [Governance & Approvals](governance.md) — the approval gate reused at build time
- [Project › Roadmap](../project/roadmap.md) — when this subsystem is targeted
