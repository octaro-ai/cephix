# Digital Robot Prototype

Ein erster Architekturentwurf für einen **digitalen Roboter**: eine autonome, telemetrierbare, konfigurierbare digitale Arbeitseinheit mit standardisierten Schnittstellen für Wahrnehmung, Handlung, interne Routinen und externe Kontrolle.

Der Fokus liegt nicht auf einem Chatbot oder einem klassischen Agenten-Loop, sondern auf einer Architektur, in der:

- das **LLM** die Rolle eines **Planner / Reasoner Node** übernimmt,
- ein **deterministischer Executive Kernel** die operative Kontrolle behält,
- ein **Capability Layer** die Weltzugriffe kapselt,
- ein **Gateway Layer** Eingabe- und Ausgabekanäle vereinheitlicht,
- ein **Semantic Bus** als interne Nervenbahn dient,
- ein **Telemetry / Event System** alle Vorgänge als Wide Events protokolliert,
- und ein mehrschichtiges **Memory-System** langfristiges Lernen ermöglicht.

---

## Zielbild

Der digitale Roboter ist keine lose Tool-Calling-Schleife und kein reiner Chat-Assistent.

Er ist eine **persistente operative Einheit**, die:

- über digitale Sensoren und Aktoren mit ihrer Umwelt interagiert,
- Workflows schrittweise ausführt,
- Zustände kennt,
- auf Ereignisse reagieren kann,
- Verhalten über Zeit verbessern kann,
- und alle relevanten Entscheidungen und Aktionen nachvollziehbar protokolliert.

Das System soll bewusst **einfach beginnen**, aber so geschnitten sein, dass später größere Architekturen möglich bleiben.

---

## Grundentscheidungen

### 1. Das LLM ist nicht der exekutive Kernel

Das LLM ist in dieser Architektur **nicht** die Runtime selbst.

Es übernimmt stattdessen die Rolle eines:

- **Planner**
- **Reasoner**
- **Summarizers**
- **Candidate Generator** für nächste sinnvolle Schritte

Warum?

Ein LLM ist sehr gut darin,

- unstrukturierte Anfragen zu interpretieren,
- semantische nächste Schritte abzuleiten,
- Informationen zusammenzufassen,
- unscharfe Situationen zu handhaben.

Aber ein LLM ist nicht ideal dafür,

- Zustandswechsel robust zu verwalten,
- Tool-Ausführung deterministisch zu kontrollieren,
- Timeouts / Retries / Fehlerzustände sauber zu modellieren,
- Auditing und Governance zu garantieren.

Deshalb gilt:

- Das **LLM entscheidet**, was semantisch sinnvoll ist.
- Der **Kernel entscheidet**, wann was ausgeführt werden darf.
- Die **Capability Layer** entscheidet, wie auf die Welt zugegriffen wird.
- Die **Telemetry** hält fest, was wirklich passiert ist.

---

### 2. Der Core ist ein deterministischer Executive Kernel

Der Kernel ist die operative Laufzeit eines einzelnen Runs.

Er kennt klar definierte Zustände wie zum Beispiel:

- `IDLE`
- `OBSERVING`
- `PLANNING`
- `ACTING`
- `FINALIZING`
- `RESPONDING`
- `DONE`
- `ERROR`

Der Kernel ist bewusst **deterministisch** und zustandsbasiert.

Er ist damit näher an einem klassischen Robotik- oder Workflow-System als an einer offenen Agentenschleife.

---

### 3. Es gibt zwei Loops

#### Äußere Runtime Loop

Diese Loop hält das System am Leben.

- Sie wartet auf Eingänge oder Events.
- Sie verursacht im Idle-Zustand praktisch keine Kosten.
- Sie startet nur dann einen neuen Run, wenn ein neues Event eintrifft.

Das ist die Grundlage für eine **Zero Cost Idle Loop**.

#### Innere Run Loop

Diese Loop verarbeitet genau einen Arbeitsfall:

- Input empfangen
- Memory laden
- Plan erstellen
- Capabilities ausführen
- Plan überarbeiten
- Antwort erzeugen
- Antwort senden
- Events protokollieren

---

## Architekturüberblick

Die Architektur ist bewusst **modular**, aber zunächst als **einfacher Python-Prototyp** gedacht.

### Architekturbausteine

#### 1. Gateway Layer

Vereinheitlicht Ein- und Ausgabekanäle.

Beispiele:

- Telegram
- E-Mail
- Telefontranskripte
- Webhooks
- UI

