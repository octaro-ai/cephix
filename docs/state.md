# Current State

> **Purpose of this page:** when you come back after a break, this is the
> first thing you read. Keep it short, keep it current. Update it whenever the
> in-flight set changes — not after the fact.

_Last updated: 2026-05-22_

## In flight

- _Nothing actively in flight._ Ready to resume work on the robot itself.

## Planned next

- **Approval workflow with self-learning** — promote one-off approvals into
  persistent rules; learn from "Immer so" / "Nie so" decisions. See
  [Project › Roadmap](project/roadmap.md#1-approval-workflow-with-self-learning).
- **Tool / Skill / SOP repositories + toolbuilder chain** — versioned
  repositories with a chain that synthesises and validates new artefacts.
  See [Concepts › Tools, Skills & Toolbuilder](concepts/toolbuilder.md).
- **Wire the SOP resolver into production runtime** — without this the
  central dynamic-harness idea does not engage.
  See [Project › Status — F1](project/status.md#f1-wire-the-sop-resolver-into-the-production-runtime).

## Recently done

- **Documentation site complete** — MkDocs Material with English content,
  Mermaid + pan/zoom diagrams, ADRs, external references, structured nav
  (Concepts / Architecture / Project / Reference). Built and deployable
  via `Dockerfile.docs` to Coolify.
- **Config layering** — host config `~/.cephix/cephix.yaml` plus per-robot
  `~/.cephix/robots/<id>/robot.yaml` with layered secrets (instance `.env`
  → global `.env` → OS env).
- **Governance layer** with `PolicyToolExecutionGuard` and JSONL approval store.
- **SOP management features** and demo driver integration.
- **`KernelPort`** for pluggable decision-making.

## Open questions / parked

- **Architecture diagrams** — keep Mermaid in repo, or model with
  Structurizr DSL and generate C4 views? Adopt when the next subsystem
  lands (Approval self-learning or Toolbuilder).
  See [ADR 0002](adr/0002-diagrams-mermaid-and-c4.md).
- **SOP YAML → documentation page generator** — planned, not yet
  implemented. Worth adding once the SOP catalogue grows beyond one file.

## How to update this page

This page is **manual**, on purpose. Update it when:

- A new initiative starts → add to **In flight**.
- An initiative finishes → move to **Recently done** (trim aggressively, keep last ~5).
- A direction changes → write it in **Open questions** before you forget the reasoning.
