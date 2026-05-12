# Gaps: Ist-Zustand vs. Zielmodell

> Was ist verdrahtet, was existiert nur als Port/Doku, und was fehlt ganz.

## Legende

- **Verdrahtet** = Code existiert UND wird im Produktiv-Wiring (`src/app.py`) genutzt
- **Port/Code da** = Protocol + Implementierung existieren, aber NICHT in `app.py` verdrahtet
- **Nur Doku/Prompt** = Wird im Planner-Prompt erwaehnt oder in Docstrings beschrieben, aber nicht deterministisch erzwungen
- **Fehlt** = Weder Code noch Port vorhanden

---

## 1. Runtime

| Komponente | Status | Details |
|---|---|---|
| Event-Eingang (WebSocket) | Verdrahtet | `WebSocketChannel` in `build_websocket_service` |
| Event-Eingang (Telegram) | Verdrahtet | `TelegramChannel` in `build_websocket_service` |
| Heartbeat | Verdrahtet | `FirmwareHeartbeat` mit HEARTBEAT.md |
| KernelPort | Verdrahtet | `DigitalRobotKernel`, austauschbar via `_kernel_factory` |
| Kernel-Phasen (Observe/Plan/Execute/Respond) | Verdrahtet | Vollstaendig implementiert |
| Control Plane (Status/Onboarding) | Verdrahtet | `RobotControlPlane` |

## 2. Context

| Komponente | Status | Details |
|---|---|---|
| Firmware laden | Verdrahtet | `MarkdownFirmwareStore` mit `AGENTS.md`, `POLICY.md`, `CONSTITUTION.md` |
| Memory-Dokumente laden | Verdrahtet | `MarkdownMemoryDocumentStore` mit `IDENTITY.md`, `USER.md`, etc. |
| Memory-Context (Fakten, Interaktionen) | Verdrahtet | `PersistentMemoryStore.build_context()` |
| Compaction / conversation_summary | Verdrahtet | `PersistentMemoryStore` fuehrt Truncation durch |
| **SOP-Resolver** | **Port/Code da, NICHT verdrahtet** | `DefaultSOPResolver` existiert, aber `sop_resolver` wird in `app.py` nicht an den `DefaultContextAssembler` uebergeben. Konsequenz: `active_sops` ist immer leer, SOP-gebundenes Tool-Mounting und Notebook-Loading greifen nie. |
| **Skill-Resolver** | **Port/Code da, NICHT verdrahtet** | `SkillResolverPort` existiert, aber kein Resolver in `app.py` uebergeben. |
| Tool-Mounting nach AutonomyLevel | Verdrahtet | `_mount_tools()` funktioniert, faellt ohne SOPs immer in "General Mode" (voller Katalog) |
| Notebook-Loading | Verdrahtet (eingeschraenkt) | `_load_notebook_entries()` funktioniert, aber ohne aktive SOPs immer leer |
| System-Tools (memory.*, notebook.*, task.*) | Verdrahtet | `SystemToolDriver` in `_build_demo_drivers` |

## 3. Planner

| Komponente | Status | Details |
|---|---|---|
| LLM-Planner | Verdrahtet | `LLMPlanner` mit Anthropic/OpenAI |
| System-Prompt-Aufbau | Verdrahtet | Firmware + Memory + SOPs + Skills + Notebooks + Governance-Block |
| Streaming | Verdrahtet | Token-Callback + chunk_clear bei Tool-Calls |
| Keyword-Planner (Fallback) | Port/Code da | `KeywordPlanner` existiert, aber nicht im Produktiv-Wiring |

## 4. Governance

