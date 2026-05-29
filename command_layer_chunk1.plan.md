# Command Layer + Capability Manifest (Chunk 1)

Status: PLAN. Diese Datei beschreibt den ersten Implementierungs-Chunk des
Command-Layers. Sie ist die Referenz fuer die Umsetzung; die To-dos sind
separat angelegt.

## Ziel

Einen bus-nativen Command-Layer einfuehren, mit dem Channels (zuerst der
CLI-Client) Roboter-Operationen ausloesen koennen, die **keine
Konversation** sind: neue Session anlegen, Sessions auflisten, Session
oeffnen, Session umbenennen. Dazu ein retained **Capability-Manifest**, aus
dem jede UI failsafe ableitet, welche Commands ueberhaupt verfuegbar sind.

**Explizit NICHT in diesem Chunk:**

- Guards (ACL / Rate-Limit / Budget) -> eigener Folge-Plan.
- Consent / Approval-Required / Pending-Slot -> eigener Folge-Plan, sobald
  der ChatKernel Tools ausfuehren kann.
- Auto-Title (SessionTitler) -> eigener Folge-Plan.
- Modell-Switch, Datei-Upload, Audio, Tool-Auswahl -> spaeter.

## Designgrundsaetze (aus der Diskussion festgehalten)

1. **Direkte Subscription statt Mediator.** Jede Bus-Komponente, die
   Commands anbietet, subscribed selbst auf die zugehoerigen
   Command-Topics. Es gibt keinen zentralen dispatchenden Broker. Das ist
   konsistent mit der bestehenden cephix-Mechanik (Kernel subscribed
   `input.message`, Audit subscribed `audit.note`).

2. **Keine Reflection beim Dispatch.** Der Handler wird beim Subscriben
   einmalig per `getattr(self, spec.handler)` an eine echte
   Method-Reference gebunden. Tippfehler im Spec-Tupel sind ein
   Boot-Zeit-`AttributeError`, kein Laufzeitproblem im Hot-Path.

3. **Owner ist immer eine Bus-Komponente.** Off-bus Utilities
   (`JsonlSessionStore`, ...) deklarieren keine Commands. Wer ihre
   Operationen exponieren will, baut den Command-Handler in eine
   Bus-Komponente (hier: der `ChatKernel`, der die Session-Utility ohnehin
   per Convention-DI haelt).

4. **Topic-Konvention: big-endian (general -> specific), Diskriminator als Suffix.**
   ```
   command.request.<action>            command.request.<action>@<disc>
   command.response.<action>           command.response.<action>@<disc>
   command.notify.<action>             command.notify.<action>@<disc>
   ```
   Die Kind-Ebene (`request`/`response`/`notify`) steht direkt hinter
   `command`, wie bei Reverse-DNS/Java-Packages das Allgemeinere links.
   So faengt ein Prefix-Subscribe auf `command.notify.` alle Notifies,
   `command.request.` alle Requests -- unabhaengig von der Action.
   `action` ist der reine Verb-String (`chat.session.new`), `target`/
   Diskriminator ist ein eigenes Event-Feld, nicht Teil des Action-Strings.

5. **Action-Naming `<domain>.<entity>.<verb>`.** Domain-Praefix vermeidet
   Kollisionen (`chat.session.new` vs. hypothetisch `database.session.new`).

6. **Capability-Collector ist ein reines Sammel-Utility.** Es hoert
   Lifecycle, baut eine **stabil geordnete** Manifest-Liste und published
   `harness.capabilities` retained. Kein Dispatch, kein Routing.

7. **Failsafe-UI als Struktur, nicht als Defensiv-Code.** Fehlt eine
   Command-Action im Manifest, zeigt die UI weder Button noch Slash-Command
   dafuer. Verschwindet die anbietende Komponente, faellt die Action via
   Lifecycle aus dem Manifest.

8. **Correlation-ID-Reply-Routing** wie bei `ComponentRequest`/
   `ComponentResponse`. Der Aufrufer merkt sich eine `correlation_id` und
   matched die Response.

## Architektur-Ueberblick

