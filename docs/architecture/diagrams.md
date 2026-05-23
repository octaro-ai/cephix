# Diagrams

Three views of the cephix harness:

1. **Component diagram** — all building blocks, grouped by layer.
2. **Sequence — RobotEvent flow** — a single event from arrival to audit.
3. **Sequence — approval flow** — what happens when a tool is blocked
   and the user is asked.

Click any diagram to zoom and pan (Alt + scroll wheel).

## 1. Component diagram — all building blocks

```mermaid
graph TB
  subgraph Entrypoints ["Entrypoints"]
    CLI["CLI<br/>src/cli.py"]
    Main["__main__.py"]
    Script["cephix-drp.py"]
  end

  subgraph Wiring ["Composition Root"]
    App["app.py<br/>build_websocket_service<br/>build_demo_robot"]
  end

  subgraph RuntimeLayer ["Runtime layer"]
    Robot["DigitalRobot<br/>src/robot.py"]
    EventLoop["RuntimeEventLoop<br/>src/runtime/event_loop.py"]
    Kernel["DigitalRobotKernel<br/>src/runtime/kernel.py"]
    Service["RobotService<br/>src/service.py"]
    ControlPlane["RobotControlPlane<br/>src/control.py"]
  end

  subgraph GatewayLayer ["Channels / Gateways"]
    Hub["ChannelHub<br/>src/gateways/hub.py"]
    WS["WebSocketChannel"]
    TG["TelegramChannel"]
    WA["WhatsAppChannel"]
  end

  subgraph ContextLayer ["Context layer"]
    Assembler["DefaultContextAssembler<br/>src/context.py"]
    FW["MarkdownFirmwareStore"]
    MemDocs["MarkdownMemoryDocumentStore"]
    SOPResolver["DefaultSOPResolver<br/>src/sop/resolver.py"]
    SkillResolver["SkillResolverPort"]
    NotebookStore["FileNotebookStore<br/>src/notebooks/store.py"]
  end

  subgraph PlannerLayer ["Planner layer"]
    LLMPlanner["LLMPlanner<br/>src/planners/llm.py"]
    KeywordPlanner["KeywordPlanner<br/>src/planners/keyword.py"]
  end

  subgraph ToolLayer ["Tool layer"]
    Executor["GovernedToolExecutor<br/>src/tools/executor.py"]
    Collector["ToolCollector<br/>src/tools/collector.py"]
    SystemTools["SystemToolDriver<br/>memory.* notebook.* task.*"]
    MailTools["MailToolDriver<br/>IMAP + MCS"]
    SOPTools["SOPToolDriver"]
    WorkstationTools["WorkstationToolDriver<br/>Docker"]
  end

  subgraph GovernanceLayer ["Governance layer"]
    Guard["PolicyToolExecutionGuard<br/>src/governance/tool_guard.py"]
    RiskClassifier["MetadataRiskClassifier"]
    ApprovalStore["FileApprovalStore<br/>JSONL"]
    ActorResolver["ConfigBasedActorResolver"]
    InputGuard["InputGuardPort<br/>not wired"]
    OutputGuard["OutputGuardPort<br/>not wired"]
  end

  subgraph AuditLayer ["Audit layer"]
    Telemetry["Telemetry<br/>src/telemetry.py"]
    EventLog["EventLog<br/>JSONL"]
    LogSink["LoggingEventSink"]
    Fanout["FanoutEventSink"]
    Bus["SemanticBus<br/>src/bus.py"]
  end

  subgraph KnowledgeLayer ["Knowledge layer"]
    Memory["PersistentMemoryStore<br/>src/memory/persistent.py"]
    FirmwareFiles["robot/firmware/<br/>AGENTS.md POLICY.md<br/>CONSTITUTION.md"]
    MemoryFiles["robot/memory/<br/>IDENTITY.md USER.md<br/>MEMORY.md BOOTSTRAP.md"]
    SOPFiles["SOPDefinition<br/>YAML"]
  end

  subgraph DomainModels ["Domain models"]
    RobotEvent["RobotEvent"]
    PlanningContext["PlanningContext"]
    Plan["Plan + PlanStep"]
    GuardDecision["GuardDecision"]
    WideEvent["WideEvent"]
  end

  CLI --> App
  Main --> CLI
  Script --> App
  App --> Robot
  Robot --> Kernel
  Robot --> EventLoop
  Robot --> ControlPlane
  Service --> EventLoop

  Hub --> WS
  Hub --> TG
  Hub --> WA
  Hub -.->|EventSourcePort| EventLoop
  EventLoop -->|handle_event| Kernel

  Kernel -->|assemble| Assembler
  Assembler --> FW
  Assembler --> MemDocs
  Assembler -.->|optional| SOPResolver
  Assembler -.->|optional| SkillResolver
  Assembler --> NotebookStore
  FW --> FirmwareFiles
  MemDocs --> MemoryFiles
  SOPResolver --> SOPFiles

  Kernel -->|create_initial_plan| LLMPlanner
  Kernel -->|execute| Executor
  Executor -->|check| Guard
  Guard --> RiskClassifier
  Guard --> ApprovalStore
  Executor -->|execute| Collector
  Collector --> SystemTools
  Collector --> MailTools
  Collector --> SOPTools
  Collector --> WorkstationTools

  Kernel --> ActorResolver
  Kernel -->|send| Hub
  Kernel -->|emit| Telemetry
  Kernel -->|publish| Bus
  Kernel -->|remember_interaction| Memory
  Telemetry --> Fanout
  Fanout --> EventLog
  Fanout --> LogSink
```

