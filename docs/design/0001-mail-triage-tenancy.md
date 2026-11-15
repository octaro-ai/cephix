# FRD-0001 - Mail-triage tenancy (multi-mailbox)

- Status: design / not yet built
- Builds on: [ADR-0005 - Robot per user](../adr/0005-robot-per-user-and-multi-mailbox-tenancy.md) (accepted)
- Origin: extracted from ADR-0005 (decisions 2-5 + "not decided") when ADRs
  were narrowed to record only made decisions.

The accepted decision is settled: **one robot process per user** (ADR-0005).
This document designs the layer *inside* one robot: how a single user's many
mailboxes are represented. None of it is built yet; it is the intended shape
for the first non-trivial workload (a mail-triage bot) and the open questions
below are still open.

## Workload

A mail-triage bot fetches mail on a heartbeat, runs each batch through a
`RuleBasedKernel`, and falls back to a `ChatKernel` that asks the user for a
decision. The reply may program a new rule or trigger a one-shot tool action.

Two axes must not be collapsed: **who** (the user - settled as 1 robot =
1 user) and **about what** (the mailbox - the subject of this design).

Properties that shape the design:

- **Multi-mailbox.** One user often owns several mailboxes (personal, work,
  team-shared). Per-mailbox status (new-message cursor, rule set, OAuth
  tokens) stays separate while the user talks to all of them through one
  conversation surface.
- **Pending decisions.** When the chat kernel is waiting on the user, new
  heartbeat batches for the same mailbox must not pile fresh proposals on
  the pending one. The system needs a first-class "this run is awaiting
  input" notion.

## Proposed design

### 1. Multi-mailbox as in-robot tenancy

Within a robot, each **mailbox** is a first-class tenant. `tenant_id`
identifies one mailbox; a robot owns N (typically 5-20). Implementation is
**N component instances per mailbox** rather than one tenant-keyed registry:

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
- one `MailboxToolLayer` (boot level still open - see below)
- one `RuleEngine` scoped to that mailbox

Shared by all mailboxes: one `ChatKernel` (single conversation surface), one
`RuleBasedKernel` (routes per-mailbox events into the right rule engine), one
channel, the persistence stack.

Why N-per-mailbox over one-keyed-by-tenant: each mailbox shows up explicitly
in the boot log; per-mailbox failure isolation (a stuck OAuth refresh for A
does not block B); hot-add/remove maps naturally to component lifecycle. For
5-20 mailboxes the overhead is negligible.

### 2. `tenant_id` as a first-class field on `RobotEvent`

Add `tenant_id: str = ""` to `RobotEvent`. Empty for events not bound to a
mailbox (system lifecycle, capability manifest, control plane); populated on
every event a tenant-aware producer publishes.

Why a new field, not folded into `payload` or `principal`: `principal` is
"who did this"; mixing in "about which mailbox" makes a parsed-string field.
`payload` is consumer-specific, but cross-cutting subscribers (telemetry,
audit, capability collector) need to filter on tenant without knowing the
payload shape. Per-tenant persistence fan-out (`logs/tenant=<id>/...`) needs
a structured field, not a dict lookup.

Producers that populate it: `MailboxHeartbeat` (every tick),
`MailboxToolLayer` (every batch announcement / tool invocation),
`RuleBasedKernel` (every forward to chat), `ChatKernel` (every output bound
to a mailbox-scoped conversation).

### 3. `RunStatus` as a first-class retained event

```python
@dataclass(frozen=True, kw_only=True)
class RunStatus(RobotEvent):
    phase: Literal["executing", "awaiting_user", "completed", "abandoned"]
    waiting_on: str = ""           # enumerated vocabulary - see below
    context_summary: str = ""      # one-line human description
    # principal, tenant_id, run_id inherited from RobotEvent base
```

Topic `run.status.<run_id>`, retained, so a late subscriber (channel
reconnect, restart-recovery worker) sees every in-flight run with a single
`bus.retained(...)` pass.

`waiting_on` is an enumerated vocabulary, not free text:

- `"user.confirm"` - approve/deny on a proposal
- `"user.input"` - free-form text
- `"tool.response"` - a tool layer's reply
- `"approval"` - an external approver (future)

Producers: `ChatKernel` publishes phase transitions for runs it owns; the
robot publishes `abandoned` for every in-flight run during shutdown.

Consumers:

- `RuleBasedKernel`: before forwarding a fresh `mail.batch` for
  `(tenant_id, principal)`, checks `bus.retained` for an active
  `awaiting_user` run on that pair. If present, queues locally and retries
  next heartbeat. **The producer self-filters; the chat kernel never drops
  unwanted inputs.**
- Channel: at welcome time and on retained updates, reads all
  `run.status.*` slots for the connected user's tenants and renders pending
  decisions into the per-user frame.
- Audit: each phase transition is auditable.

### 4. Channel-side state assembly (not capability mutation)

The retained `HarnessCapabilities` manifest stays **global to the robot** -
it describes what the robot *can* do, not what is in flight. Status-driven
UI selection happens at the channel:

```
welcome frame = filter(harness.capabilities, principal-relevant)
              + read(bus.retained("run.status.*"))
              + tenant access list
              + status-driven action subset
```

Capability components stay static; the channel composes the per-user view.
Pending-driven action subsets ("show confirm/deny instead of free input when
`awaiting_user`") are channel logic, not capability-manifest logic.

## Open questions

These graduate into ADRs as they are settled.

- **Run persistence across restart.** Does an `awaiting_user` run survive a
  robot restart, or is it abandoned? Abandoning is cheaper; surviving means
  writing `RunStatus` snapshots to the session store on every transition and
  replaying on boot. Defer until a real user need.
- **Concurrency per user.** Can one user have multiple `awaiting_user` runs
  across mailboxes? Likely yes (one per mailbox), but UI ramifications and
  conflict-resolution need deciding.
- **Status TTL / cleanup.** Retained `run.status.*` slots accumulate.
  Eviction policy (TTL, clear on `completed`, clear on user-acknowledge) is
  undecided.
- **`MailboxToolLayer` boot level.** ACTOR-tier (consumed by kernels) or a
  new category between UTILITY and ACTOR? Decided when the first ToolLayer
  lands.
- **Multi-robot orchestrator** for scale beyond ~tens of users. Out of
  scope; ADR-0005 deliberately punts on it by choosing per-user-process.
