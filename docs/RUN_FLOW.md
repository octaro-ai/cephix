# Run Flow: Ein Event von Eingang bis Audit

> Konkreter Durchlauf eines einzelnen Events mit Dateien, Klassen und Methoden.
> Beispiel: User schreibt per WebSocket "Sortiere meine neuen Mails".

## Ueberblick

```
User-Nachricht
  |
  v
WebSocketChannel.drain_events()          [src/gateways/websocket.py]
  |
  v
ChannelHub.collect_new_events()          [src/gateways/hub.py]
  |
  v
RuntimeEventLoop.run_once()              [src/runtime/event_loop.py]
  |
  v
DigitalRobotKernel.handle_event(event)   [src/runtime/kernel.py]
  |
  +---> _observe()    --> PlanningContext
  +---> _plan()       --> Plan
  +---> _execute_plan() --> _RunResult
  +---> _respond()    --> Antwort + Persistenz
```

---

## Phase 1: Event-Eingang

**Datei:** `src/gateways/websocket.py`, `src/gateways/hub.py`

1. User sendet eine WebSocket-Nachricht.
2. `WebSocketChannel` erzeugt ein `RobotEvent`:
   - `event_type = "user.message"`
   - `source_channel = "websocket"`
   - `sender_id = "owner"`
   - `text = "Sortiere meine neuen Mails"`
   - `reply_target = ReplyTarget(channel="websocket", recipient_id="owner")`
3. `ChannelHub.collect_new_events()` aggregiert Events aus allen Kanaelen.
4. `RuntimeEventLoop.run_once()` entnimmt das Event aus der Queue.
5. Aufruf: `kernel.handle_event(event)`.

**Telemetry:** noch keine.

---

## Phase 2: Observe

**Datei:** `src/runtime/kernel.py` -> `_observe()`

1. `ExecutionContext` wird erzeugt mit `run_id`, `trace_id`, `user_id`.
2. **Telemetry:** `input.received` -- Event-Metadaten.
3. Falls `actor_resolver` gesetzt: `ActorResolver.resolve(event)` -> `ActorContext`.
   Ergebnis wird an `event.actor_context` und `ctx.actor_context` gehaengt.
4. `context_assembler.assemble(event, user_id)` wird aufgerufen.

**Datei:** `src/context.py` -> `DefaultContextAssembler.assemble()`

5. **Firmware laden:** `MarkdownFirmwareStore.get_base_guidance()` -> `AGENTS.md`, `POLICY.md`, `CONSTITUTION.md`.
6. **Memory-Dokumente laden:** `MarkdownMemoryDocumentStore.get_documents()` -> `IDENTITY.md`, `USER.md`, `MEMORY.md`, `BOOTSTRAP.md`.
7. **Memory-Context laden:** `PersistentMemoryStore.build_context()` -> Fakten, `conversation_summary`, `recent_interactions`.
8. **SOPs resolven:** `sop_resolver.resolve(event, user_id)` -> matching SOPs.
   (Aktuell: `sop_resolver` ist im Produktiv-Wiring **nicht verdrahtet** -> `active_sops = []`.)
9. **Tool-Mounting:** `_mount_tools()` entscheidet nach `AutonomyLevel`:
   - Ohne aktive SOPs und CREATIVE-Level -> voller Katalog + System-Tools.
10. **Notebook-Eintraege laden:** `_load_notebook_entries()` -> Eintraege fuer aktive SOPs.
    (Aktuell: ohne SOPs leer.)
11. Ergebnis: **PlanningContext** mit allen Feldern.
12. **Telemetry:** `memory.context_loaded`, `tools.mounted`.

---

## Phase 3: Plan

**Datei:** `src/runtime/kernel.py` -> `_plan()`

1. `planner.create_initial_plan(ctx, event, planning_context)` wird aufgerufen.

**Datei:** `src/planners/llm.py` -> `LLMPlanner`