Der Gateway transformiert rohe Eingänge in ein einheitliches `RobotInput`.

#### 2. Semantic Bus

Ein interner Bus für semantische Nachrichten.

Er dient als Nervenbahn zwischen den Komponenten.

Im Prototypen ist er noch sehr einfach und speichert Nachrichten nur in Memory.

Langfristig kann daraus eine robustere Event-/Message-basierte Struktur entstehen.

Mögliche Nachrichtentypen:

- `event`
- `query`
- `command`
- `action_result`

#### 3. Executive Kernel

Die operative State Machine des Roboters.

Sie orchestriert einen einzelnen Run und bleibt deterministisch.

#### 4. Planner / Reasoner Node

Das LLM-basierte Modul.

Es:

- interpretiert die Nutzeranfrage,
- plant nächste Schritte,
- reagiert auf Capability-Ergebnisse,
- formuliert die finale Antwort.

Es führt keine Tools selbst aus.

#### 5. Capability Layer

Kapselt Weltzugriffe.

Beispiele:

- Mails lesen
- Dateien lesen/schreiben
- APIs aufrufen
- Nachrichten versenden
- ERP-/CRM-/Datenbankzugriffe

Diese Schicht soll später sauber mit **MCS** bzw. MCS-artigen Treibern gekoppelt werden.

#### 6. Memory Layer

Baut kontextrelevantes Wissen für einen Run auf und aktualisiert das Gedächtnis nach Abschluss.

#### 7. Telemetry / Event Logging

Jede relevante Aktion wird als **Wide Event** gespeichert.

Das ist die Grundlage für:

- Auditing
- Replay
- Debugging
- Monitoring
- Distillation in Memory

---

## ROS-Gedanke, aber höher abstrahiert

Die Architektur ist **ROS-inspiriert**, aber bewusst **nicht** ROS 2 selbst.

ROS 2 ist für diesen Prototypen zu fein granular und zu stark an reale Robotik und deren Infrastruktur gebunden.

Was wir übernehmen, ist das Prinzip:

- klare Kommunikationssemantik,
- Trennung von Zustandsmaschine und Kommunikation,
- Trennung zwischen Wahrnehmung, Handlung und Ausführung,
- Event-getriebener Lebenszyklus.

Die Architektur ist damit eher ein **höher abstrahiertes ROS-Prinzip für digitale Arbeit**.

---

## MCS-Einordnung

MCS ist für dieses Konzept besonders interessant, weil es bereits die richtige Richtung vorgibt:

- standardisierte Interfaces,
- austauschbare Treiber,
- transparente Capability-Ausführung,
- Orchestrierung / Tool-Kuratierung,
- Security / Approval / Auditing auf einer separaten Ebene.

Wichtig ist:

- Nicht das interne Denken des LLM wird standardisiert.
- Nicht die Promptform wird standardisiert.
- Nicht das konkrete Memory-Backend wird standardisiert.

Standardisiert werden stattdessen:

- Interfaces
- Capability-Verträge
- Sichtbarkeit / Discovery
- Ausführungsgrenzen
- Telemetrie

Das passt direkt zur gewünschten Architektur eines digitalen Roboters.

---

## Memory-Modell

Memory ist hier **kein einzelner Speicher**, sondern eine mehrschichtige Architektur.

### Grundidee

Der Roboter soll nicht einfach nur vergangene Chats wiederverwenden.

Er soll Erfahrungen über Zeit in verschiedene Gedächtnisformen transformieren.

### 1. Working Memory

Temporäres Laufzeitgedächtnis für einen einzelnen Run.

Beispiele:

- aktueller Plan
- letzte Capability-Ergebnisse
- Zwischenstände
- offene Fragen

### 2. Episodic Memory

Verdichtete abgeschlossene Interaktionen oder Arbeitsfälle.

Beispiel:

> Nutzer fragt nach neuen Nachrichten im Postkorb → Roboter liest drei neue Mails → Roboter fasst sie zusammen → Antwort wird über Telegram gesendet.

Episodic Memory ist **nicht** der rohe Event Stream, sondern eine verdichtete Sicht darauf.

### 3. Profile Memory

Stabile Präferenzen, Fakten und langfristige Muster eines Nutzers.

Beispiele:

- bevorzugt knappe Antworten
- mag Zusammenfassungen
- bevorzugter Kanal: Telegram
- wiederkehrende Arbeitsprioritäten

