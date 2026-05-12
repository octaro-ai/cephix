# Notebook-System

> **Status:** Dieses Dokument beschreibt das **angestrebte Zielmodell**.
> Der aktuelle Code verwendet noch `notebook.task` und `notebook.sop` als
> getrennte Tools sowie `NotebookType.USER_TASK` / `NotebookType.SOP`.
> Die Migration auf das hier beschriebene `work/audit x sop/skill/tool`-Modell
> steht noch aus.

## Grundprinzip

Notebooks sind **artefaktgebundene Notizen**. Sie werden nur ins Bewusstsein
geladen, wenn das zugehoerige Artefakt (SOP, Skill, Tool) gerade aktiv ist.

Im Gegensatz zu Memory, das immer geladen wird, sind Notebooks
**kontextabhaengig**: sie erscheinen und verschwinden mit ihrem Artefakt.

## Zwei Modi

Jedes Notebook hat genau zwei Modi:

| Modus | Beschreibung | User-spezifisch? |
|---|---|---|
| **work** | Operative Hinweise fuer die laufende Arbeit | Ja |
| **audit** | Verbesserungsvorschlaege am Artefakt selbst | Nein |

### Work-Modus

Fuer Dinge, die Cephix bei der Wiederaufnahme einer Aufgabe helfen:

- "Mails von Firma X nach Archive, nicht Finance"
- "User moechte bei diesem Skill immer eine Zusammenfassung"
- "Bei diesem Tool muss der Datumswert im Format YYYY-MM-DD sein"

Work-Eintraege sind **user-spezifisch**: verschiedene User koennen
unterschiedliche operative Hinweise zum selben Artefakt haben.

### Audit-Modus

Fuer Dinge, die das Artefakt selbst verbessern:

- "Schritt 3 der SOP ist unklar formuliert"
- "Dieses Skill hat keinen Fehlerfall fuer leere Eingaben"
- "Das Tool gibt bei Timeout keine hilfreiche Fehlermeldung"

Audit-Eintraege sind **nicht user-spezifisch**: sie betreffen das Artefakt
als solches und koennen spaeter ins Repository hochgeladen werden, um
SOPs, Skills oder Tools zu ueberarbeiten.

## Drei Zielobjekte

Notebooks haengen an einem konkreten Artefakt:

| Zielobjekt | Beschreibung | Geladen wenn... |
|---|---|---|
| `sop` | Standard Operating Procedure | SOP aktiv |
| `skill` | Ein Skill (kann in SOPs enthalten sein) | Skill aktiv |
| `tool` | Ein einzelnes Tool | Tool aktiv oder unmittelbar relevant |

Daraus ergibt sich diese Matrix:

| | work | audit |
|---|---|---|
| **sop** | `work:sop:<id>` | `audit:sop:<id>` |
| **skill** | `work:skill:<id>` | `audit:skill:<id>` |
| **tool** | `work:tool:<id>` | `audit:tool:<id>` |

## Wichtige Regeln

### Tool-Notizen haengen am Tool, nicht am Skill

Wenn dasselbe Tool in mehreren Skills verwendet wird, bleibt sein
Notebook dasselbe. Sonst gehen wiederverwendbare Tool-Learnings verloren.

### Skills ohne SOP behalten ihre Notebooks

Wird ein Skill spaeter direkt geladen (ohne uebergeordnete SOP), sind
seine Work- und Audit-Notebooks trotzdem vorhanden.

### Artefakt-Hierarchie: SOP -> Skill -> Tool

Eine SOP kann Skills enthalten, die Tools enthalten koennen.

> **Offene Lade-Policy:** Ob beim Laden einer SOP automatisch *alle*
> Notebooks der enthaltenen Skills und Tools mitgeladen werden, oder nur
> die des gerade aktiven Pfades, ist noch nicht final entschieden.
> Rekursives Laden aller verschachtelten Notebooks kann das Tokenbudget
> belasten. Der Context-Assembler sollte hier selektiv entscheiden.

## Agent-API

### `notebook.work(content, target?)`

Schreibt eine operative Notiz ins Work-Notebook.

- `target` ist optional: `sop`, `skill` oder `tool`
- Ohne `target` bestimmt der aktive Kontext automatisch das Ziel
- User-spezifisch: wird an `(user_id, artifact_type, artifact_id)` gebunden

Beispiele:
- `notebook.work("Mails von Firma X nach Archive sortieren")`
  -> geht an die aktive SOP
- `notebook.work("Timeout auf 30s setzen", target="tool")`
  -> geht ans aktive Tool

### `notebook.audit(content, target?)`

Schreibt eine Verbesserungsnotiz ins Audit-Notebook.

- `target` ist optional: `sop`, `skill` oder `tool`
- Ohne `target` bestimmt der aktive Kontext automatisch das Ziel
- Nicht user-spezifisch: wird an `(artifact_type, artifact_id)` gebunden

