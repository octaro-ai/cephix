# Memory-System

> **Status:** Dieses Dokument beschreibt das **angestrebte Zielmodell**.
> Der aktuelle Code (`src/tools/system_tools.py`, `src/context.py`) verwendet
> noch die aelteren Tool-Namen (`memory.write_document`, `memory.read_document`,
> `notebook.task`, `notebook.sop`). Die Migration auf das hier beschriebene
> Modell steht noch aus.

## Grundprinzip

Memory ist das **globale Langzeitgedaechtnis** des Roboters.
Es speichert stabile, kontextuebergreifende Erinnerungen, die nicht an ein
einzelnes Artefakt (SOP, Skill, Tool) gebunden sind.

Memory wird **immer ins Bewusstsein geladen** -- unabhaengig davon, welche
SOP, welches Skill oder welches Tool gerade aktiv ist.

## Bewusstseinsmodell

| Schicht | Beschreibung |
|---|---|
| **Bewusstsein** | Alles, was in den aktuellen Prompt geladen wird |
| **Kurzzeitgedaechtnis** | Chat-History der laufenden Konversation |
| **Memory** | Dauerhaftes Wissen -- wird immer ins Bewusstsein geladen |
| **Unterbewusstsein** | Archivierte Verlaeufe, alte Notebooks, rotiertes Memory -- nur per Search erreichbar |

### Zusammensetzung des Bewusstseins

Das Bewusstsein ist kein einzelner Speicher, sondern die Summe aus
allem, was der Context-Assembler in den aktuellen Prompt laedt:

1. **Firmware** -- unveraenderliche Leitplanken (immer geladen)
2. **Memory** -- globales Wissen: `identity`, `user`, `memory` (immer geladen)
3. **Aktive Notebooks** -- `work`/`audit`-Eintraege der gerade aktiven
   SOPs, Skills und Tools (nur bei aktivem Artefakt geladen)
4. **Chat-History** -- der laufende Gespraechsverlauf (unterliegt Compaction)

Daraus folgt: `memory.read` ist obsolet -- das LLM muss sein eigenes
Memory nicht per Tool abfragen, weil es bereits im Prompt steht.

Compaction betrifft **ausschliesslich die Chat-History**. Memory-Eintraege
sind davon nie betroffen. Sie verhalten sich wie Firmware, die der Roboter
sich selbst schreibt.

## Scopes

Jeder Memory-Eintrag gehoert zu einem Scope. Der Scope bestimmt den
semantischen Zielbereich:

| Scope | Beschreibung | Beispiel |
|---|---|---|
| `identity` | Wer bin ich, wie arbeite ich, mein Stil | "Ich spreche direkt und knapp" |
| `user` | Was weiss ich ueber den User | "User bevorzugt kurze Antworten" |
| `memory` | Allgemeine dauerhafte Regeln und Fakten | "Kritische Rechnungen haben Prioritaet" |
| `bootstrap` | Einmalige Onboarding-Informationen (wird nach Verarbeitung geloescht) | Onboarding-Skript |

Die Scopes `identity`, `user` und `memory` werden bei jedem Turn komplett
ins Bewusstsein geladen. `bootstrap` wird nur geladen, solange die Datei
existiert.

## Agent-API

Das LLM interagiert mit Memory ueber drei Tools:

### `memory.write(scope, content)`

Speichert eine stabile Erinnerung im passenden Scope.

- `scope` bestimmt den Zielbereich (`identity`, `user`, `memory`)
- `content` ist der atomare Fakt oder die Erinnerung

Beispiele:
- `memory.write(scope="user", content="User wird mit Du angesprochen")`
- `memory.write(scope="identity", content="Mein Name ist Aria")`
- `memory.write(scope="memory", content="Rechnungen von Firma X haben Prioritaet")`

### `memory.delete(scope, identifier)`

Entfernt eine Erinnerung oder ein Dokument.

- Fuer Einzelfakten: loescht den Eintrag
- Fuer Bootstrap: loescht das Onboarding-Dokument nach Verarbeitung

### `memory.search(query)`

Durchsucht das **Unterbewusstsein** -- also alles, was nicht aktuell im
Bewusstsein (Prompt) geladen ist:

- Archivierte Chat-Verlaeufe (nach Compaction)
- Alte Notebook-Eintraege
- Rotierte oder verdraengte Memory-Eintraege (spaeter)

Typischer Anwendungsfall: "Worueber hatten wir letzte Woche gesprochen?"

## Backend-Transparenz

Memory ist ein **Port**. Ob dahinter Markdown-Dateien, eine Datenbank oder
ein Key-Value-Store steckt, ist Implementierungsdetail. Die Agent-API
aendert sich nicht.

## Abgrenzung

