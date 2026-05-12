# Cephix als Harness

> **Was ist ein Harness?** Der deterministische Rahmen, der entscheidet,
> welches LLM mit welchem Kontext, welchen Tools und welchen Grenzen
> arbeiten darf. Das LLM ist nicht der Harness -- es ist ein
> austauschbarer Planner innerhalb des Harness.

## Fuenf tragende Schichten

```
  Event rein
      |
  [1. RUNTIME]         Aeussere Schleife, Kernel, Event-Vertrag
      |
  [2. CONTEXT]         Firmware, Memory, SOPs, Notebooks, Tool-Mounting
      |
  [3. PLANNER]         LLM-Prompt-Zusammenbau, Plan-Erzeugung
      |
  [4. GOVERNANCE]      Risk-Klassifizierung, Approval, Guard-Entscheidung
      |
  [5. AUDIT]           Telemetry (WideEvents), Approval-Store, Logs
      |
  Wirkung auf die Welt
```

### 1. Runtime -- der aeussere Rahmen

Nimmt Events entgegen, haelt den Kernel am Leben, liefert Antworten aus.

- **RobotEvent** ist der einzige Eingang in den Harness.
- **RuntimeEventLoop** pollt die Queue und ruft `kernel.handle_event`.
- **DigitalRobotKernel** fuehrt deterministisch Observe -> Plan -> Execute -> Respond aus.
- **DigitalRobot** ist die Composition-Facade: baut Kernel, Runtime und ControlPlane aus injizierten Ports.

### 2. Context -- der dynamische Harness-Kontext

Bestimmt, **was** der Planner sieht und **welche Tools** er bedienen darf.

- **Firmware** (`AGENTS.md`, `POLICY.md`, `CONSTITUTION.md`) -- unveraenderliche Leitplanken, immer geladen.
- **Memory-Dokumente** (`IDENTITY.md`, `USER.md`, `MEMORY.md`, `BOOTSTRAP.md`) -- globales Robot-Wissen, immer geladen.
- **Memory-Context** (Fakten, `conversation_summary`, `recent_interactions`) -- strukturiertes Wissen aus dem Memory-Store.
- **SOPs** -- Arbeitsanweisungen, die der Context-Assembler nach Event-Match laedt. Bestimmen `required_tools` und `required_skills`.
- **Notebooks** -- artefaktgebundene Notizen (`work` / `audit`), geladen wenn das zugehoerige Artefakt aktiv ist.
- **Tool-Mounting** -- der Context-Assembler entscheidet nach `AutonomyLevel`, welche Tools dem Planner zur Verfuegung stehen:
  - SCRIPTED: nur SOP-Tools
  - GUIDED: SOP-Tools + System-Tools
  - AUTONOMOUS: SOP-Tools oder (ohne SOP) voller Katalog + System-Tools
  - CREATIVE: wie AUTONOMOUS, plus `procedure.propose`

Das Ergebnis ist ein **PlanningContext** -- das komplette Weltbild fuer den Planner.

### 3. Planner -- die LLM-Schicht

Macht aus dem PlanningContext einen konkreten Plan.

- **PlannerPort** ist die austauschbare Schnittstelle.
- **LLMPlanner** baut den System-Prompt aus Firmware + Memory + SOPs + Skills + Notebooks + Governance-Hinweisen.
- Das Ergebnis ist ein **Plan** mit **PlanSteps** (`tool_call` oder `finalize`).
- Der Planner kann nach Tool-Ergebnissen revidieren (`revise_plan_after_tool`).

### 4. Governance -- deterministische Grenzen

Prueft **vor** jeder Tool-Ausfuehrung, ob die Aktion erlaubt ist.

- **GovernedToolExecutor** fragt den Guard, bevor er an den ToolCollector delegiert.
- **PolicyToolExecutionGuard** entscheidet nach:
  1. `RiskClass` (read_only -> erlaubt)
  2. System-Tool-Flag -> erlaubt
  3. Bestehende Approval-Regel -> erlaubt oder verweigert
  4. Sonst -> `approval_required`
- **ApprovalStore** speichert User-Entscheidungen (Einmal / Immer / Nie).
- **ActorResolver** bestimmt die Rolle des Absenders (principal, delegate, counterparty).
- Der Kernel verarbeitet `approval.decision`-Events deterministisch, ohne LLM.

### 5. Audit -- nachvollziehbare Runs

Jeder Schritt erzeugt strukturierte Telemetrie.

- **WideEvent** ist das einheitliche Audit-Format (JSONL).
- **Telemetry** schreibt ueber austauschbare **EventSinkPorts** (EventLog, LoggingSink, FanoutSink).
- Events: `input.received`, `memory.context_loaded`, `tools.mounted`, `plan.created`,
  `tool.requested`, `tool.completed`, `plan.revised`, `response.created`,
  `approval.prompt_sent`, `approval.granted`, `approval.denied`,
  `run.completed`, `run.failed`.
- Memory-Interaktionen und Notebook-Eintraege werden persistiert und fliessen im naechsten Run zurueck in den Context.

## Warum das kein "persoenlicher Agent" ist

Ein persoenlicher Agent (Hermes, OpenClaw) bindet Firmware, Memory, Tools und
Governance in einem monolithischen Prompt zusammen. Der Harness ist fest.

Cephix trennt diese Belange in Schichten mit Ports:

- Gleicher Kernel, anderer Planner? Port tauschen.
- Gleicher Planner, andere Tools? Context-Assembler konfigurieren.
- Gleiche Tools, andere Governance? Guard austauschen.
- Gleicher Guard, anderes Audit? EventSink tauschen.

Die Kombination aus SOPs, AutonomyLevel und Tool-Mounting macht den Harness
**dynamisch**: derselbe Roboter passt sich je nach Event an, statt fuer
jeden Anwendungsfall einen neuen Agenten zu bauen.