| Komponente | Status | Details |
|---|---|---|
| RiskClass + MetadataRiskClassifier | Verdrahtet | `read_only`, `low_risk_mutation`, `high_risk_mutation` |
| PolicyToolExecutionGuard | Verdrahtet | Risk-Check + System-Tool-Bypass + Approval-Check |
| ApprovalStore | Verdrahtet | `FileApprovalStore` mit JSONL-Regeln |
| Approval-Flow (Prompt + Buttons + Decision) | Verdrahtet | End-to-End: Guard -> Prompt -> User-Button -> deterministische Speicherung |
| ActorResolver | Verdrahtet (eingeschraenkt) | `ConfigBasedActorResolver` ist verdrahtet, aber nur basic Rollen |
| **SOP safe_actions als harte Policy** | **Nur Doku/Prompt** | `safe_actions` existiert in `SOPDefinition` und wird im Planner-Prompt erwaehnt. Der `PolicyToolExecutionGuard`-Docstring behauptet, `safe_actions` zu pruefen, aber die `check()`-Implementierung tut es NICHT. Es ist nur ein LLM-Hinweis. |
| **InputGuardPort** | **Port/Code da, NICHT verdrahtet** | `InputGuardPort`, `CompositeInputGuard` existieren. Nirgends in `DigitalRobotKernel` oder `app.py` aufgerufen. |
| **OutputGuardPort** | **Port/Code da, NICHT verdrahtet** | `OutputGuardPort`, `CompositeOutputGuard` existieren. Nirgends aufgerufen. |
| **Sandboxing** | **Teilweise** | `DockerWorkstationBackend` isoliert Workstation-Tools in einem Docker-Container. Kein generelles Sandbox-Konzept fuer alle Tool-Ausfuehrungen. |

## 5. Audit

| Komponente | Status | Details |
|---|---|---|
| WideEvent-Telemetry | Verdrahtet | Durchgehende `telemetry.emit()`-Aufrufe entlang des gesamten Runs |
| EventLog (JSONL) | Verdrahtet | `EventLog` als Sink |
| LoggingEventSink | Verdrahtet | Schreibt in Python-Logging |
| FanoutEventSink | Verdrahtet | Kombiniert mehrere Sinks |
| **Notebook als Audit-Trail** | **Fragmentarisch** | `NotebookEntryKind.APPROVAL_LOG` existiert im Enum, wird nirgends verwendet. Der eigentliche Audit-Trail laeuft ueber WideEvent-Telemetry, nicht ueber Notebooks. |

## 6. Memory / Notebook (Zielmodell vs. Ist)

| Komponente | Status | Details |
|---|---|---|
| Aktuelle Tools | Verdrahtet | `memory.write_document`, `memory.read_document`, `memory.delete_document`, `notebook.task`, `notebook.sop` |
| Ziel-API (aus docs/MEMORY.md) | Nur Doku | `memory.write(scope, content)`, `memory.delete(scope, id)`, `memory.search(query)` |
| Ziel-API (aus docs/NOTEBOOK.md) | Nur Doku | `notebook.work(content, target?)`, `notebook.audit(content, target?)` |
| Konvergenz-API | Nur Doku | `remember(scope, content)`, `forget(scope, id)`, `recall(query)` |
| Pre-Compaction-Flush | Fehlt | Geplant, aber kein Code vorhanden |
| Dreaming | Fehlt | Geplant, aber kein Code vorhanden |
| memory.search (Unterbewusstsein) | Fehlt | Port und Implementierung noch nicht vorhanden |

---

## Zusammenfassung: Die drei groessten Luecken

1. **SOP-Resolver nicht verdrahtet** -- Ohne ihn laeuft der Harness immer im "General Mode".
   SOPs, SOP-gebundene Notebooks und Tool-Subset-Mounting bleiben wirkungslos.
   Das ist die zentrale Harness-Dynamik und sie greift aktuell nicht.

2. **Input/Output-Guards nicht verdrahtet** -- Die Ports existieren, aber der Kernel
   ruft sie nicht auf. Eingehende Events und ausgehende Nachrichten durchlaufen
   keine deterministische Pruefung.

3. **safe_actions nicht deterministisch** -- Der Guard-Docstring behauptet,
   SOP-safe_actions zu pruefen, aber die Implementierung tut es nicht.
   Es ist nur ein LLM-Prompt-Hinweis, kein harter Schutz.

---

## Fundament-Entscheidungen vor weiterem Featurebau

Was muss stabilisiert werden, bevor neue Features sicher aufsetzen koennen?