| Konzept | Gehoert zu Memory? | Grund |
|---|---|---|
| User-Praeferenzen | Ja | Global, nicht artefaktgebunden |
| Robot-Identitaet | Ja | Global, nicht artefaktgebunden |
| Allgemeine Regeln | Ja | Global, nicht artefaktgebunden |
| SOP-spezifische Hinweise | **Nein** | Gehoert ins Notebook (`work@sop`) |
| Tool-Fehler-Workarounds | **Nein** | Gehoert ins Notebook (`audit@tool`) |
| Chat-History | **Nein** | Eigene Schicht, unterliegt Compaction |

## Verworfene Konzepte

| Konzept | Status | Begruendung |
|---|---|---|
| `core_memory` | Ersatzlos verworfen | Memory wird nie kompaktiert -- ein separater "geschuetzter" Bereich ist unnoetig |
| `memory.read` | Verworfen | Memory wird automatisch ins Bewusstsein geladen -- ein explizites Lese-Tool ist redundant |
| `memory.write_document` | Verworfen | Backend-Transparenz: `memory.write(scope, content)` reicht, ob dahinter Dateien oder DB steckt ist egal |
| `memory.delete_document` | Verworfen | Ersetzt durch `memory.delete(scope, identifier)` |
| `document.*` Tools | Verworfen | Waren technisch (dateibasiert), nicht semantisch -- ersetzt durch `memory.write` mit Scopes |

## Compaction und Archivierung

- Compaction betrifft **nur die Chat-History**, nie Memory
- Vor der Compaction sollte ein **Pre-Compaction-Flush** stattfinden, der
  dem Modell die Moeglichkeit gibt, wichtige Informationen aus dem Verlauf
  noch als Memory oder Notebook-Eintraege zu sichern (noch nicht implementiert)
- Die rohen, unkomprimierten Verlaeufe werden dauerhaft archiviert und
  sind ueber `memory.search` erreichbar (Unterbewusstsein)

## Erkannte Konvergenz: Einheitlicher Wissens-Store

Im Verlauf der Analyse hat sich gezeigt, dass Memory und Notebook
**nicht zwei verschiedene Systeme** sind, sondern **zwei Sichten auf
denselben Speicher** mit unterschiedlichen Lade-Policies.

Der Unterschied ist nicht technisch, sondern:

- **Wann** wird es automatisch geladen?
- **Woran** ist es gebunden?

Daraus folgt als logische Zielperspektive eine **einheitliche Agent-API**:

| Tool | Zweck |
|---|---|
| `remember(scope, content)` | Speichert einen Eintrag -- ob ins Memory oder Notebook haengt am Scope |
| `forget(scope, identifier)` | Loescht einen Eintrag |
| `recall(query)` | Durchsucht das gesamte Unterbewusstsein (Memory, Notebooks, archivierte Verlaeufe) |

Dabei bestimmt der Scope die Lade-Policy:

- `identity`, `user`, `memory` -- immer ins Bewusstsein geladen
- `work:sop:<id>`, `audit:sop:<id>` -- geladen wenn SOP aktiv
- `work:skill:<id>`, `audit:skill:<id>` -- geladen wenn Skill aktiv
- `work:tool:<id>`, `audit:tool:<id>` -- geladen wenn Tool aktiv

Die Trennung Memory vs Notebook existiert dann nur noch im
**Context-Assembler**, nicht mehr in der Agent-API.

> **Hinweis:** Ob die finale Implementierung die einheitliche API oder
> die zwei getrennten Tool-Familien (`memory.write` + `notebook.work/audit`)
> verwendet, ist noch offen. Beide Varianten sind mit dem Scope-Modell
> kompatibel. Die einheitliche API ist das sauberere Zielmodell, die
> getrennte Variante ist moeglicherweise fuer das LLM intuitiver.

## Spaetere Erweiterungen

- **Dreaming**: Hintergrundprozess, der aus Notebook-Eintraegen und
  archivierten Verlaeufen qualifizierte Kandidaten nach Memory promotet
- **Selektives Laden**: Wenn Memory waechst, entscheidet der Context-Assembler,
  welche Eintraege ins Bewusstsein geladen werden und welche nur per Search
  erreichbar bleiben
- **Subagenten-Recall**: Subagenten koennen `memory.search` nutzen, um
  Informationen aus dem Unterbewusstsein fuer spezialisierte Aufgaben abzurufen

## Referenz: OpenClaw-Vergleich

Die Architektur orientiert sich an Erkenntnissen aus der OpenClaw-Analyse:

- OpenClaw injiziert `MEMORY.md` komplett in den System-Prompt (entspricht
  unserem "immer ins Bewusstsein laden")
- OpenClaw hat keinen eigenen Memory-Write-Tooltyp, sondern nutzt generische
  File-Tools. Cephix abstrahiert das ueber `memory.write(scope, content)`
- OpenClaws Pre-Compaction Memory Flush ist das Vorbild fuer unseren
  geplanten Flush-Mechanismus
- OpenClaws Dreaming-System (experimentell) validiert den Pfad
  "kurzfristig speichern, spaeter promoten"

Diskutiert in: [Memory-Analyse](c109888d-d65c-4b02-ac4e-6d3fb1d25335)
