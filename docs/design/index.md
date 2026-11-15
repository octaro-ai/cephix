# Design Docs (FRD)

Design documents - Functional Requirements Documents - describe **how a
planned capability will work**, before or while it is built. They are the
forward-looking counterpart to [ADRs](../adr/index.md).

| | ADR | Design doc (FRD) |
|---|---|---|
| Captures | a decision **already made** | a capability **still being designed** |
| Lifetime | immutable, dated | living, revised until it ships |
| Answers | *why* did we decide this | *what* we will build and *how* |
| Open questions | none - it is decided | allowed and expected |

## Lifecycle

```
idea -> roadmap entry -> design doc (this folder) -> decisions settle
     -> ADR (the decision, immutable) + code + changelog entry
```

A design doc may hold several open questions. As each is settled it becomes
a one-line decision in an ADR; the design doc is updated or retired when the
capability ships. Nothing here is a commitment to a final shape - the
commitments live in the ADRs.

## Index

- [FRD-0001 - Mail-triage tenancy](0001-mail-triage-tenancy.md) - multi-mailbox
  tenancy, `tenant_id`, `RunStatus` (forward design on top of the accepted
  [ADR-0005 - robot per user](../adr/0005-robot-per-user-and-multi-mailbox-tenancy.md)).
- [FRD-0002 - Central clock port](0002-clock-port.md) - `ClockPort` /
  `SystemClock` / `ManualClock` and the migration that enforces the accepted
  [ADR-0006 - UTC time principle](../adr/0006-central-time-source.md).
- [FRD-0003 - Compile mode](0003-compile-mode.md) - split build from boot
  (preboot credential layer) on top of the accepted
  [ADR-0004 - credential layers](../adr/0004-credential-layers.md).
- [FRD-0004 - C4 architecture views](0004-c4-architecture-views.md) - the
  deferred C4 concept and Structurizr-DSL leaning behind the accepted
  [ADR-0002 - Mermaid in-tree](../adr/0002-diagrams-mermaid-and-c4.md).
- [FRD-0005 - Agent workstation](0005-agent-workstation.md) - a persistent,
  pre-provisioned computer for the robot (shell, headed browser, optional
  desktop) behind one backend-neutral port, next to the file sandbox of the
  accepted [ADR-0007 - robot home vs. workspace](../adr/0007-robot-home-and-workspace.md).