```
CLI-Client                WebsocketChannel              Bus                ChatKernel
   |                            |                         |                     |
   |  {"type":"command",        |                         |                     |
   |   "action":"chat.session.new",                       |                     |
   |   "correlation_id":cid}    |                         |                     |
   |--------------------------->|                         |                     |
   |                            | CommandRequest          |                     |
   |                            | topic=command.request.chat.session.new        |
   |                            |------------------------>|                     |
   |                            |                         |  (kernel subscribed |
   |                            |                         |   this topic)       |
   |                            |                         |-------------------->|
   |                            |                         |     cmd_session_new |
   |                            |                         |   self._sessions.new_session()
   |                            |                         |<--------------------|
   |                            | CommandResponse         | publish             |
   |                            | topic=command.response.chat.session.new       |
   |                            |<------------------------|                     |
   |  {"type":"command_response"|                         |                     |
   |   "correlation_id":cid,    |                         |                     |
   |   "payload":{session_id}}  |                         |                     |
   |<---------------------------|                         |                     |

CapabilityCollector (BUS_UTILITY)
   subscribes component.<name>.lifecycle (broadcast) for every component
   builds ordered manifest -> publishes harness.capabilities (retained)

CLI on connect: reads retained harness.capabilities -> enables /new, /sessions, ...
```

## Bus-Events (src/bus/messages.py)

Neue Topic-Helper analog zu `component_lifecycle_topic`:

```python
COMMAND_TOPIC_PREFIX = "command."
HARNESS_CAPABILITIES_TOPIC = "harness.capabilities"

def command_request_topic(action: str, discriminator: str | None = None) -> str: ...
def command_response_topic(action: str, discriminator: str | None = None) -> str: ...
def command_notify_topic(action: str, discriminator: str | None = None) -> str: ...
# -> "command.<action>.request"  (+ "@<disc>" wenn discriminator gesetzt)
```

Drei neue Event-Klassen:

```python
@dataclass(frozen=True, kw_only=True)
class CommandRequest(RobotEvent):
    action: str = ""
    target: str | None = None              # Diskriminator
    payload: dict[str, Any] = field(default_factory=dict)
    # __post_init__: action nicht leer; correlation_id Pflicht (wie ComponentRequest)

@dataclass(frozen=True, kw_only=True)
class CommandResponse(Failable, RobotEvent):
    action: str = ""
    target: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    # __post_init__: super() (Failable-Invariante) + correlation_id Pflicht

@dataclass(frozen=True, kw_only=True)
class CommandNotify(RobotEvent):
    action: str = ""
    target: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    # broadcast-faehig; correlation_id NICHT noetig
```

Begruendung Felder:
- `action` bleibt sauberer Verb-String fuer Audit/Telemetrie-Queries.
- `target` separat, damit "alle chat.session.new gruppiert nach target"
  eine reine Feld-Query ist.
- `CommandResponse` erbt `Failable` -> einheitliches ok/error-Vokabular,
  Deny/Fehler reist als `status="error"` + `ErrorInfo`.

## Command-Spec (neues Paket src/command/)

```
src/command/__init__.py        # API-Re-Exports
src/command/spec.py            # CommandSpec, RiskClass, ConsentRequirement (stub)
src/command/mixin.py           # CommandProviderMixin / Wiring-Helper
```

`spec.py`:

```python
class RiskClass(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK_MUTATION = "low_risk_mutation"
    HIGH_RISK_MUTATION = "high_risk_mutation"

@dataclass(frozen=True)
class CommandSpec:
    action: str
    handler: str                              # Methodenname auf der Owner-Instanz
    label: str = ""
    description: str = ""
    args_schema: dict[str, Any] = field(default_factory=dict)
    risk_class: RiskClass = RiskClass.READ_ONLY
    discriminator: str | None = None          # optionaler Diskriminator
    ui_hints: dict[str, Any] = field(default_factory=dict)
    # Platzhalter fuer Folge-Chunks (nicht ausgewertet in Chunk 1):
    # consent, context_mapping, rate_limit
```

`provides_commands` ist ein `ClassVar[tuple[CommandSpec, ...]]` Default `()`
auf `RobotComponent`. Damit hat jede Komponente das Attribut, ohne dass es
Pflicht wird ("alles kann nichts muss").

Wiring-Helper (auf `BusComponent` oder als freie Funktion):

```python
def wire_commands(component, bus) -> list[Subscription]:
    subs = []
    for spec in component.provides_commands:
        handler = getattr(component, spec.handler)   # Boot-Zeit-Check
        topic = command_request_topic(spec.action, spec.discriminator)
        subs.append(bus.subscribe(topic, _make_command_consumer(component, spec, handler, bus)))
    return subs
```

