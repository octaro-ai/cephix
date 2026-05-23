# Cephix

> Digital robot prototype with a semantic bus and deterministic kernel.

Cephix is a Python framework for building digital robots: agent-like systems
with explicit observation, planning, governed tool execution, and audit. It
is opinionated about **determinism**, **governance** (approvals, policies),
and **traceability** (semantic bus, telemetry, event log).

## Where to start

- **New to the project?** Read [Concepts › Harness Model](concepts/harness-model.md),
  then skim the [Diagrams](architecture/diagrams.md).
- **Coming back after a break?** Open [Current State](state.md) — what is in
  flight, what is done, what is next.
- **Want to run it?** Jump to the [Quickstart](getting-started/quickstart.md).
- **Why was X decided that way?** Browse the [ADRs](adr/index.md).
- **Looking for a specific module?** See the [API Reference](reference/api.md).

## Core ideas

- **Deterministic kernel** — `DigitalRobotKernel` runs an explicit
  OBSERVE → PLAN → EXECUTE → RESPOND loop.
- **Governed tools** — every tool call passes through a policy guard with
  risk classification and approvals.
- **Semantic bus + telemetry** — all interesting events are emitted,
  observable, replayable.
- **Markdown-first context** — firmware, memory, SOPs are plain files.
  Humans and the robot read the same source.

## Reading paths

| If you want to... | Start here |
|---|---|
| Understand the architecture | [Harness Model](concepts/harness-model.md) → [Runtime Lifecycle](concepts/runtime-lifecycle.md) → [Diagrams](architecture/diagrams.md) |
| Run the demo | [Installation](getting-started/installation.md) → [Quickstart](getting-started/quickstart.md) |
| Find your way around the source | [Project Layout](getting-started/project-layout.md) → [Code Map](architecture/code-map.md) |
| Plan a contribution | [Status](project/status.md) → [Roadmap](project/roadmap.md) → [ADRs](adr/index.md) |
| Look up a term | [Glossary](reference/glossary.md) |
