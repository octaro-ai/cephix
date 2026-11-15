# ADR-0002 - Diagrams: Mermaid in-tree; C4 toolchain deferred

- Status: accepted
- Date: 2026-05-22

## Context

The project uses **Mermaid** for component, sequence, and approval-flow
diagrams (see [Diagrams](../architecture/diagrams.md)). Mermaid renders
natively in GitHub and in MkDocs Material via `pymdownx.superfences`, so
diagrams stay co-located with prose at zero toolchain cost.

The question was whether to additionally adopt a heavier C4 toolchain
(C4-PlantUML, Mermaid-C4, or Structurizr DSL) for static architecture views
(Context / Container / Component).

## Decision

- **Mermaid is the diagram tool** for sequence diagrams, state machines,
  flowcharts, and any ad-hoc diagram co-located with prose.
- **PlantUML is not adopted** as a primary tool. `pyreverse` may be used
  ad-hoc for class diagrams when reviewing a module.

Adopting a dedicated **C4 toolchain** (the leaning is Structurizr DSL) is
**deferred**, not rejected - until a second major subsystem makes the cost
worth it. The C4 concept, the tool comparison, and the trigger condition are
captured in [Design › C4 architecture views](../design/0004-c4-architecture-views.md);
the roadmap tracks the ordering. When C4 is adopted, that decision graduates
into its own ADR superseding this deferral.

## Consequences

**Easier:**

- One low-friction default for everything procedural; no extra CLI/Docker
  rendering step in CI.
- Diagrams live next to the code they describe and render in GitHub.

**Harder / accepted trade-offs:**

- No single-source-of-truth C4 model today: Context and Container views, if
  drawn in Mermaid, are hand-synced. Accepted until the architecture grows
  enough to justify a model-driven tool.