`_make_command_consumer` umschliesst den Handler so, dass:
- nur `CommandRequest` durchgelassen wird,
- der Handler `(req) -> dict` Result liefert,
- daraus eine `CommandResponse` (status ok) gebaut + published wird,
- Handler-Exceptions zu `CommandResponse(status="error", code="command.handler_failed")`
  werden.

So bleibt jede Komponente frei von Boilerplate -- sie schreibt nur
`provides_commands` + die `cmd_*`-Methoden und ruft `wire_commands` im
`start`.

## Capability-Collector (src/utility/capability_collector/)

`BUS_UTILITY`, Boot-Prioritaet 7 (bestehend). Aufgaben:

1. Beim `start(bus)`: `subscribe_all` und filtern auf
   `ComponentLifecycle`-Instanzen. Lifecycle-Topics heissen pro Komponente
   (`component.<name>.lifecycle`) und sind beim Start noch nicht alle
   bekannt; `subscribe_all` umgeht das ohne Wildcard-Subscriptions
   (bestaetigte Entscheidung). Der Collector hoert bewusst auf alles und
   filtert selbst.
2. Aus `ComponentLifecycle.info.metadata["provides_commands"]` die
   CommandSpecs lesen. -> erfordert, dass die Specs im
   `ComponentInfo.metadata` landen (siehe Robot-Anpassung unten).
3. Eine **geordnete Liste** pflegen: append bei `boot`/`ready`, remove bei
   `shutdown`/`failure`. Re-Mount derselben `instance_id` aktualisiert
   in-place (keine Umsortierung) -> stabile Reihenfolge.
4. Nach jeder Aenderung `harness.capabilities` mit `retain=True`
   re-publishen.

### Capability-Scoping: zwei Ebenen (entschieden)

Das Manifest mischt zwei Naturen, daher **zwei getrennte Ebenen** mit
gleicher Event-Form-Philosophie, aber verschiedenen Topics:

1. **`harness.capabilities`** -- **global**, retained, ohne `session_id`.
   Beantwortet "was bietet dieser Roboter ueberhaupt an": `commands`,
   verfuegbare `models`-Liste, `tools`, `skills`, und das `settings`-
   **Schema** (key/type/values/default -- nicht der gewaehlte Wert).
   `session.new`/`session.list` muessen hier leben, weil sie existieren,
   *bevor* es eine Session gibt. **-> Chunk 1 baut nur das, befuellt nur
   `commands`.**

2. **`harness.context@<session_id>`** -- **pro Session**, retained je
   Session (Diskriminator-Suffix wie bei Commands). Beantwortet "was kann
   ich *in dieser Session jetzt*": aktives `model` + dessen `supports_*`,
   aufgeloeste `inputs`/`outputs` (Upload-/Audio-Affordances), und die
   **gewaehlten** `settings`-Werte. **-> spaeterer Chunk**, sobald
   Modell-Auswahl pro Session existiert. (Topic-Helper
   `harness_context_topic(session_id)` kommt dann.)

Settings-Aufteilung (entschieden): das **Schema** (`values`, `default`)
ist modellabhaengig/global und lebt im `harness.capabilities`; der
**gewaehlte Wert** ist Session-State und lebt im `harness.context`.

### Globales Event (Chunk 1)

```python
@dataclass(frozen=True, kw_only=True)
class HarnessCapabilities(RobotEvent):
    # Chunk 1: gefuellt
    commands: tuple[dict[str, Any], ...] = ()
    # Folge-Chunks (global): Schema steht, Inhalt kommt spaeter
    models: tuple[dict[str, Any], ...] = ()    # verfuegbare Modelle (NICHT das aktive)
    tools: tuple[dict[str, Any], ...] = ()     # verfuegbare Tools
    skills: tuple[dict[str, Any], ...] = ()    # verfuegbare Skills/SOPs
    settings: tuple[dict[str, Any], ...] = ()  # Schema: key/type/values/default
```

Die per-Session-Felder (`model` aktiv, `inputs`, `outputs`, gewaehlte
Settings) sind **bewusst nicht** in diesem Event -- sie gehoeren in das
spaetere `HarnessContext`-Event auf `harness.context@<session_id>`.

Published auf `HARNESS_CAPABILITIES_TOPIC` via `publish_broadcast(retain=True)`.

