# `_reference/` -- vorheriger Implementierungsstand

Dieser Ordner enthaelt den Stand der Cephix-Implementierung von vor dem
Greenfield-Reset auf die bus-zentrische Robot-OS-Architektur. Der Inhalt
ist eingefroren, wird nicht mehr fortgeschrieben und dient ausschliesslich
als Vergleichsbasis und Inspirationsquelle fuer den schrittweisen Wiederaufbau
unter `src/`.

## Was hier liegt

| Pfad | Inhalt |
|---|---|
| `src/` | Bisherige Python-Implementierung (Kernel, Gateways, Tools, SOP/Skill, Memory, Governance). |
| `tests/` | Tests gegen die bisherige Implementierung. |
| `robot/` | Beispiel-Firmware, Memory-Dokumente und SOP `order-export.yaml`. |
| `cephix-drp.py` | Frueherer Entry-Point-Wrapper auf `src.app.main`. |
| `robot_events.jsonl` | Audit-Trail-Artefakt aus alten Lokal-Runs. |
| `readme2.md`, `TODO.md` | Aeltere Konzept- und Roadmap-Notizen. |

## Einordnung

Die Architektur unter `_reference/src/` folgt dem bisherigen
5-Layer-Harness-Modell mit `DigitalRobotKernel`, `RuntimeEventLoop`,
`SemanticBus` und separater `Telemetry`. Diese Aufteilung wird durch das
neue Modell ersetzt -- siehe
[`docs/architecture/robot-os-target.md`](../docs/architecture/robot-os-target.md).

Konkrete Mappings beim Wiederaufbau:

- `SemanticBus` + `RuntimeEventLoop.queue` + `Telemetry` werden auf einen
  einzigen Systembus konsolidiert.
- `RobotEvent` bleibt als Basistyp; die Subtypen `RobotInput`,
  `RobotTrigger`, `RobotOutput`, `RobotRequest`, `RobotResponse` und
  `RobotAuditNote` werden eingefuehrt.
- `system_tool`-Marker entfaellt durch die Trennung Tool Execution Layer
  und Kernel Capability Layer.
- `DefaultSOPResolver` und `SkillResolverPort` werden Teilnehmer einer
  Loader-Kette.
- `PolicyToolExecutionGuard` wandert in die Governance-Middleware.

## Was hier nicht passieren sollte

- Keine neuen Features in `_reference/`.
- Kein Importpfad aus `src/` zurueck nach `_reference/`. Wenn etwas
  uebernommen werden soll, wird es bewusst neu geschrieben oder kopiert,
  nicht referenziert.
