# Project Layout

A quick map of the repository so you don't get lost on your first walk-through.

## Top-level directories

```
cephix/
├── src/              ← Python source for the framework
├── robot/            ← Robot-specific assets (firmware, memory, SOPs)
├── docs/             ← This documentation site
├── tests/            ← Pytest suite
├── cephix-drp.py     ← Convenience entrypoint for the demo flow
├── pyproject.toml    ← Package metadata and dependencies
├── mkdocs.yml        ← Documentation site configuration
└── uv.lock           ← Resolved dependency lockfile
```

## `src/` — framework code

Organised by the five harness layers:

```
src/
├── app.py            ← Composition root (build_websocket_service, build_demo_robot)
├── cli.py            ← CLI entrypoints (init, start, chat, list, demo)
├── __main__.py       ← `python -m src` → cli.main
├── robot.py          ← DigitalRobot facade
├── service.py        ← Async host (RobotService)
├── control.py        ← RobotControlPlane (status, onboarding)
├── domain.py         ← Core dataclasses (RobotEvent, PlanningContext, Plan, ...)
├── ports.py          ← Protocol classes for every layer
│
├── runtime/          ← Kernel + event loop
│   ├── kernel.py
│   └── event_loop.py
│
├── gateways/         ← Channel adapters (WebSocket, Telegram, ...)
├── context.py        ← Context assembler + firmware / memory document stores
├── memory/           ← Persistent memory store
├── notebooks/        ← Notebook store
├── sop/              ← SOP resolver + driver + models
├── skills/           ← Skill ports
│
├── planners/
│   ├── llm.py        ← LLMPlanner (Anthropic / OpenAI / LiteLLM)
│   └── keyword.py    ← Rule-based fallback
│
├── tools/
│   ├── executor.py   ← GovernedToolExecutor (with guard check)
│   ├── collector.py  ← ToolCollector
│   ├── system_tools.py
│   ├── imap_driver.py
│   ├── mail_driver_factory.py
│   └── mcs_adapter.py
│
├── governance/
│   ├── tool_guard.py
│   ├── risk_classifier.py
│   ├── actor_resolver.py
│   ├── approval_store.py
│   └── composite.py
│
├── workstation/      ← Docker-backed sandboxed tools
├── telemetry.py      ← WideEvent, EventLog, sinks
├── bus.py            ← SemanticBus
└── llm/              ← LLM provider adapters
```

For a deeper file-by-file role assignment, see
[Architecture › Code Map](../architecture/code-map.md).

## `robot/` — robot-specific assets

This is what makes one cephix instance different from another. Everything
here is Markdown or YAML — humans and the robot read the same files.

```
robot/
├── firmware/         ← Immutable guardrails, always loaded into the prompt
│   ├── AGENTS.md
│   ├── POLICY.md
│   ├── CONSTITUTION.md
│   └── HEARTBEAT.md
│
├── memory/           ← Global memory documents
│   ├── IDENTITY.md
│   ├── USER.md
│   ├── MEMORY.md
│   └── BOOTSTRAP.md
│
└── sops/             ← Standard operating procedures
    └── order-export.yaml
```

When you initialise a new robot via `cephix init <robot_id>`, this layout is
copied into `~/.cephix/robots/<robot_id>/`. The host registry lives in
`~/.cephix/cephix.yaml`; the per-robot runtime config lives next to those
assets as `robot.yaml`.

## `docs/` — this site

```
docs/
├── index.md
├── state.md                ← Current state (in-flight tracker)
├── getting-started/        ← Installation, quickstart, this page
├── concepts/               ← Harness model, lifecycle, memory, notebooks, ...
├── architecture/           ← Diagrams, code map, run flow
├── adr/                    ← Architecture decision records
├── project/                ← Status, roadmap, changelog
└── reference/              ← Configuration, glossary, API
```

The site is built with MkDocs Material (`mkdocs serve` for local preview,
`mkdocs build` for static output). See
[ADR 0001](../adr/0001-use-mkdocs-material.md) for the rationale.

## Where to look for what

| You want to... | Open... |
|---|---|
| Understand the architecture | [Concepts › Harness Model](../concepts/harness-model.md) |
| Find a specific file's role | [Architecture › Code Map](../architecture/code-map.md) |
| Trace one event end-to-end | [Architecture › Run Flow](../architecture/run-flow.md) |
| See what's wired vs. missing | [Project › Status](../project/status.md) |
| Add a new tool | `src/tools/` and `src/tools/system_tools.py` for a reference |
| Add a new channel | `src/gateways/` |
| Tweak robot identity / behaviour | `robot/firmware/` and `robot/memory/` |
