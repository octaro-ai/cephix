# Memory

!!! warning "Status: target model"
    This document describes the **intended target model**. The current code
    (`src/tools/system_tools.py`, `src/context.py`) still uses the older tool
    names (`memory.write_document`, `memory.read_document`, `notebook.task`,
    `notebook.sop`). Migration to the model described here is pending.

## Core idea

Memory is the **global long-term store** of the robot. It holds stable,
cross-context recollections that are not tied to a single artefact (SOP,
skill, tool).

Memory is **always loaded into consciousness** — regardless of which SOP,
skill, or tool is currently active.

## Model of consciousness

| Layer | Description |
|---|---|
| **Consciousness** | Everything loaded into the current prompt |
| **Short-term memory** | Chat history of the running conversation |
| **Memory** | Permanent knowledge — always loaded into consciousness |
| **Subconsciousness** | Archived conversation tails, old notebooks, rotated memory — reachable only via search |

### Composition of consciousness

Consciousness is not a single store; it is the sum of everything the context
assembler loads into the current prompt:

1. **Firmware** — immutable guardrails (always loaded)
2. **Memory** — global knowledge: `identity`, `user`, `memory` (always loaded)
3. **Active notebooks** — `work` / `audit` entries of the currently active
   SOPs, skills, and tools (loaded only when the artefact is active)
4. **Chat history** — the running conversation (subject to compaction)

A consequence: `memory.read` is obsolete. The LLM does not need to query its
own memory via a tool, because memory is already in the prompt.

Compaction affects **only the chat history**. Memory entries are never
compacted. They behave like firmware that the robot writes for itself.

## Scopes

Every memory entry belongs to a scope. The scope determines the semantic area:

| Scope | Description | Example |
|---|---|---|
| `identity` | Who I am, how I work, my style | "I speak directly and concisely" |
| `user` | What I know about the user | "User prefers short answers" |
| `memory` | General permanent rules and facts | "Critical invoices have priority" |
| `bootstrap` | One-shot onboarding information (deleted after processing) | Onboarding script |

The scopes `identity`, `user`, and `memory` are loaded into consciousness
every turn. `bootstrap` is loaded only while the file exists.

## Agent API

The LLM interacts with memory through three tools.

### `memory.write(scope, content)`

Stores a stable recollection in the appropriate scope.

- `scope` determines the target area (`identity`, `user`, `memory`)
- `content` is the atomic fact or recollection

Examples:

- `memory.write(scope="user", content="Address the user with 'Du'")`
- `memory.write(scope="identity", content="My name is Aria")`
- `memory.write(scope="memory", content="Invoices from company X have priority")`

### `memory.delete(scope, identifier)`

Removes a recollection or document.

- For single facts: deletes the entry
- For bootstrap: deletes the onboarding document after processing

### `memory.search(query)`

Searches the **subconsciousness** — everything that is not currently in
consciousness (the prompt):

- Archived conversation tails (after compaction)
- Old notebook entries
- Rotated or displaced memory entries (later)

Typical use: "What did we talk about last week?"

## Backend transparency

Memory is a **port**. Whether the backing store is Markdown files, a
database, or a key-value store is an implementation detail. The agent API
does not change.

## Delineation

| Concept | Belongs in memory? | Reason |
|---|---|---|
| User preferences | Yes | Global, not artefact-bound |
| Robot identity | Yes | Global, not artefact-bound |
| General rules | Yes | Global, not artefact-bound |
| SOP-specific hints | **No** | Belongs in the notebook (`work@sop`) |
| Tool-error workarounds | **No** | Belongs in the notebook (`audit@tool`) |
| Chat history | **No** | Separate layer, subject to compaction |

## Discarded concepts

| Concept | Status | Reason |
|---|---|---|
| `core_memory` | Dropped without replacement | Memory is never compacted — a separate "protected" area is unnecessary |
| `memory.read` | Discarded | Memory is loaded into consciousness automatically — an explicit read tool is redundant |
| `memory.write_document` | Discarded | Backend transparency: `memory.write(scope, content)` is enough, regardless of whether files or a DB sit behind it |
| `memory.delete_document` | Discarded | Replaced by `memory.delete(scope, identifier)` |
| `document.*` tools | Discarded | Were technical (file-based), not semantic — replaced by `memory.write` with scopes |

## Compaction and archival

- Compaction affects **only the chat history**, never memory.
- Before compaction, a **pre-compaction flush** should occur, giving the
  model an opportunity to preserve important information from the
  conversation as memory or notebook entries (not yet implemented).
- The raw, uncompressed conversation tails are permanently archived and
  reachable via `memory.search` (the subconsciousness).

## Convergence — a unified knowledge store

Over the course of analysis it became clear that memory and notebook are
**not two different systems**, but **two views of the same store** with
different load policies.

The distinction is not technical, but:

- **When** is it loaded automatically?
- **What** is it bound to?

Logically, the target perspective converges on a **unified agent API**:

| Tool | Purpose |
|---|---|
| `remember(scope, content)` | Store an entry — memory or notebook depends on the scope |
| `forget(scope, identifier)` | Delete an entry |
| `recall(query)` | Search the entire subconsciousness (memory, notebooks, archived tails) |

The scope determines the load policy:

- `identity`, `user`, `memory` — always loaded into consciousness
- `work:sop:<id>`, `audit:sop:<id>` — loaded when SOP is active
- `work:skill:<id>`, `audit:skill:<id>` — loaded when skill is active
- `work:tool:<id>`, `audit:tool:<id>` — loaded when tool is active

The split between memory and notebook then exists only in the
**context assembler**, no longer in the agent API.

!!! note
    Whether the final implementation uses the unified API or two separate
    tool families (`memory.write` + `notebook.work/audit`) is still open.
    Both variants are compatible with the scope model. The unified API is
    the cleaner target model; the separated variant may be more intuitive
    for the LLM.

## Later extensions

- **Dreaming**: a background process that promotes qualifying candidates
  from notebook entries and archived conversations into memory.
- **Selective loading**: as memory grows, the context assembler decides
  which entries are loaded into consciousness and which remain reachable
  only via search.
- **Sub-agent recall**: sub-agents can use `memory.search` to retrieve
  information from the subconsciousness for specialised tasks.

## Reference — OpenClaw comparison

The architecture is informed by lessons from analysing OpenClaw:

- OpenClaw injects `MEMORY.md` entirely into the system prompt — equivalent
  to our "always load into consciousness".
- OpenClaw has no dedicated memory-write tool type; it uses generic file
  tools. Cephix abstracts this via `memory.write(scope, content)`.
- OpenClaw's pre-compaction memory flush is the inspiration for our planned
  flush mechanism.
- OpenClaw's dreaming system (experimental) validates the
  "store short-term, promote later" path.