Beispiele:
- `notebook.audit("Schritt 3 der SOP ist unklar bei mehreren Empfaengern")`
  -> geht an die aktive SOP
- `notebook.audit("Tool gibt bei leerer Antwort keinen Fehler", target="tool")`
  -> geht ans aktive Tool

## Lade-Regeln

Der Context-Assembler entscheidet, welche Notebooks ins Bewusstsein geladen
werden:

1. Wenn eine **SOP** aktiv ist: lade `work:sop:<id>` und `audit:sop:<id>`
2. Wenn ein **Skill** aktiv ist: lade `work:skill:<id>` und `audit:skill:<id>`
3. Wenn ein **Tool** aktiv oder unmittelbar relevant ist: lade `work:tool:<id>` und `audit:tool:<id>`
4. Wenn eine SOP geladen wird, **koennen** auch die Notebooks der enthaltenen Skills und Tools geladen werden (Lade-Policy offen, siehe oben)
5. Work-Eintraege werden nach `user_id` gefiltert; Audit-Eintraege sind fuer alle sichtbar

Nicht geladene Notebook-Eintraege sind **nicht verloren** -- sie sind ueber
`memory.search` (Unterbewusstsein) erreichbar.

## Abgrenzung zu Memory

| Frage | Antwort |
|---|---|
| Ist es allgemeingueltig? | -> Memory |
| Ist es an ein Artefakt gebunden? | -> Notebook |
| Hilft es bei der naechsten Ausfuehrung dieser Aufgabe? | -> `notebook.work` |
| Hilft es, das Artefakt selbst zu verbessern? | -> `notebook.audit` |
| Ist es user-spezifisch und artefaktgebunden? | -> `notebook.work` |
| Betrifft es das Artefakt fuer alle User? | -> `notebook.audit` |

## Verworfene Konzepte

| Konzept | Status | Begruendung |
|---|---|---|
| `notebook.task` | Als Notebook-Achse verworfen | War immer schon `work@sop`. `task` bleibt als **Laufzeitkonzept** erhalten (spaeter `task.plan` / `task.update`), aber nicht als persistente Notebook-Achse. |
| `NotebookType.AUDIT` (alt) | Verworfen | Audit-Logging ist Aufgabe des strukturierten Logs, nicht der Notebooks |
| `NotebookType.USER` (alt) | Verworfen | User-Informationen gehoeren in Memory (`scope=user`), nicht ins Notebook |
| `NotebookType.ARTIFACT` (alt) | Umbenannt | Wurde zu `audit@sop/skill/tool` -- praezisere Semantik |

## Symmetrie mit Memory

Memory und Notebook teilen dieselbe Grundstruktur:

| | Memory | Notebook |
|---|---|---|
| Schreiben | `memory.write(scope, content)` | `notebook.work/audit(content, target?)` |
| Loeschen | `memory.delete(scope, id)` | `notebook.delete(...)` (spaeter) |
| Suchen | `memory.search(query)` | `memory.search(query)` -- durchsucht beides |
| Atomare Eintraege | Ja | Ja |
| Backend-neutral | Ja | Ja |

Intern koennen Memory und Notebook denselben Store verwenden.
Die Trennung liegt in der **Lade-Policy**, nicht im Backend:

- Memory-Scopes (`identity`, `user`, `memory`) werden **immer** geladen
- Notebook-Scopes (`work:sop:*`, `audit:tool:*`, ...) werden **nur bei aktivem Artefakt** geladen

### Konvergenzrichtung: Einheitliche API

Die API-Symmetrie ist kein Zufall. Im Zielbild koennte eine einzige
Agent-API beide Bereiche abdecken:

- `remember(scope, content)` -- Scope bestimmt, ob Memory oder Notebook
- `forget(scope, identifier)` -- Loescht unabhaengig vom Bereich
- `recall(query)` -- Sucht ueberall (Memory, Notebooks, Archive)

Siehe `docs/MEMORY.md` Abschnitt "Erkannte Konvergenz" fuer Details.

## Spaetere Erweiterungen

- **Notebook-Komprimierung**: Wenn ein Notebook zu viele Eintraege hat,
  kann ein Hintergrundprozess aeltere Eintraege zusammenfassen
- **Promotion nach Memory**: Wiederkehrende Notebook-Eintraege koennen
  nach Memory promotet werden, wenn sie sich als stabil erweisen
- **Audit-Export**: Audit-Eintraege koennen als Pull-Request-Material
  ins SOP/Skill/Tool-Repository exportiert werden
- **Notebook-basierte SOP-Revision**: Audit-Eintraege dienen als
  Eingabe fuer automatische oder manuelle SOP-Ueberarbeitungen

Diskutiert in: [Memory-Analyse](c109888d-d65c-4b02-ac4e-6d3fb1d25335)