2. `_build_messages()` baut den LLM-Request:
   - System-Prompt aus:
     - Firmware-Dokumente
     - Memory-Dokumente
     - Memory-Context (core_memory, facts, conversation_summary)
     - Aktive SOPs (steps, learnings, safe_actions)
     - Aktive Skills
     - Notebook-Eintraege (User-Task-Notes)
     - Governance-Block (AutonomyLevel, gemountete Tools)
   - User-Turn aus `event.text` + `recent_interactions`
   - Tool-Schemas aus `planning_context.tool_schemas`
3. LLM-Aufruf (Anthropic/OpenAI) mit Streaming.
4. Response wird zu `Plan` mit `PlanSteps` geparst:
   - `tool_call`-Steps: Tool-Name + Argumente
   - `finalize`-Step: Antworttext
5. **Telemetry:** `plan.created` mit Steps-Uebersicht.

---

## Phase 4: Execute

**Datei:** `src/runtime/kernel.py` -> `_execute_plan()`, `_act_on_tool_calls()`

Fuer jeden `tool_call`-Step:

1. **Telemetry:** `tool.requested` -- Tool-Name + Argumente.
2. `tool_executor.execute(ctx, tool_name, arguments)` wird aufgerufen.

**Datei:** `src/tools/executor.py` -> `GovernedToolExecutor.execute()`

3. Pruefe: Ist das Tool gemountet? Sonst RuntimeError.
4. `guard.check(ctx, tool_name, arguments)` wird aufgerufen.

**Datei:** `src/governance/tool_guard.py` -> `PolicyToolExecutionGuard.check()`

5. **RiskClass pruefen:** `risk_classifier.classify(tool_name)`.
   - `read_only` -> `GuardDecision.allow()`
   - System-Tool-Flag -> `GuardDecision.allow()`
6. **ApprovalStore pruefen:** `approval_store.check(principal_id, action, source, target)`.
   - Bestehende Regel gefunden -> allow oder deny.
7. **Keine Regel** -> `GuardDecision.require_approval()`.

Drei moegliche Ausgaenge:

- **Erlaubt:** `ToolCollector.execute()` -> delegiert an den passenden `ToolDriverPort`.
- **Approval erforderlich:** Return `{"status": "approval_required", ...}`.
  Spaeter in `_respond()` wird ein `ApprovalPrompt` an den User gesendet.
- **Verweigert:** Return `{"status": "denied", ...}`.

8. **Telemetry:** `tool.completed`.
9. Nach allen Tool-Calls: `planner.revise_plan_after_tool()` -- LLM bewertet Ergebnisse.
10. **Telemetry:** `plan.revised`.
11. Schleife bis ein `finalize`-Step kommt.

---

## Phase 5: Respond

**Datei:** `src/runtime/kernel.py` -> `_respond()`

1. **Antwort senden:** `message_delivery.send(target, OutboundMessage)`.
2. **Approval-Prompts senden:** Falls Tool-Ergebnisse `approval_required` enthalten,
   wird `_maybe_send_approval_prompt()` aufgerufen -> `ApprovalPrompt` mit Buttons
   (Einmal / Immer so / Nein / Nie so) an den User.
3. **Memory persistieren:** `memory.remember_interaction()`.
4. **Telemetry:** `memory.updated`, `output.sent`.
5. **Telemetry:** `run.completed`.

---

## Sonderfall: Approval-Entscheidung

Wenn der User einen Approval-Button klickt:

1. Event mit `event_type = "approval.decision"` kommt rein.
2. `handle_event()` erkennt den Typ -> **Early Return**, kein LLM-Aufruf.
3. `_handle_approval_decision()` speichert die Regel im `ApprovalStore`.
4. Kurze Bestaetigung wird gesendet ("Freigabe gespeichert.").
5. **Telemetry:** `approval.granted` oder `approval.denied`.

Beim naechsten Mal dasselbe Tool mit denselben Parametern:
Guard findet die gespeicherte Regel -> Tool wird ohne Rueckfrage ausgefuehrt.
