# Architecture Overview

Cephix is structured as a set of layers around a deterministic kernel. The kernel
runs an explicit lifecycle (OBSERVE → PLAN → EXECUTE → RESPOND) and delegates
to ports — interfaces — that are wired together in the composition root
([`src/app.py`](https://github.com/your-org/cephix/blob/main/src/app.py)).

For full diagrams (component graph, message flow, approval flow), see [Diagrams](diagrams.md).

## Layers at a glance

| Layer | What lives here | Where |
|---|---|---|
| **Entrypoints** | CLI, `__main__`, scripts | `src/cli.py`, `src/__main__.py`, `cephix-drp.py` |
| **Composition root** | Wires concrete adapters into ports | `src/app.py` |
| **Runtime** | Kernel, event loop, service, control plane | `src/runtime/`, `src/robot.py`, `src/service.py` |
| **Configuration** | Host config, robot config, workspace layout, secrets | `src/configuration.py`, `src/defaults.yaml` |
| **Gateways** | Channel adapters (WebSocket wired; Telegram/WhatsApp code exists) | `src/gateways/` |
| **Context** | Assembler, firmware store, memory docs, SOP resolver | `src/context.py`, `src/sop/` |
| **Planners** | LLM and keyword planners | `src/planners/` |
| **Tools** | Executor, collector, tool drivers | `src/tools/`, `src/workstation/` |
| **Governance** | Policy guard, risk classifier, approval store | `src/governance/` |
| **Audit** | Telemetry, semantic bus, event log | `src/telemetry.py`, `src/bus.py` |
| **Knowledge** | Persistent memory, firmware files, SOP definitions | `src/memory/`, `robot/firmware/`, `robot/memory/`, `robot/sops/` |

## Reading order for newcomers

1. [Diagrams](diagrams.md) — get the visual.
2. [Run Flow](run-flow.md) — follow one event end-to-end.
3. [Code Map](code-map.md) — find your way around the source tree.
4. [Status](../project/status.md) — what is wired, what is port-only, what is missing.

## Key design decisions

- **Ports & adapters everywhere.** The kernel knows interfaces, not implementations.
- **Deterministic where possible, LLM where necessary.** Approval flows do not call
  the LLM; planning does.
- **Markdown is the source.** Firmware, memory, SOP outputs live as plain files
  so humans can read and edit them directly.

For the rationale, see the [ADRs](../adr/index.md).
