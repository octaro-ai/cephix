# Notebooks

!!! warning "Status: target model"
    This document describes the **intended target model**. The current code
    still uses `notebook.task` and `notebook.sop` as separate tools, and
    `NotebookType.USER_TASK` / `NotebookType.SOP`. Migration to the
    `work / audit × sop / skill / tool` model described here is pending.

## Core idea

Notebooks are **artefact-bound notes**. They are loaded into consciousness
only when the associated artefact (SOP, skill, tool) is currently active.

In contrast to memory — which is always loaded — notebooks are
**context-dependent**: they appear and disappear with their artefact.

## Two modes

Every notebook has exactly two modes:

| Mode | Description | User-specific? |
|---|---|---|
| **work** | Operational hints for ongoing work | Yes |
| **audit** | Improvement notes targeting the artefact itself | No |

### Work mode

For things that help Cephix resume a task:

- "Mail from company X goes to Archive, not Finance"
- "User always wants a summary for this skill"
- "This tool needs the date value in YYYY-MM-DD format"

Work entries are **user-specific**: different users can have different
operational notes on the same artefact.

### Audit mode

For things that improve the artefact itself:

- "Step 3 of the SOP is ambiguous"
- "This skill has no error case for empty input"
- "The tool gives no helpful error message on timeout"

Audit entries are **not user-specific**: they concern the artefact as such
and can later be uploaded to the repository to revise SOPs, skills, or tools.

## Three target objects

Notebooks attach to a concrete artefact:

| Target | Description | Loaded when... |
|---|---|---|
| `sop` | Standard Operating Procedure | SOP active |
| `skill` | A skill (may live inside SOPs) | Skill active |
| `tool` | A single tool | Tool active or immediately relevant |

That yields this matrix:

| | work | audit |
|---|---|---|
| **sop** | `work:sop:<id>` | `audit:sop:<id>` |
| **skill** | `work:skill:<id>` | `audit:skill:<id>` |
| **tool** | `work:tool:<id>` | `audit:tool:<id>` |

## Important rules

### Tool notes attach to the tool, not the skill

If the same tool is used in several skills, its notebook stays the same.
Otherwise, reusable tool learnings would be lost.

### Skills without SOP keep their notebooks

If a skill is later loaded directly (without a parent SOP), its work and
audit notebooks are still available.

### Artefact hierarchy: SOP → Skill → Tool

An SOP can contain skills, which can contain tools.

!!! note "Open load policy"
    Whether loading an SOP also loads *all* notebooks of contained skills
    and tools, or only those of the active path, is not yet finalised.
    Recursive loading of all nested notebooks can strain the token budget.
    The context assembler should decide selectively.

## Agent API

### `notebook.work(content, target?)`

Writes an operational note into the work notebook.

- `target` is optional: `sop`, `skill`, or `tool`
- Without `target`, the active context determines the target automatically
- User-specific: bound to `(user_id, artifact_type, artifact_id)`

Examples:

- `notebook.work("Sort mail from company X into Archive")`
  → goes to the active SOP
- `notebook.work("Set timeout to 30s", target="tool")`
  → goes to the active tool

### `notebook.audit(content, target?)`

Writes an improvement note into the audit notebook.

- `target` is optional: `sop`, `skill`, or `tool`
- Without `target`, the active context determines the target automatically
- Not user-specific: bound to `(artifact_type, artifact_id)`

Examples:

- `notebook.audit("SOP step 3 is unclear when there are multiple recipients")`
  → goes to the active SOP
- `notebook.audit("Tool returns no error on empty response", target="tool")`
  → goes to the active tool

## Load rules

The context assembler decides which notebooks are loaded into consciousness:

1. If an **SOP** is active: load `work:sop:<id>` and `audit:sop:<id>`.
2. If a **skill** is active: load `work:skill:<id>` and `audit:skill:<id>`.
3. If a **tool** is active or immediately relevant: load `work:tool:<id>`
   and `audit:tool:<id>`.
4. When an SOP is loaded, the notebooks of contained skills and tools **may
   also** be loaded (policy open, see above).
5. Work entries are filtered by `user_id`; audit entries are visible to all.

Notebook entries that are not loaded are **not lost** — they remain
reachable via `memory.search` (the subconsciousness).

## Delineation from memory

| Question | Answer |
|---|---|
| Is it generally valid? | → Memory |
| Is it artefact-bound? | → Notebook |
| Does it help next time this task runs? | → `notebook.work` |
| Does it help improve the artefact itself? | → `notebook.audit` |
| Is it user-specific and artefact-bound? | → `notebook.work` |
| Does it concern the artefact for all users? | → `notebook.audit` |

## Discarded concepts

| Concept | Status | Reason |
|---|---|---|
| `notebook.task` | Discarded as notebook axis | Was always `work@sop`. `task` survives as a **runtime concept** (later `task.plan` / `task.update`), but not as a persistent notebook axis. |
| `NotebookType.AUDIT` (old) | Discarded | Audit logging is the job of the structured log, not of notebooks |
| `NotebookType.USER` (old) | Discarded | User information belongs in memory (`scope=user`), not in notebooks |
| `NotebookType.ARTIFACT` (old) | Renamed | Became `audit@sop/skill/tool` — more precise semantics |

## Symmetry with memory

Memory and notebook share the same basic structure:

| | Memory | Notebook |
|---|---|---|
| Write | `memory.write(scope, content)` | `notebook.work/audit(content, target?)` |
| Delete | `memory.delete(scope, id)` | `notebook.delete(...)` (later) |
| Search | `memory.search(query)` | `memory.search(query)` — searches both |
| Atomic entries | Yes | Yes |
| Backend-neutral | Yes | Yes |

Internally, memory and notebook can use the same store. The split lies in
the **load policy**, not the backend:

- Memory scopes (`identity`, `user`, `memory`) are loaded **always**.
- Notebook scopes (`work:sop:*`, `audit:tool:*`, ...) are loaded **only
  when the artefact is active**.

### Convergence direction — unified API

The API symmetry is not coincidental. In the target picture, a single agent
API could cover both areas:

- `remember(scope, content)` — scope decides memory vs. notebook
- `forget(scope, identifier)` — deletes regardless of area
- `recall(query)` — searches everywhere (memory, notebooks, archive)

See [Memory › Convergence](memory.md#convergence-a-unified-knowledge-store)
for details.

## Later extensions

- **Notebook compression**: when a notebook accumulates too many entries, a
  background process can summarise older ones.
- **Promotion to memory**: recurring notebook entries can be promoted to
  memory once they prove stable.
- **Audit export**: audit entries can be exported as pull-request material
  into the SOP/skill/tool repository.
- **Notebook-driven SOP revision**: audit entries serve as input for
  automatic or manual SOP revisions.
