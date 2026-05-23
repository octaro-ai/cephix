# 0002 — Diagrams: Mermaid for flows, C4 via Structurizr for architecture

- Status: proposed
- Date: 2026-05-22

## Context

The project already uses **Mermaid** for component, sequence, and approval-flow
diagrams (see [Diagrams](../architecture/diagrams.md)). Mermaid renders natively in
GitHub and in MkDocs Material via `pymdownx.superfences`.

As the architecture grows (governance, toolbuilder, skill/SOP repositories), we
expect more **static architecture views**: context, container, component. The
C4 model is the de-facto vocabulary for that.

Options for C4:

- **C4-PlantUML** — established, requires the PlantUML toolchain (Java/server).
- **Mermaid C4** — built-in, marked experimental, weaker layout.
- **Structurizr DSL** — one DSL file describes the model; views (Context,
  Container, Component, Deployment) are generated automatically and stay
  consistent with each other.

## Decision (proposed)

- **Keep Mermaid** for sequence diagrams, state machines, flowcharts, and any
  ad-hoc diagrams co-located with prose.
- **Adopt Structurizr DSL** for the C4 architecture views once we add a second
  major subsystem (planned: approval self-learning, toolbuilder chain). The DSL
  file lives at `docs/architecture/cephix.dsl` and CI renders it to SVG that the
  Markdown pages embed.
- **Do not adopt PlantUML** as a primary tool. `pyreverse` may be used ad-hoc
  for class diagrams when reviewing a module.

## Consequences

**Easier:**

- One model → multiple consistent C4 views (no manual sync between Context and Container).
- Mermaid stays the low-friction default for everything procedural.

**Harder / accepted trade-offs:**

- Structurizr is one more tool to learn; rendering needs a CLI/Docker step in CI.
- Decision deferred until the next subsystem lands, so we do not pay the cost
  before the value.
