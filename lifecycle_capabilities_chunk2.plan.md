# Chunk 2 - Self-Announced Lifecycle + komponentengetriebene Capabilities

## Ziel / Leitidee

Der Robot ist nur das **Geruest**. Das **Leben** findet auf dem Bus durch
die Komponenten statt. Faehigkeiten (Commands, spaeter Tools/Modelle)
gehoeren den Komponenten, nicht dem Boot-Snapshot. Deshalb:

- Jede **BusComponent** kuendigt sich **selbst** an (`ready` beim Attach)
  und meldet sich **selbst** ab (`shutdown` beim Detach) -- ueber
  `ComponentLifecycle` (retained, pro Komponente).
- Der **CapabilityCollector** aggregiert diese Selbstmeldungen live:
  `ready`/`warn` -> Commands der Komponente aufnehmen, `shutdown`/`failure`
  -> entfernen, danach `harness.capabilities` retained republizieren.
- `RobotLifecycle` bleibt das **Roster** (wer existiert: category, name,
  description) -- KEINE `provides_commands` mehr. Einzige Quelle der
  Faehigkeiten ist `ComponentLifecycle`.

Resilienz: faellt der Kernel aus / wird gewartet, verschwinden seine
Commands aus dem Manifest, der Channel blendet die Buttons aus -- der
Rest des Roboters laeuft weiter. Kein Crash, nur "Faehigkeit nicht
verfuegbar".

## Wer publiziert, wer nimmt zurueck

| Ereignis | Wer | Event |
|---|---|---|
| Attach (`start(bus)`) | die BusComponent selbst | retained `ComponentLifecycle(phase="ready")` mit `info.metadata["provides_commands"]` |
| Detach (`stop()`, Bus lebt noch -- Bus hat Prio 0, stoppt zuletzt) | die BusComponent selbst | `ComponentLifecycle(phase="shutdown")` |
| Crash / Komponente kann es nicht selbst | der Owner (Robot, Health-Loop) | `ComponentLifecycle(phase="failure")` -- **nur** im Fehlerfall, sonst immer die Komponente. Spaeterer Chunk. |
| Plain `RobotComponent` (Bus, Actors, Off-Bus-Utilities) im Happy-Path | niemand | kein Lifecycle-Event (nur `started`/`stopped` im Log) -- sie haben keinen Bus und keine Faehigkeiten |

## Schritte

1. **Topic-Konvention big-endian** (wie `command.<kind>.<action>`):
   - `component.lifecycle.<name>` statt `component.<name>.lifecycle`
   - `component.mount.<name>` statt `component.<name>.mount`
   - Anpassen: `component_lifecycle_topic`, `component_mount_topic`
     (+ Docstrings) in `src/bus/messages.py`. Der BaseKernel-Aufruf
     `component_mount_topic(self.component_name)` zieht automatisch nach.
   - Begruendung: Prefix `component.lifecycle.` faengt alle Lifecycle.
     Heute noch `subscribe_all` + Filter; sobald der Bus `subscribe_prefix`
     bekommt, tauscht der Collector das gegen `component.lifecycle.`.

2. **`RobotComponent.component_info() -> ComponentInfo`** (bus-frei):
   reine Selbstbeschreibung (category, name, description,
   `metadata["provides_commands"]` via `CommandSpec.manifest_entry`).
   Genutzt von `announce_lifecycle` (self) und spaeter vom Robot
   (failure-Bridge). Die `provides_commands`-Serialisierung wandert aus
   `Robot._component_metadata` hierher.

3. **`BusComponent.announce_lifecycle(bus, phase, *, error=None)`**:
   baut aus `self.component_info()` eine `ComponentLifecycle` und
   publiziert sie broadcast+retained auf `component.lifecycle.<name>`.
   `parent=""` (direkt vom Robot besessen). `announce_lifecycle` lebt auf
   BusComponent, weil nur sie einen Bus hat -- `RobotComponent` braucht
   nichts dergleichen.

4. **Self-Announce in allen BusComponents verdrahten**: am Ende von
   `start(bus)` -> `ready`, am Anfang von `stop()` (Bus noch da) ->
   `shutdown`. Betroffen: BaseKernel/ChatKernel, WebsocketChannel,
   BusRecorder, AuditNoteSink, CapabilityCollector, CredentialProvider.
   Faehigkeiten tragen heute nur Kernel; der Rest meldet leere
   `provides_commands` (vom Collector ignoriert), liefert aber
   Observability.

5. **`RobotLifecycle`-Roster wieder schlank**: `Robot.component_manifest`
   baut `ComponentInfo` ohne `provides_commands` (Chunk-1-Ergaenzung
   zuruecknehmen). Roster = "wer existiert".

6. **CapabilityCollector umbauen**: statt `subscribe_broadcast(LIFECYCLE_TOPIC)`
   nun `subscribe_all` + Filter auf `ComponentLifecycle`.
   - `ready`/`warn` -> `info.metadata["provides_commands"]` der Komponente
     in den Aggregat-Zustand (Key = `info.name`) uebernehmen.
   - `shutdown`/`failure` -> Eintrag der Komponente entfernen.
   - Reihenfolge: erste-gesehen-Reihenfolge = Boot-Reihenfolge (stabil,
     da Command-Anbieter Prio >= 8 nach dem Collector Prio 7 attachen ->
     live gefangen). Kein Roster-Seed noetig.
   - bei Aenderung `harness.capabilities` retained republizieren.

7. **Tests anpassen / ergaenzen**:
   - `tests/bus/test_messages.py`: neue Topic-Formate.
   - `tests/kernel/test_base_kernel.py`: Mount-Topic-Format.
   - Collector-Tests auf `ComponentLifecycle`-Aggregation (add/remove/
     republish) umstellen.
   - Neuer Test: BusComponent self-announce ready/shutdown.
   - Robot-Roster-Test: kein `provides_commands` mehr im Manifest.
   - End-to-End-Smoke bleibt gruen (Kernel kuendigt Commands an ->
     Collector -> Channel).

## Akzeptanzkriterien

- Komplette pytest-Suite gruen.
- Booten eines chatbot-Robots: Collector baut `harness.capabilities`
  ausschliesslich aus den selbst-gemeldeten `ComponentLifecycle` des
  ChatKernels.
- Stoppt der Kernel (ohne den Robot zu stoppen), verschwinden seine
  Commands aus dem retained `harness.capabilities`.
- `RobotLifecycle` traegt keine `provides_commands` mehr.