### F1: SOP-Resolver in der Produktiv-Runtime verdrahten

**Warum zuerst:** Ohne SOPs ist der Harness statisch. Tool-Mounting, Notebook-Loading
und die gesamte "dynamischer Harness"-Idee greifen erst, wenn SOPs tatsaechlich
geladen werden. Solange der Resolver fehlt, ist Cephix ein generischer Agent
mit vollem Toolkatalog, nicht ein aufgabenadaptiver Harness.

**Entscheidung noetig:**
- Soll der `DefaultSOPResolver` in `build_websocket_service` verdrahtet werden,
  oder braucht es einen neuen Resolver (z. B. semantisch statt Pattern-basiert)?
- Wo liegen die SOP-Definitionen produktiv? `FileSOPRepository` oder Repo-basiert?

### F2: Kernel mit Input/Output-Guards verbinden

**Warum zuerst:** Prompt-Injection-Schutz und Output-Filtering sind fuer
einen Enterprise-Harness nicht optional. Die Ports existieren, der Kernel
muss sie nur aufrufen.

**Entscheidung noetig:**
- Soll `_observe()` den Input-Guard vor dem LLM-Aufruf pruefen?
- Soll `_respond()` den Output-Guard vor dem `message_delivery.send()` pruefen?
- Welche konkreten Guards werden initial implementiert?
  (z. B. Rate-Limiting, Content-Filter, Injection-Detection)

### F3: safe_actions deterministisch im Guard umsetzen

**Warum zuerst:** Der Docstring verspricht es, die Implementierung tut es nicht.
Das ist gefaehrlich: wer den Docstring liest, vertraut auf eine Garantie,
die nicht existiert. Entweder implementieren oder den Docstring korrigieren.

**Entscheidung noetig:**
- Soll der `PolicyToolExecutionGuard` die aktive SOP und ihre `safe_actions`
  tatsaechlich als deterministische Allowlist nutzen?
- Wenn ja: Wie kommt die aktive SOP in den Guard?
  (Aktuell kennt der Guard den `PlanningContext` nicht.)

### F4: Memory/Notebook-API finalisieren

**Warum zuerst:** Die aktuellen Tool-Namen (`memory.write_document`, `notebook.task`,
`notebook.sop`) weichen stark vom dokumentierten Zielmodell ab. Je laenger
beide Varianten nebeneinander existieren, desto mehr Tests und SOPs entstehen
auf der alten API, die spaeter migriert werden muessen.

**Entscheidung noetig:**
- Direkt auf die Ziel-API (`memory.write(scope, content)` + `notebook.work/audit`)
  migrieren? Oder die bestehenden Tools vorerst beibehalten und nur umbenennen?
- Wann wird `memory.search` (Unterbewusstsein) implementiert?

### F5: Prioritaet und Reihenfolge

Empfohlene Reihenfolge:

1. **F1 (SOP-Resolver)** -- schaltet die Harness-Dynamik frei, ohne bestehenden
   Code zu brechen. Kann parallel zu allem anderen laufen.
2. **F3 (safe_actions)** -- kleiner Fix mit grosser Wirkung. Entweder implementieren
   oder den Docstring korrigieren, damit keine falschen Sicherheitsannahmen entstehen.
3. **F4 (Memory/Notebook-API)** -- Voraussetzung fuer saubere SOP-Notebook-Integration.
4. **F2 (Input/Output-Guards)** -- wichtig fuer Enterprise, aber nicht blockierend
   fuer die Testphase.

### Was NICHT Fundament ist

Diese Dinge koennen warten, ohne das Fundament zu gefaehrden:

- **Dreaming / Pre-Compaction-Flush** -- Feature, kein Fundament.
- **Konvergenz-API (remember/forget/recall)** -- Designziel, nicht blockierend.
- **Generelles Sandboxing** -- Docker-Workstation reicht fuer den MVP.
  Generelles Sandboxing ist ein Infrastruktur-Thema, kein Architektur-Thema.
- **Skill-Resolver** -- erst relevant, wenn SOPs mit Skills tatsaechlich genutzt werden.
