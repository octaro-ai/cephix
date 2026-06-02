# 0005 - Robot per user, multi-mailbox tenancy within a robot

- Status: proposed
- Date: 2026-05-30

## Context

The first non-trivial cephix workload is a mail-triage bot: it
fetches mails on a heartbeat, runs them through a `RuleBasedKernel`,
and falls back to a `ChatKernel` that asks the user for a decision.
The user's reply may program a new rule, or trigger a one-shot tool
action.

Three workload properties matter for the architecture:

- **Per-user state.** Each user has their own conversation history,
  their own pending decisions, their own configured rules. There is
  no useful global state across users.
- **Multi-mailbox.** A single user often owns or accesses several
  mailboxes (personal, work, team-shared). The bot must keep
  per-mailbox status (new-message cursor, rule set, OAuth tokens)
  separate while letting the user talk to all of them through one
  conversation surface.
- **Pending decisions.** When the chat kernel has asked the user for
  a decision and is waiting, new heartbeat-driven mail batches for
  the same mailbox should not pile fresh proposals on top of the
  pending one. The system needs a first-class notion of "this run
  is awaiting input".

Two axes are present and must not be collapsed: **who** (the user)
and **about what** (the mailbox).

## Decision

Three concrete decisions, each independently justifiable but jointly
the simplest path that respects the two axes.

### 1. Robot-to-user is 1:1 (one robot process per user)

Each end-user gets their own robot process. Authentication,
isolation, OAuth tokens, conversation state, scheduling and resource
limits are all per-process. The builder is the multiplier: it
already produces an arbitrary number of identical robots from one
spec, so "spin up a robot for a new user" is a deployment
operation, not an in-process partitioning problem.

Why:

- **Failure isolation is free.** A crash, a tight loop, a stuck OAuth
  refresh affects exactly one user.
- **Existing primitives apply unchanged.** `CredentialProvider`,
  `SessionStore`, the lifecycle, the bus -- all remain
  single-tenant in their internal model. No retro-fit of
  per-principal state.
- **Authentication shrinks to a single decision** at the channel
  entry: "is this connecting client the owner of this robot?" --
  rather than a per-event tenant-access lookup throughout the
  system.
- **Builder is the multi-user layer.** A robot home
  `~/.cephix/robots/<user>/` per user is the right granularity.
  Scaling to 1000 users is 1000 robot processes, not one robot
  with 1000 sessions.

Not chosen: in-process per-principal sharding (one robot, many
users with a `principal` field on every event). That route forces
every component to be principal-aware, makes the credential layer
much harder, and complicates failure semantics. The simplicity
gained from "the robot IS a user" is worth more than the resource
savings of process sharing for the workloads we care about.

### 2. Multi-mailbox is in-robot tenancy

Within a robot, multiple **mailboxes** are first-class tenants.
`tenant_id` identifies one mailbox; the robot owns N of them in
the typical case (5-20). Implementation is **N component instances
per mailbox** rather than a single tenant-keyed registry:

```yaml
mailboxes:
  - id: personal
    credentials: env:PERSONAL_OAUTH
    heartbeat_interval: 300s
  - id: team-sales
    credentials: env:SALES_OAUTH
    heartbeat_interval: 600s
```

The builder produces, per mailbox:

- one `MailboxHeartbeat` (BUS_UTILITY, level 8)
- one `MailboxToolLayer` (new category, sits between UTILITY and
  ACTOR conceptually -- exact boot level decided when the code
  lands)
- one `RuleEngine` instance scoped to that mailbox

Shared by all mailboxes:

- one `ChatKernel` (single conversation surface)
- one `RuleBasedKernel` (routes per-mailbox events into the right
  rule engine)
- one channel
- the persistence stack

Why N-per-mailbox instead of one-keyed-by-tenant:

- Boot log shows each mailbox explicitly -- "5 mailboxes online" is
  visible as 5 startup lines, not a constructor argument.
- Per-mailbox failure isolation: a stuck OAuth refresh for mailbox
  A doesn't block mailbox B's heartbeat.
- Hot-add / hot-remove of mailboxes will naturally map to
  component lifecycle (a future iteration).
- For 5-20 mailboxes the overhead is negligible.

### 3. `tenant_id` as first-class field on `RobotEvent`

Add `tenant_id: str = ""` to `RobotEvent`. Default empty for events
not bound to a specific mailbox (system lifecycle, capability
manifest, control plane). Populated on every event a tenant-aware
producer publishes.

Why a new field instead of folding tenant into `payload` or
`principal`:

- `principal` already carries the "who did this" identity. Mixing
  in "about which mailbox" creates a parsed-string field where one
  was clean before.
- `payload` is consumer-specific. Cross-cutting subscribers
  (telemetry, audit, capability collector) need to filter on tenant
  without knowing the topic-specific payload shape.
- The persistence stack should be able to fan out per-tenant
  (`logs/tenant=<id>/...`) cheaply -- that requires a structured
  field, not a dict lookup.

Producers that must populate it:

- `MailboxHeartbeat` -- every tick.
- `MailboxToolLayer` -- every batch announcement, every tool
  invocation.
- `RuleBasedKernel` -- every forward to chat.
- `ChatKernel` -- every output that belongs to a mailbox-scoped
  conversation.

### 4. `RunStatus` as a first-class retained event

A new event type:

```python
@dataclass(frozen=True, kw_only=True)
class RunStatus(RobotEvent):
    phase: Literal["executing", "awaiting_user", "completed", "abandoned"]
    waiting_on: str = ""           # enumerated vocabulary -- see below
    context_summary: str = ""      # one-line human description
    # principal, tenant_id, run_id inherited from RobotEvent base
```

Topic convention: `run.status.<run_id>`. Retained, so a late
subscriber (channel reconnect, restart-recovery worker) sees the
current state of every in-flight run with a single
`bus.retained(...)` pass.

`waiting_on` is an enumerated vocabulary, not free text:

- `"user.confirm"` -- waiting for an approve/deny on a proposal
- `"user.input"` -- waiting for free-form text
- `"tool.response"` -- waiting for a tool layer's reply
- `"approval"` -- waiting for an external approver (future)

Producers:

- `ChatKernel` publishes phase transitions for the runs it owns
  (start, awaiting-user, completion, abandonment).
- The robot publishes `abandoned` for every in-flight run during
  shutdown.

Consumers:

- `RuleBasedKernel`: before forwarding a fresh `mail.batch` for
  `(tenant_id, principal)` to the chat kernel, checks
  `bus.retained` for an active `awaiting_user` run on that pair.
  If present, queues the batch locally and retries on the next
  heartbeat. **The chat kernel does not drop unwanted inputs --
  the producer self-filters.**
- Channel: at welcome time and on retained updates, the channel
  reads all `run.status.*` slots for the connected user's tenants
  and renders pending decisions into the per-user frame.
- Audit: each phase transition is auditable.

### 5. Channel-side state assembly (not capability mutation)

The retained `HarnessCapabilities` manifest stays **global to the
robot** -- it describes what the robot *can* do, not what is
currently in flight. Status-driven UI selection happens at the
channel:

```
welcome frame = filter(harness.capabilities, principal-relevant)
              + read(bus.retained("run.status.*"))
              + tenant access list
              + status-driven action subset
```

Capability components stay static; the channel composes the
per-user view. Pending-driven action subsets (e.g. "show
confirm/deny instead of free input when `awaiting_user`") are
channel logic, not capability-manifest logic.

## Consequences

What gets easier:

- **Multi-user infrastructure is deployment**, not architecture. The
  in-robot code stays single-user. `principal` simplifies to a
  static value derived from `robot.identity`.
- **Mailbox tenancy is component-level**, visible in the boot log
  and the manifest, lifecycle-managed by the existing skeleton.
- **`RunStatus` decouples producers and consumers**: the rule
  kernel and the chat kernel never directly negotiate "can I send
  you this now" -- the rule kernel reads a public retained slot
  and decides locally. This generalises to any future kernel that
  needs to avoid stepping on a pending run (a future LLM-Planner,
  a Notifier, etc.).
- **Channel-side state assembly** keeps the capability story
  simple and the UI flexible. Different channels (websocket today,
  future TUI, future mail channel) compose their own renderings
  off the same bus state.

What gets harder:

- **Process-per-user resource cost.** 1000 users = 1000 robot
  processes. Each carries the full bus, persistence stack,
  credential layer. At small scale (tens of users) this is fine.
  At large scale we will re-evaluate -- but not by retro-fitting
  per-principal sharding, rather by a multi-robot-orchestrator
  layer above this one.
- **`tenant_id` schema bump.** Every event type that inherits
  `RobotEvent` gains a field. Default-empty keeps existing code
  working, but the persistence stack and external readers must be
  re-built against the new schema.
- **Restart-recovery for pending runs is non-trivial** (see "Not
  decided" below).

## Not decided (future ADRs)

- **Run persistence across restart.** Does an `awaiting_user` run
  survive a robot restart, or is it abandoned? The latter is
  cheaper; the former requires writing `RunStatus` snapshots to
  the session store on every transition and replaying them on
  boot. Defer until we have a real user need.
- **Concurrency per user**. Can the same user have multiple
  `awaiting_user` runs in flight across different mailboxes?
  Likely yes (one per mailbox), but the UI ramifications and
  conflict-resolution rules need their own ADR.
- **Status TTL / cleanup**. Retained `run.status.*` slots
  accumulate over time. Eviction policy (TTL, explicit clear on
  `completed`, cleared on user-acknowledge) is a separate
  decision.
- **`MailboxToolLayer` boot level**. Whether tool layers are
  ACTOR-tier (consumed by kernels) or a new category between
  UTILITY and ACTOR. Decided when the first ToolLayer lands.
- **Multi-robot orchestrator layer** for scale beyond ~tens of
  users. Out of scope here; the present ADR explicitly punts on
  it by choosing the simpler per-user-process model.

## Relationship to other ADRs

- [0003 - Wire-format codec is not abstracted](0003-no-codec-abstraction-yet.md):
  rejects a pattern because the use case is speculative.
- [0004 - Credential layers: build-time, preboot, runtime](0004-credential-layers.md):
  separates lifetimes. Robot-per-user makes the runtime layer
  trivial -- there is exactly one principal whose credentials the
  provider serves, so audit and access control collapse to "is
  the connecting client this robot's user?".
- This ADR (0005) is the first that introduces a workload-shaped
  decision (mail triage). The earlier ADRs were structural. This
  one binds the architecture to a concrete first user.
