# Architecture Decision Records

ADRs capture **why** a non-trivial decision was made. They are short, dated,
and immutable. When a decision is reversed, write a new ADR that supersedes
the old one; do not edit history.

## Format

Each ADR uses this skeleton:

```
# NNNN — Short title

- Status: proposed | accepted | superseded by NNNN | deprecated
- Date: YYYY-MM-DD

## Context
What forces are at play? What problem are we solving?

## Decision
What did we decide to do?

## Consequences
What becomes easier? What becomes harder? What do we accept as trade-off?
```

## Index

- [0001 — Use MkDocs Material for documentation](0001-use-mkdocs-material.md)
- [0002 — Diagrams: Mermaid + C4 via Structurizr](0002-diagrams-mermaid-and-c4.md)
- [0003 — Wire-format codec is not abstracted (for now)](0003-no-codec-abstraction-yet.md)
