# Chunk 4a - Persistence DAO Layering (Backend / Connection / Provider)

## Leitidee

Persistenz sauber DAO-modelliert, FORMAT orthogonal zum Storage-Layer.
Kein `EventSink` mehr im oeffentlichen API: der Provider IST das DAO und
hat ``append(channel, record)``. Sinks/Handles sind interne
Implementierungsoptimierung.

## Schichtung

```
Domain-Port              EventStreamProviderPort (Protocol)
                           append(channel, record)
                           flush(channel?)
       implements
              v
Provider (Level 2)       FilesystemEventStreamProvider
                           - connection (DI)
                           - codec
                           - intern: _handles map (channel -> Handle)
       uses
              v
Connection (Level 1)     FilesystemConnection
                           - adapter (DI)
                           - root: Path
                           - open_append(rel_path), mkdir, health
       uses
              v
Backend (Level 0)        LocalFSAdapter implements FilesystemPort
                           - pathlib + aiofiles
```

FORMAT (jsonl, json, ...) lebt als ``JsonlCodec`` neben der
Filesystem-Familie; reine Bibliothek (kein ``RobotComponent``).

## Konsumenten-Vertrag

BusRecorder und AuditNoteSink kennen nur den Domain-Port + einen
``channel``-String. Kein ``sink``-Konzept im API mehr.

```python
class BusRecorder(BusComponent):
    def __init__(self, *, provider: EventStreamProviderPort,
                 channel: str = "telemetry"): ...

    async def _on_event(self, event):
        record = self._serialize(event)
        await self._provider.append(self._channel, record)
```

## Boot-Log mit injected-Markern

Symmetrisch zu ``LLMActorOpenAI injected into ChatKernel``:

```
=== Boot Level 0 (BACKEND) ===
LocalFSAdapter (...) started
=== Boot Level 1 (CONNECTION) ===
LocalFSAdapter (...) injected into FilesystemConnection (...)
FilesystemConnection (...) started
=== Boot Level 2 (PROVIDER) ===
FilesystemConnection (...) injected into FilesystemEventStreamProvider (...)
FilesystemEventStreamProvider (...) started
=== Boot Level 3 (UTILITY) ===
...
```

Robot logs die ``injected into``-Zeile beim Konstruieren der naechsten
Stufe -- gleicher Mechanismus wie BaseKernel beim Actor-Mount, aber
generischer im Robot, weil Adapter -> Connection -> Provider eine echte
Kette ist (nicht nur ein Slot).

## Module-Layout

```
src/persistence/
  __init__.py               # Re-exports: EventStreamProviderPort, all classes
  codec/
    __init__.py
    jsonl.py                # JsonlCodec: dict -> bytes, append-only NDJSON
  filesystem/
    __init__.py
    port.py                 # FilesystemPort Protocol
    local_adapter.py        # LocalFSAdapter (BACKEND, Level 0)
    connection.py           # FilesystemConnection (CONNECTION, Level 1)
  event_stream/
    __init__.py
    port.py                 # EventStreamProviderPort Protocol
    filesystem.py           # FilesystemEventStreamProvider (PROVIDER, Level 2)
```

Komplett **geloescht** (kein Deprecation):
- ``src/persistence/sink.py`` (EventSink)
- ``src/persistence/jsonl_sink.py`` (JsonlEventSink)
- ``src/persistence/provider.py`` (JsonlPersistenceProvider)

## YAML-Form (Convention-driven)

Single-line bleibt der Default; der Builder synthesisiert die drei
Komponenten:

```yaml
persistence:
  - id: data
    name: filesystem-events       # = Registry-Name des Providers
    root: logs                    # -> wird der FilesystemConnection.root
    codec: jsonl                  # -> JsonlCodec
    adapter: local-fs             # default, optional
```

Builder erzeugt:
1. ``LocalFSAdapter`` (anonyme Instanz, fuer diesen Stack)
2. ``FilesystemConnection(adapter=adapter, root=workspace/logs)``
3. ``FilesystemEventStreamProvider(connection=connection, codec="jsonl")``

Alle drei landen als Komponenten in ``robot.components``, jeweils
in ihrer korrekten Boot-Phase. Stack-Identitaet wird ueber eine
``persistence_id`` Convention zugeordnet (so dass spaeter zwei
unterschiedliche Stacks parallel laufen koennen).

Telemetry/Audit referenzieren den Provider per id:

```yaml
telemetry:
  - {name: bus_recorder, persistence: data, channel: telemetry}
  - {name: capability-collector}
audit:
  - {name: audit_note_sink, persistence: data, channel: audit}
```

## Akzeptanzkriterien

- ``EventStreamProviderPort``, ``FilesystemEventStreamProvider``,
  ``FilesystemConnection``, ``LocalFSAdapter``, ``JsonlCodec`` existieren.
- ``EventSink``, ``JsonlEventSink``, ``JsonlPersistenceProvider`` sind
  komplett aus dem Repo geloescht.
- ``BusRecorder`` und ``AuditNoteSink`` konstruieren mit
  ``provider + channel`` (Kwargs).
- Boot-Log zeigt vier Levels (0/1/2 belegt) mit ``injected into``-Zeilen
  fuer die DI-Kette.
- Builder synthesisiert den Stack aus einer ``persistence``-YAML-Zeile,
  ``persistence: <id>`` in ``telemetry``/``audit`` referenziert ihn.
- Alle Tests gruen, manueller Smoke gegen dreamgirl schreibt sauber in
  ``logs/telemetry.jsonl`` und ``logs/audit.jsonl``.

## Folge-Chunks

- 4b: SessionStore wird ``SessionStoreProvider`` (Level 2), nutzt
  ``FilesystemConnection`` statt eigenes FS-IO. Rename
  ``JsonlSessionStore`` -> ``FilesystemSessionStoreProvider``.
- 4c: ``MarkdownFirmwareStore`` zieht auf dieselbe Connection
  (FirmwareStoreProvider).
- 5: Erste ``BUS_PROVIDER``-Implementierung (Level 5): exposes
  ``EventStreamProviderPort`` ueber den Bus -- ``command.request.storage.append``
  etc. Telemetry/Audit koennen wahlweise on-bus statt off-bus
  schreiben.
- spaeter: ``S3FSAdapter``, ``SmbAdapter`` -- nur Level 0 wird
  ausgetauscht, Connection/Provider unveraendert.
- spaeter: Andere Provider-Familien (DatabaseEventStreamProvider,
  ObjectStoreEventStreamProvider).
