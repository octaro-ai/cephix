# Architecture Decision Records

ADRs capture **why** a decision was made - and only decisions that have
**actually been made**. They are short, dated, and immutable. When a decision
is reversed, write a new ADR that supersedes the old one; do not edit history.

What an ADR is **not**: a place for pending work, proposals under
consideration, migration plans, or "not decided yet" lists. Those live in the
[roadmap](../project/roadmap.md) (what is coming, in what order) and in
[design docs / FRDs](../design/index.md) (how a planned capability will work).
A design's decisions graduate into an ADR once they are settled and shipped.

## Format

Each ADR uses this skeleton:

```
# ADR-NNNN - Short title

- Status: accepted | superseded by NNNN | deprecated
- Date: YYYY-MM-DD

## Context
What forces are at play? What problem are we solving?

## Decision
What did we decide to do?

## Consequences
What becomes easier? What becomes harder? What do we accept as trade-off?
```

There is no `proposed` status: an unsettled idea is a roadmap entry or a
design doc, not an ADR. An ADR is written when the decision is real.

## Index

- [ADR-0001 - Use MkDocs Material for documentation](0001-use-mkdocs-material.md)
- [ADR-0002 - Diagrams: Mermaid in-tree; C4 toolchain deferred](0002-diagrams-mermaid-and-c4.md)
- [ADR-0003 - Wire-format codec is not abstracted (for now)](0003-no-codec-abstraction-yet.md)
- [ADR-0004 - Credential layers: build-time, preboot, runtime](0004-credential-layers.md)
- [ADR-0005 - Robot per user (one process per end-user)](0005-robot-per-user-and-multi-mailbox-tenancy.md)
- [ADR-0006 - Central time source: UTC everywhere, one clock, no library defaults](0006-central-time-source.md)
- [ADR-0007 - Robot home vs. workspace: a sandboxed file area for tools](0007-robot-home-and-workspace.md)

## Forward-looking docs

- [Design docs (FRD)](../design/index.md) - how planned capabilities will work
- [Roadmap](../project/roadmap.md) - what is coming, in what order
