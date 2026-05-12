# Code Map

> Welche Datei gehoert zu welcher Harness-Schicht und was darf dort passieren.

## Legende

- **Port** = Protocol-Klasse (Schnittstelle, austauschbar)
- **Impl** = konkrete Implementierung
- **Domain** = Datenmodell (Dataclasses, Enums)
- **Wiring** = Verdrahtung der Ports zu einer laufenden Instanz

---

## 1. Runtime

| Datei | Rolle | Zentrale Symbole |
|---|---|---|
| `src/domain.py` | Domain | `RobotEvent`, `ExecutionContext`, `Plan`, `PlanStep`, `PlanningContext`, `RobotState`, `AutonomyLevel`, `ApprovalPrompt` |
| `src/ports.py` | Port | `KernelPort`, `EventSourcePort`, `MessageDeliveryPort`, `HeartbeatPort`, `ChannelIngressPort`, `ChannelEgressPort`, `ApprovalPromptPort` |
| `src/runtime/kernel.py` | Impl | `DigitalRobotKernel` -- Observe/Plan/Execute/Respond, Approval-Handling |
| `src/runtime/event_loop.py` | Impl | `RuntimeEventLoop` -- Queue, `run_once`, Heartbeat-Integration |
| `src/robot.py` | Wiring | `DigitalRobot` -- Composition Root, baut Kernel + Runtime + ControlPlane |
| `src/service.py` | Impl | `RobotService` -- async Host, `run_forever`, Control-Request-Dispatch |
| `src/control.py` | Impl | `RobotControlPlane` -- Info/Status/Onboarding/Pairings |
| `src/gateways/hub.py` | Impl | `ChannelHub` -- aggregiert Ingress/Egress/Control fuer alle Kanaele |
| `src/gateways/websocket.py` | Impl | `WebSocketChannel` -- WebSocket-Kanal |
| `src/gateways/telegram.py` | Impl | `TelegramChannel` -- Telegram-Kanal |

## 2. Context

| Datei | Rolle | Zentrale Symbole |
|---|---|---|
| `src/ports.py` | Port | `ContextAssemblerPort`, `FirmwarePort`, `MemoryDocumentPort`, `MemoryPort` |
| `src/context.py` | Impl | `DefaultContextAssembler` (assemble, _mount_tools, _load_notebook_entries), `MarkdownFirmwareStore`, `MarkdownMemoryDocumentStore`, `FirmwareHeartbeat` |
| `src/memory/persistent.py` | Impl | `PersistentMemoryStore` -- Fakten, Interaktionen, `conversation_summary`, Compaction |
| `src/memory/store.py` | Impl | `InMemoryMemoryStore` -- leichtgewichtige Variante |
| `src/sop/models.py` | Domain | `SOPDefinition` -- trigger_patterns, required_tools, required_skills, steps, safe_actions |
| `src/sop/resolver.py` | Impl | `DefaultSOPResolver` -- matched SOPs nach Event |
| `src/sop/ports.py` | Port | `SOPResolverPort` |
| `src/skills/ports.py` | Port | `SkillResolverPort` |
| `src/notebooks/models.py` | Domain | `NotebookType`, `NotebookEntry`, `NotebookEntryKind` |
| `src/notebooks/store.py` | Impl | `FileNotebookStore` |
| `src/notebooks/ports.py` | Port | `NotebookStorePort` |
| `robot/firmware/` | Config | `AGENTS.md`, `POLICY.md`, `CONSTITUTION.md`, `HEARTBEAT.md` |
| `robot/memory/` | Config | `IDENTITY.md`, `USER.md`, `MEMORY.md`, `BOOTSTRAP.md` |

## 3. Planner

| Datei | Rolle | Zentrale Symbole |
|---|---|---|
| `src/ports.py` | Port | `PlannerPort` -- `create_initial_plan`, `revise_plan_after_tool` |
| `src/planners/llm.py` | Impl | `LLMPlanner` -- `_build_messages` (System-Prompt aus PlanningContext), `_build_system_prompt` |
| `src/planners/keyword.py` | Impl | `KeywordPlanner` -- regelbasierter Fallback ohne LLM |
| `src/domain.py` | Domain | `Plan`, `PlanStep` (kind: `tool_call` oder `finalize`) |

## 4. Governance

| Datei | Rolle | Zentrale Symbole |
|---|---|---|
| `src/governance/ports.py` | Port | `InputGuardPort`, `ToolExecutionGuardPort`, `OutputGuardPort`, `ActorResolverPort`, `RiskClassifierPort`, `ApprovalStorePort` |
| `src/governance/domain.py` | Domain | `ActorRole`, `ActorContext`, `RiskClass`, `ApprovalScope`, `ApprovalRule` |
| `src/governance/models.py` | Domain | `GuardDecision` (allow / deny / require_approval) |
| `src/governance/tool_guard.py` | Impl | `PolicyToolExecutionGuard` -- Risk + SystemTool + ApprovalStore |
| `src/governance/risk_classifier.py` | Impl | `MetadataRiskClassifier` -- liest `risk_class` aus ToolDefinition.metadata |
| `src/governance/actor_resolver.py` | Impl | `ConfigBasedActorResolver` -- Event -> ActorContext |
| `src/governance/approval_store.py` | Impl | `FileApprovalStore` -- JSONL-basierter Rule-Store |
| `src/governance/composite.py` | Impl | `CompositeInputGuard`, `CompositeOutputGuard` -- Ketten mehrerer Guards |

## 5. Audit

| Datei | Rolle | Zentrale Symbole |
|---|---|---|
| `src/telemetry.py` | Impl | `Telemetry`, `WideEvent`, `EventLog` (JSONL), `LoggingEventSink`, `FanoutEventSink` |
| `src/ports.py` | Port | `TelemetryPort`, `BusPort` |
| `src/telemetry.py` | Port | `EventSinkPort` |

## 6. Tools

| Datei | Rolle | Zentrale Symbole |
|---|---|---|
| `src/tools/ports.py` | Port | `ToolDriverPort`, `ToolCatalogPort`, `ToolRegistryPort`, `ToolExecutionPort` |
| `src/tools/models.py` | Domain | `ToolDefinition` -- name, description, parameters, metadata (risk_class, system_tool, context_mapping) |
| `src/tools/collector.py` | Impl | `ToolCollector` -- aggregiert ToolDrivers, Catalog + Registry + Execution in einem |
| `src/tools/executor.py` | Impl | `GovernedToolExecutor` -- Guard-Check vor Ausfuehrung |
| `src/tools/system_tools.py` | Impl | `SystemToolDriver` -- memory.*, notebook.*, task.* Tools |
| `src/tools/mcs_adapter.py` | Impl | `MCSToolDriverAdapter` -- adaptiert externe MCS ToolDrivers |
| `src/tools/mail_driver_factory.py` | Impl | Mail-Tools mit context_mapping |
| `src/tools/imap_driver.py` | Impl | IMAP-basierte Mail-Tools |
| `src/sop/driver.py` | Impl | `SOPToolDriver` -- SOP-Management-Tools |

## 7. Wiring (Composition Root)

| Datei | Rolle | Zentrale Symbole |
|---|---|---|
| `src/app.py` | Wiring | `build_websocket_service` (Produktiv), `build_demo_robot` / `build_demo_runtime` (Demo), `_build_tool_stack`, `_build_demo_drivers` |
| `src/__main__.py` | Entry | `python -m src` -> `src.cli.main` |
| `src/cli.py` | Entry | CLI-Kommandos (`start`, etc.) -> `build_websocket_service` |