Jeder Command-Eintrag in `commands`: `{action, label, description,
args_schema, risk_class, discriminator, ui_hints, owner_component,
owner_instance_id}`.

**Robot-Anpassung (src/robot.py):** beim Bau der `ComponentInfo` fuer ein
Component die `provides_commands` serialisieren und in `metadata`
["provides_commands"] ablegen. Dadurch sieht der Collector sie via
Lifecycle, ohne dass eine neue Discovery-Mechanik gebaut wird.

## SessionStore-Erweiterung

`src/utility/session_store/types.py`: neuer Typ

```python
@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    title: str | None = None
    created_at: str = ""
    last_activity_at: str = ""
    message_count: int = 0
    model_id: str | None = None
```

`ports.py` + `store.py`:
- `list_sessions()` Rueckgabe von `list[str]` -> `list[SessionSummary]`
  (geordnet: nach `last_activity_at` desc oder Anlage-Reihenfolge; in
  Chunk 1 reicht Anlage-Reihenfolge aus dem Index).
- neue Methode `set_title(session_id, title)`.
- ein kleiner `index.json` neben den `*.jsonl` haelt Meta (title,
  created_at, last_activity_at, message_count). Crash-toleranz: bei
  fehlendem/kaputtem Index aus den vorhandenen `*.jsonl` rekonstruieren.

Hinweis: bestehende Aufrufer von `list_sessions()` (falls vorhanden) sind
anzupassen; der ChatKernel nutzt heute `new_session`/`open`/`append`/
`messages`, also gering betroffen.

## ChatKernel-Commands (src/kernel/chat.py)

```python
provides_commands = (
    CommandSpec(action="chat.session.new",    handler="cmd_session_new",
                label="New chat",  ui_hints={"shortcut": "/new", "group": "session"}),
    CommandSpec(action="chat.session.list",   handler="cmd_session_list",
                label="List chats", ui_hints={"shortcut": "/sessions", "group": "session"}),
    CommandSpec(action="chat.session.open",   handler="cmd_session_open",
                label="Open chat",  args_schema={"session_id": "string"}),
    CommandSpec(action="chat.session.rename", handler="cmd_session_rename",
                label="Rename chat", args_schema={"session_id": "string", "title": "string"}),
)
```

Im `start(bus)` (nach super().start): `self._command_subs = wire_commands(self, bus)`.
Im `stop`: Subscriptions unsubscriben.

Handler:
```python
async def cmd_session_new(self, req) -> dict:
    sid = self._sessions.new_session()
    return {"session_id": sid}

async def cmd_session_list(self, req) -> dict:
    return {"sessions": [asdict(s) for s in self._sessions.list_sessions()]}

async def cmd_session_open(self, req) -> dict:
    sid = req.payload["session_id"]
    created = self._sessions.open(sid)
    msgs = self._sessions.messages(sid)
    return {"session_id": sid, "created": created,
            "messages": [m.to_jsonl_dict() for m in msgs]}

async def cmd_session_rename(self, req) -> dict:
    self._sessions.set_title(req.payload["session_id"], req.payload["title"])
    return {"ok": True}
```

## Registry + defaults.yaml

- Registry: `CapabilityCollector` registrieren.
- defaults.yaml: im `chatbot`-Template die `utility:`-Liste um
  `{name: capability-collector}` ergaenzen; Component-Library-Eintrag
  `utility: - {name: capability-collector}`.
- (default-Template optional; in Chunk 1 reicht chatbot.)

## WebSocket-Protokoll (src/channels/websocket.py)

Client -> Server zusaetzlich:
```json
{"type": "command", "action": "chat.session.new", "target": null,
 "payload": {}, "correlation_id": "cmd-..."}
```
-> Channel publisht `CommandRequest` auf `command_request_topic(action, target)`.
   Channel subscribed dynamisch (oder pauschal via subscribe_all gefiltert)
   die zugehoerige `*.response`-Topic und routet die `CommandResponse`
   zurueck an die Session, die den Request geschickt hat (Mapping
   `correlation_id -> session_id`).

Server -> Client:
```json
{"type": "command_response", "action": "...", "correlation_id": "...",
 "status": "ok", "payload": {...}}            // oder status=error + error-block
{"type": "capabilities", "commands": [...]}    // bei Connect + bei Updates
```

