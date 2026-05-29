# Chunk 3 - Persistence als BusComponent + Boot-Priority-Renumber

## Ziel

- Persistence wird eine echte **BusComponent** mit Lifecycle und
  `health_check()`, schreibt aber weiterhin direkt (nicht bus-aware fuer
  den Datenpfad). Dadurch ist die Komponente health-pruefbar und
  spaeter swap-/fallback-faehig, ohne Schreibvolumen ueber den Bus zu
  jagen.
- Boot-Prioritaeten werden renummeriert: `UTILITY=0` (off-bus zuerst),
  `BUS=1`, neue `PERSISTENCE=2`, `TELEMETRY=3`, `AUDIT=4`,
  `BUS_UTILITY=8`, `ACTOR=9`, `KERNEL=11`, `CHANNEL=21`. Skeleton wird
  `{UTILITY, BUS, PERSISTENCE, TELEMETRY}`.
- `audit` wird im Builder singular-oder-Liste (wie `telemetry` und
  `kernel`/`kernels`) und teilt sich mit `telemetry` einen generischen
  Observer-Listen-Builder. Loest die alte „1:1"-Inkonsistenz auf.
- `persistence` wird singular-oder-Liste mit optionalem `id:`. Solo-
  Eintrag ist automatisch der Default fuer telemetry/audit.

Bewusst **nicht** Teil dieses Chunks: Composite-/Fallback-Provider,
Fan-Out, S3-/Supabase-Layer, Rename `jsonl` -> `filesystem`. Das kommt
spaeter -- jetzt zaehlt: laeuft als BusComponent mit Lifecycle.

## Schritte

1. **Categories & Boot-Priority** (`src/components.py`):
   - Neue `ComponentCategory.PERSISTENCE = "persistence"`.
   - `BOOT_PRIORITY` neu nummerieren wie oben.
   - `SKELETON_CATEGORIES` um `UTILITY` und `PERSISTENCE` erweitern.
   - Docstrings im Modulkopf anpassen.

2. **`JsonlPersistence` -> BusComponent**
   (`src/persistence/jsonl_provider.py` oder vergleichbar):
   - Klasse erbt von `BusComponent`, `component_category =
     PERSISTENCE`, sinnvoller `component_name`.
   - `start(bus)`: `announce_lifecycle("ready")`. Keine Sinks selbst
     oeffnen, kein Subscribe.
   - `stop()`: `announce_lifecycle("shutdown")`, danach offene Sinks
     schliessen (`flush` + `close`).
   - `open(channel) -> Sink`: bleibt direktes API. Telemetry/Audit
     rufen es synchron in deren `start()`. Buchfuehrung, damit `stop()`
     alle ausgegebenen Sinks finalisieren kann.
   - `health_check()`: simpler erster Wurf -- prueft, dass das
     Wurzelverzeichnis schreibbar ist (echte DB-/S3-Checks erst, wenn
     diese Provider kommen).

3. **Robot enthaelt Persistence** (`src/robot.py`):
   - Der Builder uebergibt das Persistence-Objekt jetzt als
     RobotComponent im `components`-Argument (nicht mehr nur als
     Build-Zeit-Helfer). Der `Robot` muss sonst nichts wissen --
     `BOOT_PRIORITY` kuemmert sich um die Reihenfolge.

4. **Builder umbauen** (`src/builder.py`):
   - `_build_persistence_provider(...)` zu
     `_build_persistence_components(...) -> dict[str, RobotComponent]`
     erweitern: nimmt singular oder Liste, baut alle Provider, gibt
     `id -> provider` zurueck. Solo-Eintrag bekommt `id = "default"`.
   - `_build_telemetry_components` und `_build_audit` durch
     **einen** `_build_observer_components(slot, expected_category, …)`
     ersetzen, der singular-oder-Liste akzeptiert, library defaults
     anwendet, Kategorie prueft und einen per-Name-Sink-Injection-Hook
     hat. Hook: `bus_recorder` und `audit_note_sink` bekommen ihren
     Sink ueber `persistence_index[persistence_id].open(channel)`,
     wobei `persistence_id` aus dem Spec oder `"default"` kommt.
   - `audit` als singular-oder-Liste annehmen.
   - Reihenfolge: `persistence_components` werden in die finale
     `components`-Liste aufgenommen; `BOOT_PRIORITY` macht den Rest.

5. **`defaults.yaml`**:
   - Templates: `audit` als Liste schreiben (Single-Entry-Form bleibt
     fuer Userdateien aber gueltig).
   - Library: `persistence`-Eintrag um Hinweis erweitern, dass Solo
     automatisch `id: default` traegt.

6. **Tests** anpassen / ergaenzen:
   - `tests/test_components.py`: neue `PERSISTENCE`-Kategorie,
     Skeleton-Mitgliedschaft, Boot-Reihenfolge-Marker.
   - `tests/test_robot.py`: Persistence-Component erscheint in der
     boot order an Position 2 (nach UTILITY+BUS, vor TELEMETRY).
   - `tests/test_builder.py`: persistence singular-oder-Liste, audit
     singular-oder-Liste, optional `persistence: <id>` Referenz,
     defaults laufen ohne explizite id.
   - JsonlPersistence-Tests: lifecycle (ready/shutdown), `open()`
     funktioniert nach start(), `stop()` schliesst sinks,
     `health_check()` ok.
   - Integration: chatbot-Template baut sauber, Boot-Reihenfolge
     enthaelt Persistence vor Telemetry, Capability-Collector immer
     noch vor allen Command-Anbietern.

7. **Verifikation**:
   - Komplette pytest-Suite gruen.
   - Smoke: ein Bot aus dem chatbot-Template hochziehen, im Log die
     neuen Phase-Marker sehen (Entering Boot Level 0 UTILITY -> 1 BUS
     -> 2 PERSISTENCE -> 3 TELEMETRY -> ...).

## Akzeptanzkriterien

- Persistence ist ein `BusComponent`, taucht in `robot.components`
  zwischen BUS und TELEMETRY auf, kuendigt sich `ready` / `shutdown` an
  und antwortet auf `health_check()`.
- Boot-Prios sind durchnummeriert wie oben, Tests reflektieren das.
- `audit` und `telemetry` akzeptieren beide singular und Liste.
- `persistence` akzeptiert singular und Liste; ohne explizites `id:`
  greift `"default"`, ohne explizite `persistence: <id>` in
  telemetry/audit wird Default genutzt.
- Komplette Suite gruen.

## Migration fuer `~/.cephix` (Userseite, nach gruenem Test)

- `~/.cephix/cephix.yaml` einmal loeschen, beim naechsten Lauf wird die
  neue Defaults-Datei dort hin kopiert.
- `~/.cephix/robots/dreamgirl/robot.yaml`: alte Form
  (`persistence: {name: jsonl}`, `audit: {name: audit_note_sink}`)
  bleibt gueltig; nur wer Mehrfach-Persistence will, schreibt um.