Profile Memory sollte restriktiv gepflegt werden und idealerweise mit:

- Confidence
- Evidence
- Zeitstempel
- möglicher Bestätigung

arbeiten.

### 4. Procedural Memory

Gelernte Arbeitsweisen, Regeln, SOP-Hinweise oder wiederverwendbare Procedures.

Beispiele:

- `mail-summary.concise.v1`
- `postkorb.check-and-summarize.v2`
- `executive-briefing.short.v1`

Diese Procedures sollen:

- eindeutig benannt sein,
- eine Kurzbeschreibung haben,
- versionierbar sein,
- bei Bedarf geladen und wieder entfernt werden können.

### Wichtige Trennung

#### Event Store

Die Rohwahrheit. Append-only.

Er enthält alle Events, nicht nur Chat-Historie.

Beispiele:

- Input empfangen
- Plan erstellt
- Capability angefordert
- Capability abgeschlossen
- Antwort gesendet
- Memory geladen
- Memory aktualisiert
- Fehler / Abbruch / Retry

#### Episode Store

Verdichtete abgeschlossene Arbeitsfälle, abgeleitet aus dem Event Store.

#### Profile Store

Verdichtete stabile Nutzerfakten.

#### Procedure Store

Bibliothek wiederverwendbarer Procedures.

### Wichtig

Die Chathistorie ist **nicht** der Event Store.

Sie ist nur eine **Projektion** auf einen Teil der Events.

---

## Memory-Pipeline

Ein sinnvoller Fluss ist:

1. **Event Store** als append-only Rohquelle
2. **Episode Builder** verdichtet Events zu Episoden
3. **Memory Distiller** erzeugt daraus:
   - Profile-Facts
   - Procedure-Kandidaten
4. **Profile Store** speichert stabile Nutzerfakten
5. **Procedure Store** speichert benannte, versionierte Procedures
6. **Memory Builder** lädt für einen Run nur den relevanten Kontext

---

## Procedure-Handling

Procedures dürfen langfristig sehr zahlreich sein.

Aber:

- nicht alle Procedures sollen gleichzeitig im Kontext liegen,
- das LLM soll nicht blind alle Procedures laden,
- stattdessen sollte ein **Procedure Resolver** entscheiden, welche für einen Run relevant sind.

### Zwei mögliche Procedure-Arten

#### Passive Procedures

Nur Richtlinien oder Leitplanken.

Beispiel:

- `mail-summary.concise.v1`
- Kurzbeschreibung: „Fasse neue E-Mails knapp zusammen und biete einen Drilldown an.“

#### Executable Procedures

Formalere SOP-/Behavior-Tree-Bausteine.

Beispiel:

- `postkorb.check-and-summarize.v2`
- Kurzbeschreibung: „Lies neue Nachrichten, bewerte Relevanz, fasse sie im bevorzugten Stil zusammen und sende die Antwort über den Ursprungskanal.“

---

## Telegram/Postkorb-Beispiel

### Anfrage

Ein Nutzer schreibt via Telegram:

> „Was ist neu in meinem Postkorb?“

### Ablauf

1. Der **Gateway** empfängt die Telegram-Nachricht.
2. Der Eingang wird als `RobotInput` normiert.
3. Die äußere Runtime Loop startet einen neuen Run.
4. Der **Kernel** wechselt in `OBSERVING`.
5. Ein `input.received` Event wird geschrieben.
6. Die **Memory Layer** baut einen `memory_context` für Nutzer und Konversation.
7. Der **Planner** erzeugt einen Plan:
   - `mail.list_new_messages(limit=10)`
8. Der **Kernel** wechselt in `ACTING`.
9. Die **Capability Layer** liest neue Nachrichten.
10. Das Ergebnis wird als `capability.completed` geloggt.
11. Der **Planner** wird erneut aufgerufen und erstellt nun die Summary.
12. Der **Kernel** wechselt in `FINALIZING`.
13. Die Antwort wird über den **Telegram Gateway** zurückgeschickt.
14. Die Interaktion wird im Memory aktualisiert.
15. Alle Schritte werden als **Wide Events** gespeichert.

### Ergebnis

Der Nutzer erhält z. B.:

- Anzahl neuer Nachrichten
- Betreff
- Absender
- Kurzfassung
- optionaler Hinweis auf Drilldown

---

## Wide Events

Jede relevante Aktion wird als strukturiertes Event gespeichert.

### Ziel

Die Events sollen später nicht nur Logging sein, sondern Grundlage für:

