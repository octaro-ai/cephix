# Cephix

**Der erste digitale Roboter.**

---

Physische Roboter haben die Fertigung transformiert. Digitale Roboter werden alles andere transformieren.

Jedes Unternehmen hat diesen einen Mitarbeiter. Den, der jeden Prozess kennt. Der immer verfuegbar ist. Der um 3 Uhr nachts noch die richtige Antwort hat. Der sich an alles erinnert, was letzten Dienstag besprochen wurde.

Cephix baut genau diesen Mitarbeiter -- nur digital, beliebig skalierbar und vollstaendig unter Ihrer Kontrolle.

Das ist die naheliegendste Anwendung. Bei weitem die einzige ist sie davon noch lange.

Ein digitaler Roboter ist jede autonome Einheit, die **wahrnehmen, planen, handeln, lernen und sich verantworten** kann. Dieselbe Architektur, die einen digitalen Kundenservice-Mitarbeiter antreibt, kann einen Sentinel antreiben, der in Ihren Log-Stroemen lebt, Anomalien erkennt und Infrastruktur heilt -- anstelle von starren Alerting-Regeln, die bei jeder Systemaenderung brechen. Sie kann einen autonomen Crawler antreiben, der APIs und Websites traversiert, sich an Layout-Aenderungen anpasst und semantische Entscheidungen trifft, was relevant ist -- anstelle von handgepflegten Scrapern, die innerhalb von Wochen veralten. Sie kann in einen physischen Koerper bruecken und Aktoren ueber denselben Tool/Skill/SOP-Stack steuern. Sie kann in einer Datenpipeline sitzen und kontextbewusste Routing- und Qualitaetsentscheidungen treffen -- anstelle von starren ETL-Skripten, die nur den Happy Path abdecken.

Ueberall dort, wo heute deterministische Algorithmen eingesetzt werden, weil Menschen zu langsam oder zu teuer sind, das Problem aber tatsaechlich **semantische Komplexitaet** hat, die starrer Code schlecht bewaeltigt -- genau dort gehoert ein digitaler Roboter hin.

