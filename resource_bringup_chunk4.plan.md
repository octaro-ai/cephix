# Chunk 4 - Resource Bringup Boot Levels

## Leitidee

Boot-Levels sind **kontiguierlich nummeriert** (0…11, keine Luecken).
Drei universelle off-bus Stufen modellieren *jede* externe Ressource
(Persistenz, Playwright, Docker, SSH, …) — nicht nur Storage:

| Level | Category | Bus? | Rolle |
|------:|----------|------|-------|
| 0 | `BACKEND` | nein | Ressource existiert (DB-Prozess, FS-Mount, VM, Container-Daemon) |
| 1 | `CONNECTION` | nein | Verbindung steht (Conn-Pool, SSH-Session, Browser-Context) |
| 2 | `PROVIDER` | nein | Domain-API injizierbar (SessionStoreProvider, EventStreamProvider) |
| 3 | `UTILITY` | nein | reine Helfer ohne externe Ressource (ModelCatalog, FirmwareStore) |
| 4 | `BUS` | -- | Routing-Fabric |
| 5 | `BUS_PROVIDER` | ja | off-bus Provider als Bus-Dienst (heute leer; Enum reserviert) |
| 6 | `TELEMETRY` | ja | BusRecorder, CapabilityCollector |
| 7 | `AUDIT` | ja | AuditNoteSink |
| 8 | `BUS_UTILITY` | ja | Credentials, kuenftig Approval/Cost |
| 9 | `ACTOR` | nein | in-process |
| 10 | `KERNEL` | ja | ChatKernel, BaseKernel |
| 11 | `CHANNEL` | ja | Websocket, kuenftig Telegram |

## Symmetrie-Regel (Namenskonvention)

> **Off-bus liefert; on-bus stellt bereit.**

| Off-bus | On-bus |
|---------|--------|
| `UTILITY` | `BUS_UTILITY` |
| `PROVIDER` | `BUS_PROVIDER` |

- **PROVIDER** (Level 2): DAO-Factory, per Konstruktor injiziert. Kein Bus.
- **BUS_PROVIDER** (Level 5): dieselbe Rolle auf dem Bus — Konsumenten
  koennen ueber Bus-Protokoll statt Injection zugreifen. Heute noch
  unbesetzt; leere Kategorie erzeugt keinen Log-Laerm
  (`_group_by_category` druckt nur Kategorien mit Komponenten).

## Persistenz-Schichtung (spaeter, nicht dieser Chunk)

```
Domain Store     SessionStore, EventStreamStore  (was Konsumenten wollen)
       ↓
PROVIDER         SessionStoreProvider, EventStreamProvider  (Level 2)
       ↓
CONNECTION       FilesystemConnection, DatabaseConnection  (Level 1)
       ↓
BACKEND          LocalFsBackend, PostgresBackend, S3Backend  (Level 0)

FORMAT (jsonl, json, …) ist orthogonal zum Layer.
```

Heute: `JsonlPersistenceProvider` ist ein **interims-PROVIDER** (Level 2,
off-bus). Spaeter aufgespalten in Backend/Connection/Provider + Rename
`filesystem` statt `jsonl` als Layer-Name.

## Skeleton vs Userspace

**Skeleton** (Phase 2, vor `RobotLifecycle(boot)`):

`BACKEND`, `CONNECTION`, `PROVIDER`, `UTILITY`, `BUS`, `BUS_PROVIDER`, `TELEMETRY`

**Userspace** (Phase 3, nach `RobotLifecycle(boot)`):

`AUDIT`, `BUS_UTILITY`, `ACTOR`, `KERNEL`, `CHANNEL`

## Telemetry / Audit heute

Sink-gebundene Observer erhalten den off-bus Provider **weiter per
Konstruktor-Injection** (`sink=provider.open(channel)`). Kein
`BUS_PROVIDER` noetig bis wir bewusst auf Bus-sichtbare Storage-Zugriffe
umstellen.

## Akzeptanzkriterien (dieser Chunk)

- `ComponentCategory` + `BOOT_PRIORITY` wie oben; `PERSISTENCE` entfaellt.
- `JsonlPersistenceProvider`: `PROVIDER`, plain `RobotComponent` (kein Bus).
- Leeres `BUS_PROVIDER`-Level im Enum, kein Log-Output.
- pytest-Suite gruen.

## Folge-Chunks

- Backend/Connection/Provider-Split fuer Filesystem.
- `SessionStore` zieht Provider statt eigenes FS-IO.
- Erste `BUS_PROVIDER`-Implementierung (StorageBusProvider).
- Composite/Fallback-Provider, DB/S3/Supabase-Backends.