- Auditing
- Debugging
- Analyse
- Memory Distillation
- Replay
- Observability

### Beispielhafte Event-Typen

- `input.received`
- `memory.context_loaded`
- `plan.created`
- `capability.requested`
- `capability.completed`
- `plan.revised`
- `response.created`
- `memory.updated`
- `output.sent`
- `run.completed`
- `run.failed`

### Beispielstruktur

Ein Wide Event enthält typischerweise:

- `event_id`
- `event_type`
- `timestamp`
- `run_id`
- `trace_id`
- `robot_id`
- `conversation_id`
- `actor`
- `payload`

Im Prototypen werden diese Events zunächst in `robot_events.jsonl` gespeichert.

---

## Prototyp-Stand heute

Die aktuelle Python-Datei demonstriert bereits:

- normierte Inputs (`RobotInput`)
- `ExecutionContext`
- einen einfachen `SemanticBus`
- einen `DigitalRobotKernel`
- einen `RuntimeEventLoop`
- einen `TelegramGateway`
- eine einfache `FakeMailCapability`
- einen `LLMPlanner`
- einen ersten `InMemoryMemoryStore`
- `Telemetry` + `EventLog`
- einen Demo-Use-Case für Telegram → Postkorb → Summary → Telegram

---

## Warum diese Architektur stark ist

### 1. Zero Cost Idle

Im Leerlauf ist das System billig.

Es passiert nichts, solange kein Event eintrifft.

### 2. Klare Verantwortlichkeiten

- Gateway vereinheitlicht Kanäle
- Kernel kontrolliert den Ablauf
- Planner macht semantische Entscheidungen
- Capabilities greifen auf die Welt zu
- Memory lernt über Zeit
- Telemetry macht alles nachvollziehbar

### 3. Gute Erweiterbarkeit

Das System kann klein anfangen und später wachsen in Richtung:

- mehr Capabilities
- echte MCS-Treiber
- Approval / Policy Gates
- Behavior Trees / SOP-Runtime
- persistente Stores
- dedizierte Log-/Telemetry-Systeme
- Marketplace-artige Procedures / Skills

### 4. Gute Trennbarkeit von Probabilistik und Determinismus

Das LLM bleibt stark dort, wo es stark ist.

Die Systemverantwortung bleibt in deterministischen Komponenten.

---

## Nächste sinnvolle Ausbaustufen

### Kurzfristig

- persistenter Event Store
- persistenter Episode Store
- persistenter Profile Store
- erster Procedure Store
- `MemoryDistiller`
- `ProcedureResolver`
- bessere Capability-Abstraktion

### Mittelfristig

- MCS-basierte Driver im Capability Layer
- Approval / Policy Layer
- Replay / Simulation
- Behavior-Tree- oder SOP-Runtime
- mehr Kanäle (E-Mail, UI, Webhook)

### Langfristig

- verteilte Komponenten
- stärkere Bus-Architektur
- versionierte Procedure-Libraries
- Roboter-Profile / Konfigurator
- Observability / Analytics / Governance Layer

---

## Leitsatz dieser Architektur

Der Kernel sagt, **wann** etwas passieren darf.  
Das LLM sagt, **was** als Nächstes sinnvoll ist.  
Die Capability sagt, **wie** die Welt verändert oder gelesen wird.  
Die Telemetry sagt, **was tatsächlich passiert ist**.  
Das Memory sorgt dafür, dass der Roboter **über Zeit besser wird**.

---

## Implementierungsrichtung

Für die Umsetzung in Python kann die bestehende Prototyp-Datei als Startpunkt dienen.

Sinnvolle nächste Module wären:

- `event_store.py`
- `episode_store.py`
- `profile_store.py`
- `procedure_store.py`
- `memory_distiller.py`
- `procedure_resolver.py`
- `capability_registry.py`
- `policy_gate.py`
- `planner.py`
- `kernel.py`
- `runtime.py`
- `gateways/telegram.py`
- `capabilities/mail.py`

---

## Wichtiger Architekturgrundsatz

Nicht alles sofort maximal abstrahieren.

Die Architektur darf **groß gedacht** werden, soll aber **einfach beginnen**.

Das heißt konkret:

- erst ein robuster Monolith,
- dann saubere Grenzen,
- dann Schritt für Schritt Persistenz, Distillation, Procedure-Loading, Governance und MCS-Integration.

Nicht zuerst das perfekte System bauen.  
Zuerst das richtige Skelett bauen.