[Octaro](https://octaro.de) baut das Unternehmen rund um diesen Insight. Der digitale Mitarbeiter -- fuer Kundenservice, Vertrieb, Onboarding, Marketing und interne Prozesse -- ist die erste Applikation auf der Plattform. Infrastruktur-Sentinels, autonome Crawler, Datenstrom-Operatoren und Cyber-Physical Bridges sind der naechste Schritt. Open Source, DSGVO-konform, in Europa gehostet.

Cephix ist der erste Prototyp, der zeigt, wie all das aussieht -- gebaut, lauffaehig und bereit zu lernen.

> *We are building the new digital robot industry.*

---

## Das Problem

Jeder kann heute in 20 Minuten einen beeindruckenden KI-Demo bauen. Prompt rein, Antwort raus, fertig.

Aber zwischen einer Demo und einem produktiven digitalen Mitarbeiter liegt ein Ozean. Ein LLM ist hervorragend darin, Sprache zu verstehen, naechste Schritte abzuleiten und Informationen zusammenzufassen. Doch ein produktiver Mitarbeiter braucht mehr: zuverlaessige Zustandsverwaltung, deterministische Ausfuehrungskontrolle, Auditing, Governance und ein Gedaechtnis, das ueber Zeit waechst.

Die Frameworks, die das loesen wollen -- OpenClaw, nanobot, OpenFang und viele andere -- teilen alle denselben strukturellen Fehler: **Der Agent ist mit seiner Umgebung verheiratet.** Seine Faehigkeiten sind fest an die Integrationen der Plattform gebunden. Sein Gedaechtnis lebt im Host. Seine Identitaet ist untrennbar vom Deployment. Man kann das Gelernte, die Skills, die Arbeitsanweisungen eines Agenten nicht herausnehmen und in einer anderen Runtime laufen lassen. Man kann einen Agenten nicht aus standardisierten, unabhaengigen Teilen zusammensetzen. Der Agent existiert nicht als eigenstaendige Einheit -- er ist eine Konfiguration seines Host-Systems.

Das ist das Geraete-Modell. Ein Thermomix kann kochen, aber nur mit Thermomix-Rezepten, nur auf einer Thermomix-Arbeitsplatte, und nur das, was Thermomix vorgesehen hat.

Cephix folgt dem Industrieroboter-Modell. Man tauscht den Werkzeugkopf. Man laedt ein anderes Programm. Man folgt einer anderen Arbeitsanweisung. Gleicher Roboter, voellig andere Faehigkeit. Und das Gehirn des Roboters -- seine Firmware, sein Gelerntes, sein Gedaechtnis -- reist mit ihm.

## Der Kern-Insight

Ein physischer Roboter in einer Fabrik besteht aus drei Dingen: einem **Werkzeug** (der Schweisskopf), einem **Programm** (das Schweissprogramm) und einer **Arbeitsanweisung** (die SOP). Das Zusammenspiel dieser drei macht die Faehigkeit aus. Die Faehigkeit selbst ist emergent.

Cephix uebertraegt genau dieses Prinzip auf digitale Arbeit:

| Physischer Roboter | Digitaler Roboter (Cephix) |
|--------------------|----------------------------|
| Werkzeug am Arm | **Tool** -- atomare Aktion (`mail.list`, `crm.search`) |
| Erlerntes Programm | **Skill** -- Instruktionen + deklarierte Tools |
| Arbeitsanweisung | **SOP** -- Decision Graph, der den Workflow steuert |
| Steuerungseinheit | **Executive Kernel** -- deterministischer Zustandsautomat |
| Sicherheitskaefig | **Governance** -- Guards an jeder Systemgrenze |
| Wartungsprotokoll | **Telemetrie** -- jede Aktion als Wide Event |
| Erfahrungsspeicher | **Memory** -- vierschichtiges Gedaechtnis |

Jede Komponente ist ein austauschbarer Port. Wie bei einem echten Roboter: Standardteile, zusammengesteckt.

---

## So sieht das in Aktion aus

Ein Nutzer schreibt via Telegram:

> "Was ist neu in meinem Postkorb?"

Was passiert:

1. Der **Gateway** empfaengt die Nachricht und normiert sie als `RobotEvent`
2. Der **SOPResolver** erkennt "postkorb" und aktiviert `postkorb.check.v2`
3. Der **SkillResolver** laedt den Skill `email-reading`
4. Die **ToolRegistry** mountet exakt `mail.list` und `mail.read`
5. Die **Memory Layer** baut den relevanten Kontext fuer diesen Nutzer
6. Der **Planner** (LLM) erstellt den Plan: `mail.list_new_messages(limit=10)`
7. Der **GovernedToolExecutor** prueft Guards und fuehrt das Tool aus
8. Der **Planner** erhaelt die Ergebnisse und formuliert die Summary
9. Die Antwort geht ueber den **Telegram Gateway** zurueck
10. Das **Memory** aktualisiert sich, jeder Schritt liegt als **Wide Event** vor

Der Nutzer erhaelt: Anzahl neuer Nachrichten, Betreff, Absender, Kurzfassung, optionaler Drilldown.

Das Entscheidende: Das LLM hat zu jedem Zeitpunkt exakt die Tools gesehen, die der aktuelle Workflow-Schritt vorsah. Die Governance hat jede Ausfuehrung geprueft. Der Kernel hat den gesamten Ablauf deterministisch gesteuert. Und alles ist auditierbar protokolliert.

---

## Architektur

### Die zentrale Design-Entscheidung

Cephix gibt jedem Baustein genau die Verantwortung, die zu ihm passt:

- **Das LLM entscheidet**, was semantisch sinnvoll ist -- als Planner, Reasoner und Summarizer.
- **Der Kernel entscheidet**, wann was ausgefuehrt werden darf.
- **Die Tool-Schicht entscheidet**, wie auf die Welt zugegriffen wird.
- **Die Governance entscheidet**, ob eine Aktion erlaubt ist.
- **Die Telemetrie haelt fest**, was tatsaechlich passiert ist.
- **Das Memory sorgt dafuer**, dass der Roboter ueber Zeit besser wird.

Das ist die saubere Trennung von **Probabilistik und Determinismus**: Das LLM bleibt stark dort, wo es stark ist. Die Systemverantwortung liegt in deterministischen Komponenten.

### Executive Kernel

Ein deterministischer Zustandsautomat. Jeder Run durchlaeuft:

```
IDLE -> OBSERVING -> PLANNING -> ACTING -> FINALIZING -> RESPONDING -> DONE
```

Das LLM schlaegt vor. Der Kernel entscheidet, kontrolliert und protokolliert.

### Zwei Loops

**Aeussere Runtime Loop** -- haelt das System am Leben, verursacht im Idle-Zustand null Kosten, startet genau dann einen Run, wenn ein Event eintrifft (**Zero Cost Idle**).

**Innere Run Loop** -- verarbeitet genau einen Arbeitsfall: Input -> Memory -> Plan -> Tools -> Revision -> Antwort -> Delivery -> Events.

### SOP-getriebenes Tool-Mounting

Die SOP bestimmt, was geladen wird. Das LLM bekommt exakt die Tools, die der aktuelle Workflow vorsieht:

```
Event eintrifft
  -> SOPResolver bestimmt: "postkorb.check.v2"
  -> SOP definiert: required_skills=["email-reading"], required_tools=["mail.list", "mail.read"]
  -> SkillResolver laedt Skill-Instruktionen
  -> ToolRegistry mountet genau diese Tools
  -> LLM sieht exakt die montierten Tools
  -> SOPNavigator schraenkt pro Schritt weiter ein
```

Das ist **Progressive Disclosure** -- der Roboter hat immer genau den Fokus, den er braucht.

### Governance: Guards an drei Grenzen

Sicherheit ist von Anfang an eingebaut -- als transparenter Decorator an jeder Systemgrenze:

| Grenze | Guard | Beispiele |
|--------|-------|-----------|
| **Eingang** | `InputGuardPort` | PII-Erkennung, Prompt-Injection-Schutz |
| **Ausfuehrung** | `ToolExecutionGuardPort` | ACL, Rate Limiting, Circuit Breaker |
| **Ausgang** | `OutputGuardPort` | Content-Policy, Response-Sanitization |

Composite-Pattern: eine Liste von Guards pro Grenze, sequenziell durchlaufen, erster Deny stoppt. Leere Liste = alles erlaubt (Prototyp-Modus).

```python
robot = DigitalRobot(
    ...
    input_guard=CompositeInputGuard([
        PiiDetectionGuard(),
        PromptInjectionGuard(policy=strict_policy),
    ]),
    tool_execution_guard=CompositeToolExecutionGuard([
        AclGuard(permissions=user_permissions),
        RateLimitGuard(limits=rate_config),
    ]),
    output_guard=CompositeOutputGuard([
        ContentPolicyGuard(rules=robot_rules),
    ]),
)
```

### Sieben Bausteine

| Baustein | Aufgabe |
|----------|---------|
| **Gateway Layer** | Vereinheitlicht Ein-/Ausgabekanaele (Telegram, E-Mail, Webhooks, UI) zu `RobotEvent` / `OutboundMessage` |
| **Semantic Bus** | Interne Nervenbahn (`event`, `query`, `command`, `action_result`) |
| **Executive Kernel** | Deterministische State Machine fuer einen Run |
| **Planner / Reasoner** | LLM-basiert: interpretiert, plant, reagiert, formuliert. Ausfuehrung bleibt beim Kernel |
| **Tool-Schicht** | Kapselt Weltzugriffe (APIs, Datenbanken, E-Mail, CRM). MCS-Treiber mountbar via Adapter |
| **Memory Layer** | Baut Kontext pro Run, aktualisiert Gedaechtnis nach Abschluss |
| **Telemetry** | Jede Aktion als Wide Event -- Grundlage fuer Auditing, Replay, Debugging, Distillation |

### ROS-inspiriert

Die Architektur folgt dem gleichen Prinzip wie ROS in der physischen Robotik -- auf einer hoeheren Abstraktionsebene, zugeschnitten auf digitale Arbeit: klare Kommunikationssemantik, Trennung von Zustandsmaschine und Kommunikation, Trennung zwischen Wahrnehmung, Handlung und Ausfuehrung, Event-getriebener Lebenszyklus. Konsequent **SOLID**, konsequent **Dependency Inversion**.

---

## Memory: Das Gedaechtnis des Roboters

Der Roboter transformiert Erfahrungen ueber Zeit in verschiedene Gedaechtnisformen -- von rohen Events bis zu stabilen, versionierten Arbeitsweisen.

### Vier Schichten

| Schicht | Funktion | Beispiele |
|---------|----------|-----------|
| **Working Memory** | Temporaerer Laufzeitkontext fuer einen Run | Aktueller Plan, Tool-Ergebnisse, Zwischenstaende |
| **Episodic Memory** | Verdichtete abgeschlossene Arbeitsfaelle | "Postkorb -> 3 Mails gelesen -> Summary gesendet" |
| **Profile Memory** | Stabile Nutzerfakten mit Confidence und Evidence | "bevorzugt knappe Antworten", "Lieblingskanal: Telegram" |
| **Procedural Memory** | Gelernte, versionierte Arbeitsweisen | `mail-summary.concise.v1`, `postkorb.check.v2` |

### Memory-Pipeline

```
Event Store (append-only Rohwahrheit)
  -> Episode Builder (verdichtet Events zu Episoden)
  -> Memory Distiller (erzeugt Profile-Facts + Procedure-Kandidaten)
  -> Profile Store (stabile Nutzerfakten)
  -> Procedure Store (benannte, versionierte Procedures)
  -> Memory Builder (laedt pro Run den relevanten Kontext)
```

Der Event Store ist die primaere Wahrheitsquelle. Die Chathistorie ist eine **Projektion** auf einen Teil davon. Ein **Procedure Resolver** waehlt pro Run gezielt die relevanten Arbeitsweisen aus -- passive Richtlinien oder formale SOP-/DAG-Bausteine.

---

## MCS-Integration

MCS (Model Context Standard) passt direkt in die Tool-Schicht. Der Fokus der Standardisierung liegt auf den **Systemgrenzen**: Interfaces, Tool-Vertraege, Discovery, Ausfuehrungsgrenzen und Telemetrie. Internes Reasoning, Prompt-Format, Memory-Backend und Planungsstrategie bleiben bewusst offen.

Der **MCS-Adapter** wickelt MCS-Treiber in Cephix-Ports -- jeder Treiber wird mit Namespace automatisch als Tool gemountet und ist sofort durch die Governance geschuetzt.

---

## Wide Events

Jede Aktion wird als strukturiertes Wide Event gespeichert (`event_id`, `event_type`, `timestamp`, `run_id`, `trace_id`, `robot_id`, `conversation_id`, `actor`, `payload`). Die Grundlage fuer Auditing, Debugging, Analyse, Memory Distillation, Replay und Observability.

```
input.received -> memory.context_loaded -> plan.created -> tool.requested ->
tool.completed -> plan.revised -> response.created -> memory.updated ->
output.sent -> run.completed
```

---

## Modulstruktur

```
src/
  tools/                    # Tool-Schicht (Aktoren)
    models.py               ports.py            registry.py
    executor.py             file_catalog.py     mcs_adapter.py
    write_ports.py
  skills/                   # Skill-Schicht (Erlerntes)
    models.py               ports.py            file_repo.py
    resolver.py             cache.py
  sop/                      # SOP-Schicht (Arbeitsanweisungen)
    models.py               ports.py            file_repo.py
    resolver.py             navigator.py        compiler.py
  governance/               # Governance (Guards)
    models.py               ports.py            composite.py
    guards/
  runtime/                  # Kernel + Event Loop
    kernel.py               event_loop.py
  planners/                 # Planner-Implementierungen
  memory/                   # Memory-Stack
  gateways/                 # Channel-Adapter (Telegram, WebSocket)
  domain.py                 # Domain Objects
  ports.py                  # Port-Definitionen (Protocols)
  context.py                # ContextAssembler
  robot.py                  # DigitalRobot Aggregat
  service.py                # RobotService Lifecycle
  toolbuilder.py            # ToolBuilder-Robot
  telemetry.py              # Wide Event Logging
  bus.py                    # Semantic Bus
  app.py                    # Composition Root
```

---

## Schnellstart

```powershell
# Demo
python cephix-drp.py

# Robot-Service starten
python -m src serve --host 127.0.0.1 --port 8765

# Chat-Client verbinden (separater Prozess, optional mit Live-Telemetrie)
python -m src chat --url ws://127.0.0.1:8765/ws --debug

# Tests
python -m pytest tests/ -v
```

---

## Prinzipien

1. **Ports & Protocols** -- Der Kernel kennt ausschliesslich Abstraktionen.
2. **Constructor Injection** -- Alles wird von aussen zusammengesteckt.
3. **Determinismus im Kern** -- Das LLM beraet. Der Kernel entscheidet.
4. **Progressive Disclosure** -- SOPs bestimmen den Fokus. Das LLM sieht genau das, was es braucht.
5. **Governance by Design** -- Guards sind Decorators, eingebaut von Anfang an.
6. **Zero Cost Idle** -- Im Leerlauf ruht das System vollstaendig.
7. **Portable Brain** -- Firmware und Wissen lassen sich unabhaengig exportieren und klonen.
8. **Gross denken, einfach beginnen** -- Die Architektur traegt grosse Systeme und startet als robuster Monolith mit sauberen Grenzen.

---

## Leitsatz

> Der Kernel sagt, **wann** etwas passieren darf.
> Das LLM sagt, **was** als Naechstes sinnvoll ist.
> Das Tool sagt, **wie** die Welt veraendert oder gelesen wird.
> Die Governance sagt, **ob** eine Aktion erlaubt ist.
> Die Telemetrie sagt, **was tatsaechlich passiert ist**.
> Das Memory sorgt dafuer, dass der Roboter **ueber Zeit besser wird**.
