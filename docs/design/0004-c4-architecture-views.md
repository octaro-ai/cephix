# FRD-0004 - C4 architecture views

- Status: design / deferred (not yet built)
- Builds on: [ADR-0002 - Diagrams: Mermaid in-tree](../adr/0002-diagrams-mermaid-and-c4.md) (accepted)
- Origin: the C4 concept and tool comparison that used to live in ADR-0002,
  extracted on 2026-06-02 when ADRs were narrowed to record only made
  decisions. ADR-0002 keeps the made decision (Mermaid now, no C4 toolchain
  yet); this doc keeps the forward concept.

The decision is settled that **Mermaid covers all in-tree diagrams today**
(ADR-0002). This document captures the **C4 concept** for static architecture
views and the tool choice we lean toward when the cost becomes worth paying -
so the thinking is not lost while it waits.

## Why C4 at all

As the architecture grows (governance, toolbuilder, skill/SOP repositories,
the bus-centric runtime), we expect more **static architecture views** -
Context, Container, Component. The [C4 model](https://c4model.com/) is the
de-facto vocabulary for those, and Mermaid flowcharts do not scale to a
consistent multi-level architecture picture (a Container view and a Component
view drawn by hand drift apart).

## Tool options

| Option | Notes |
|---|---|
| **C4-PlantUML** | Established, but needs the PlantUML toolchain (Java/server). |
| **Mermaid C4** | Built-in (no new tool), but marked experimental, weaker layout. |
| **Structurizr DSL** | One DSL file describes the model; Context / Container / Component / Deployment views are generated automatically and stay consistent with each other. |

## Leaning

**Structurizr DSL**, because one model yields multiple consistent C4 views
with no manual sync between Context and Container. The intended shape:

- the model lives at `docs/architecture/cephix.dsl`;
- CI renders it to SVG that the Markdown pages embed;
- Mermaid stays the low-friction default for everything procedural
  (sequence diagrams, state machines, flowcharts, ad-hoc prose diagrams).

PlantUML is **not** a candidate as a primary tool (that part is already
decided in ADR-0002); `pyreverse` may be used ad-hoc for class diagrams.

## Trigger

Adopt when the **next major subsystem lands** (currently: approval
self-learning or the toolbuilder chain) - not before. Paying for the model +
CI rendering step only earns out once there is a second big box to keep
consistent with the first.

## Trade-offs / open questions

- Structurizr is one more tool to learn; rendering needs a CLI/Docker step in
  CI. Accepted only at the trigger point.
- Open: pin Structurizr CLI via Docker image vs. a hosted render? Decide at
  adoption.
- Open: which views ship first (Context + Container), and do we keep the
  existing Mermaid component diagram or regenerate it from the model?

When this is adopted, the choice graduates into its own ADR (supersedes the
"deferred" half of ADR-0002).
