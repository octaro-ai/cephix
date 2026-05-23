# Code Map

> Which file belongs to which harness layer, and what is allowed to happen there.

## Legend

- **Port** = Protocol class (interface, replaceable)
- **Impl** = concrete implementation
- **Domain** = data model (dataclasses, enums)
- **Wiring** = port-to-instance composition

---

## 1. Runtime

| File | Role | Central symbols |
|---|---|---|
| `src/domain.py` | Domain | `RobotEvent`, `ExecutionContext`, `Plan`, `PlanStep`, `PlanningContext`, `RobotState`, `AutonomyLevel`, `ApprovalPrompt` |
| `src/ports.py` | Port | `KernelPort`, `EventSourcePort`, `MessageDeliveryPort`, `HeartbeatPort`, `ChannelIngressPort`, `ChannelEgressPort`, `ApprovalPromptPort` |
| `src/runtime/kernel.py` | Impl | `DigitalRobotKernel` — Observe/Plan/Execute/Respond, approval handling |
| `src/runtime/event_loop.py` | Impl | `RuntimeEventLoop` — queue, `run_once`, heartbeat integration |
| `src/robot.py` | Wiring | `DigitalRobot` — runtime facade, builds kernel + runtime + control plane from injected ports |
| `src/service.py` | Impl | `RobotService` — async host, `run_forever`, control-request dispatch |
| `src/control.py` | Impl | `RobotControlPlane` — info / status / onboarding / pairings |
| `src/gateways/hub.py` | Impl | `ChannelHub` — aggregates ingress/egress/control for all channels |
| `src/gateways/websocket.py` | Impl | `WebSocketChannel` — WebSocket channel |
| `src/gateways/telegram.py` | Impl | `TelegramChannel` — Telegram channel |
| `src/configuration.py` | Impl | Host config, robot config, workspace layout, layered secret resolution |

## 2. Context

| File | Role | Central symbols |
|---|---|---|
| `src/ports.py` | Port | `ContextAssemblerPort`, `FirmwarePort`, `MemoryDocumentPort`, `MemoryPort` |
| `src/context.py` | Impl | `DefaultContextAssembler` (assemble, _mount_tools, _load_notebook_entries), `MarkdownFirmwareStore`, `MarkdownMemoryDocumentStore`, `FirmwareHeartbeat` |
| `src/memory/persistent.py` | Impl | `PersistentMemoryStore` — facts, interactions, `conversation_summary`, compaction |
| `src/memory/store.py` | Impl | `InMemoryMemoryStore` — lightweight variant |
| `src/sop/models.py` | Domain | `SOPDefinition` — trigger_patterns, required_tools, required_skills, steps, safe_actions |
| `src/sop/resolver.py` | Impl | `DefaultSOPResolver` — matches SOPs against the event |
| `src/sop/ports.py` | Port | `SOPResolverPort` |
| `src/skills/ports.py` | Port | `SkillResolverPort` |
| `src/notebooks/models.py` | Domain | `NotebookType`, `NotebookEntry`, `NotebookEntryKind` |
| `src/notebooks/store.py` | Impl | `FileNotebookStore` |
| `src/notebooks/ports.py` | Port | `NotebookStorePort` |
| `robot/firmware/` | Config | `AGENTS.md`, `POLICY.md`, `CONSTITUTION.md`, `HEARTBEAT.md` |
| `robot/memory/` | Config | `IDENTITY.md`, `USER.md`, `MEMORY.md`, `BOOTSTRAP.md` |

## 3. Planner

| File | Role | Central symbols |
|---|---|---|
| `src/ports.py` | Port | `PlannerPort` — `create_initial_plan`, `revise_plan_after_tool` |
| `src/planners/llm.py` | Impl | `LLMPlanner` — `_build_messages` (system prompt from PlanningContext), `_build_system_prompt` |
| `src/planners/keyword.py` | Impl | `KeywordPlanner` — rule-based fallback without LLM |
| `src/domain.py` | Domain | `Plan`, `PlanStep` (kind: `tool_call` or `finalize`) |

## 4. Governance

| File | Role | Central symbols |
|---|---|---|
| `src/governance/ports.py` | Port | `InputGuardPort`, `ToolExecutionGuardPort`, `OutputGuardPort`, `ActorResolverPort`, `RiskClassifierPort`, `ApprovalStorePort` |
| `src/governance/domain.py` | Domain | `ActorRole`, `ActorContext`, `RiskClass`, `ApprovalScope`, `ApprovalRule` |
| `src/governance/models.py` | Domain | `GuardDecision` (allow / deny / require_approval) |
| `src/governance/tool_guard.py` | Impl | `PolicyToolExecutionGuard` — risk + system-tool + approval store |
| `src/governance/risk_classifier.py` | Impl | `MetadataRiskClassifier` — reads `risk_class` from ToolDefinition.metadata |
| `src/governance/actor_resolver.py` | Impl | `ConfigBasedActorResolver` — event → ActorContext |
| `src/governance/approval_store.py` | Impl | `FileApprovalStore` — JSONL-backed rule store |
| `src/governance/composite.py` | Impl | `CompositeInputGuard`, `CompositeOutputGuard` — chain multiple guards |

## 5. Audit

| File | Role | Central symbols |
|---|---|---|
| `src/telemetry.py` | Impl | `Telemetry`, `WideEvent`, `EventLog` (JSONL), `LoggingEventSink`, `FanoutEventSink` |
| `src/ports.py` | Port | `TelemetryPort`, `BusPort` |
| `src/telemetry.py` | Port | `EventSinkPort` |

## 6. Tools

| File | Role | Central symbols |
|---|---|---|
| `src/tools/ports.py` | Port | `ToolDriverPort`, `ToolCatalogPort`, `ToolRegistryPort`, `ToolExecutionPort` |
| `src/tools/models.py` | Domain | `ToolDefinition` — name, description, parameters, metadata (risk_class, system_tool, context_mapping) |
| `src/tools/collector.py` | Impl | `ToolCollector` — aggregates ToolDrivers; Catalog + Registry + Execution in one |
| `src/tools/executor.py` | Impl | `GovernedToolExecutor` — guard check before execution |
| `src/tools/system_tools.py` | Impl | `SystemToolDriver` — `memory.*`, `notebook.*`, `task.*` tools |
| `src/tools/mcs_adapter.py` | Impl | `MCSToolDriverAdapter` — adapts external MCS ToolDrivers |
| `src/tools/mail_driver_factory.py` | Impl | Mail tools with context_mapping |
| `src/tools/imap_driver.py` | Impl | IMAP-backed mail tools |
| `src/sop/driver.py` | Impl | `SOPToolDriver` — SOP management tools |

## 7. Wiring (composition root)

| File | Role | Central symbols |
|---|---|---|
| `src/app.py` | Wiring | `build_websocket_service` (production), `build_demo_robot` / `build_demo_runtime` (demo), `_build_tool_stack`, `_build_demo_drivers` |
| `src/__main__.py` | Entry | `python -m src` → `src.cli.main` |
| `src/cli.py` | Entry | CLI commands (`start`, etc.) → `build_websocket_service` |