## 2. Sequence diagram — RobotEvent flow with reply and BusPort

```mermaid
sequenceDiagram
  participant User
  participant WS as WebSocketChannel
  participant Hub as ChannelHub
  participant EvLoop as RuntimeEventLoop
  participant Kernel as DigitalRobotKernel
  participant Bus as SemanticBus
  participant Tel as Telemetry
  participant ActorRes as ActorResolver
  participant Ctx as ContextAssembler
  participant Planner as LLMPlanner
  participant Guard as PolicyToolExecutionGuard
  participant Exec as GovernedToolExecutor
  participant Tool as ToolDriver
  participant Mem as MemoryStore

  User ->> WS: send message
  WS ->> Hub: drain_events()
  Hub ->> EvLoop: collect_new_events()
  EvLoop ->> Kernel: handle_event(RobotEvent)

  Note over Kernel: Phase 1 - OBSERVE
  Kernel ->> Bus: publish("event", "input.received", ...)
  Kernel ->> Tel: emit("input.received")
  Kernel ->> ActorRes: resolve(event) -> ActorContext
  Kernel ->> Ctx: assemble(event, user_id)
  Ctx -->> Kernel: PlanningContext
  Kernel ->> Tel: emit("memory.context_loaded")
  Kernel ->> Tel: emit("tools.mounted")

  Note over Kernel: Phase 2 - PLAN
  Kernel ->> Planner: create_initial_plan(ctx, event, planning_context)
  Planner -->> Kernel: Plan with tool_call mail.list
  Kernel ->> Bus: publish("command", "plan.created", ...)
  Kernel ->> Tel: emit("plan.created")

  Note over Kernel: Phase 3 - EXECUTE
  Kernel ->> Bus: publish("command", "tool.requested", ...)
  Kernel ->> Tel: emit("tool.requested")
  Kernel ->> Exec: execute(ctx, "mail.list", args)
  Exec ->> Guard: check(ctx, "mail.list", args)
  Guard -->> Exec: GuardDecision.allow()
  Exec ->> Tool: execute(ctx, "mail.list", args)
  Tool -->> Exec: mail1, mail2, mail3
  Exec -->> Kernel: mail1, mail2, mail3
  Kernel ->> Tel: emit("tool.completed")

  Kernel ->> Planner: revise_plan_after_tool(results)
  Planner -->> Kernel: Plan finalize "You have 3 new emails"
  Kernel ->> Tel: emit("plan.revised")

  Note over Kernel: Phase 4 - RESPOND
  Kernel ->> Hub: send(target, "You have 3 new emails")
  Hub ->> WS: send(target, message)
  WS ->> User: show reply

  Kernel ->> Mem: remember_interaction(user_text, robot_text)
  Kernel ->> Tel: emit("memory.updated")
  Kernel ->> Tel: emit("output.sent")
  Kernel ->> Tel: emit("run.completed")
```

## 3. Sequence diagram — approval flow (tool is blocked)

```mermaid
sequenceDiagram
  participant User
  participant WS as WebSocketChannel
  participant Hub as ChannelHub
  participant EvLoop as RuntimeEventLoop
  participant Kernel as DigitalRobotKernel
  participant Exec as GovernedToolExecutor
  participant Guard as PolicyToolExecutionGuard
  participant Store as ApprovalStore
  participant Tel as Telemetry

  User ->> WS: "Move mail to Archive"
  WS ->> Hub: drain_events()
  Hub ->> EvLoop: collect_new_events()
  EvLoop ->> Kernel: handle_event(RobotEvent)

  Note over Kernel: Observe + Plan (as above)
  Kernel ->> Exec: execute(ctx, "mail.move", folder=Archive)
  Exec ->> Guard: check(ctx, "mail.move", args)
  Guard ->> Store: check(principal, "mail.move", ...)
  Store -->> Guard: None (no rule)
  Guard -->> Exec: require_approval(risk: low_risk_mutation)
  Exec -->> Kernel: status approval_required

  Note over Kernel: Respond - send approval prompt
  Kernel ->> Hub: send(target, LLM reply)
  Kernel ->> Hub: send_approval_prompt(target, prompt)
  Hub ->> WS: show buttons - Once / Always / No / Never
  WS ->> User: approval buttons

  Kernel ->> Tel: emit("approval.prompt_sent")

  Note over User: User clicks "Always"
  User ->> WS: button click
  WS ->> Hub: approval.decision event
  Hub ->> EvLoop: collect_new_events()
  EvLoop ->> Kernel: handle_event(approval.decision)

  Note over Kernel: Deterministic, no LLM
  Kernel ->> Store: grant(rule persistent)
  Kernel ->> Tel: emit("approval.granted")
  Kernel ->> Hub: send(target, "Approval saved.")
  Hub ->> WS: confirmation
  WS ->> User: "Approval saved."
```