Beim Connect: retained `harness.capabilities` lesen und als
`capabilities`-Frame senden; zusaetzlich auf das Topic subscriben um
Updates durchzureichen.

Entscheidung Subscriptions (bestaetigt): in Chunk 1 nutzt der Channel
`subscribe_all` und filtert auf `CommandResponse` + `HarnessCapabilities`.
Der Channel haelt **keine** statische Topic-Liste, sondern prueft pro
durchlaufender Botschaft dynamisch, ob sie ihn betrifft. Das ist
ausdruecklich gewollt: Komponenten koennen jederzeit Command-Topics dazu-
oder abschalten (Skill mounten/entmounten, Guard deaktivieren), und der
Channel passt seine wirksame Befehlsliste laufend an, ohne neu zu
subscriben. Die autoritative "was ist gerade verfuegbar"-Sicht bleibt das
retained `harness.capabilities`-Manifest.

## CLI-Client (src/cli_client.py)

- Beim Connect `capabilities`-Frame auswerten -> verfuegbare
  Slash-Commands ableiten (`ui_hints.shortcut`).
- Eingabe-Parsing: beginnt die Zeile mit `/`, als Command interpretieren:
  - `/new`            -> action chat.session.new
  - `/sessions`       -> action chat.session.list, Ergebnis als Liste rendern
  - `/open <id>`      -> action chat.session.open, danach aktive Session
                         wechseln + History rendern
  - `/rename <id> <title...>` -> action chat.session.rename
  - `/help`           -> lokale Auflistung der freigeschalteten Commands
- Nicht-Slash-Eingabe weiterhin als `input`-Frame mit aktueller
  `session_id` im payload.
- Correlation: pro gesendetem Command eine `correlation_id` erzeugen,
  Future/Event halten, auf `command_response` matchen.
- Failsafe: ist ein Slash-Command nicht im Manifest, Hinweis ausgeben
  ("not supported by this robot") statt zu senden.

## Tests

- `tests/bus/test_messages.py`: Validierung CommandRequest/Response/Notify
  (action Pflicht, correlation_id Pflicht wo noetig, Failable-Invariante,
  Topic-Helper inkl. Diskriminator).
- `tests/command/test_spec.py`: CommandSpec Defaults, wire_commands bindet
  Methoden, AttributeError bei falschem handler.
- `tests/command/test_wiring.py`: CommandRequest -> Handler -> CommandResponse
  (ok + error-Pfad) ueber einen echten AsyncioBus.
- `tests/utility/capability_collector/test_collector.py`: Lifecycle-Events
  rein -> Manifest geordnet, retained, remove bei shutdown, stabile
  Reihenfolge bei Re-Mount.
- `tests/utility/session_store/`: SessionSummary, set_title, list_sessions
  Reihenfolge, Index-Rekonstruktion.
- `tests/kernel/test_chat_kernel.py`: die vier cmd_* Handler.
- `tests/channels/test_websocket.py`: command-Frame -> CommandRequest;
  CommandResponse -> command_response-Frame; capabilities-Frame bei Connect.
- CLI: Slash-Parsing als reine Funktion testbar machen
  (`_parse_input(line) -> InputAction | CommandAction`).

## Akzeptanzkriterien

1. Komplette `pytest`-Suite gruen.
2. Smoketest dreamgirl:
   - Connect zeigt `capabilities` mit chat.session.* Commands.
   - `/sessions` listet vorhandene Sessions (inkl. Titel falls vorhanden).
   - `/new` legt eine Session an, CLI wechselt darauf.
   - Normale Nachricht laeuft wie bisher gegen den ChatKernel.
   - `/rename <id> <title>` setzt den Titel; `/sessions` zeigt ihn.
3. Faellt der ChatKernel weg (oder Collector), zeigt die CLI keine
   chat.session.* Slash-Commands -> Failsafe verifiziert.

## Reihenfolge der Umsetzung

1. Bus-Events + Topic-Helper (+ Tests).
2. Command-Spec-Paket + wire_commands (+ Tests).
3. Robot: provides_commands in ComponentInfo.metadata serialisieren.
4. CapabilityCollector (+ Tests).
5. SessionStore-Erweiterung (+ Tests).
6. ChatKernel-Commands (+ Tests).
7. Registry + defaults.yaml.
8. WebSocket-Protokoll (+ Tests).
9. CLI-Slash-Commands (+ Tests).
10. Volle Suite + Smoketest.
