# ADR-0005 - Robot per user (one process per end-user)

- Status: accepted
- Date: 2026-05-30

## Context

The first non-trivial cephix workload is a mail-triage bot. It surfaces two
axes that must not be collapsed: **who** (the end-user) and **about what**
(the mailbox). This ADR decides the *who* axis only. The *about-what* axis
(multi-mailbox tenancy, `tenant_id`, `RunStatus`) is forward design, not a
made decision - it lives in
[design / mail-triage tenancy](../design/0001-mail-triage-tenancy.md).

The relevant property for the *who* axis: each user has their own
conversation history, pending decisions, and configured rules. There is no
useful global state across users.

## Decision

**Robot-to-user is 1:1 - one robot process per end-user.**

Authentication, isolation, OAuth tokens, conversation state, scheduling and
resource limits are all per-process. The builder is the multiplier: it
already produces an arbitrary number of identical robots from one spec, so
"spin up a robot for a new user" is a deployment operation, not an in-process
partitioning problem. A robot home `~/.cephix/robots/<user>/` per user is the
granularity (see [ADR-0007 - robot home](0007-robot-home-and-workspace.md)).

Why:

- **Failure isolation is free.** A crash, a tight loop, a stuck OAuth refresh
  affects exactly one user.
- **Existing primitives apply unchanged.** `CredentialProvider`,
  `SessionStore`, the lifecycle, the bus all stay single-tenant in their
  internal model. No retro-fit of per-principal state.
- **Authentication shrinks to a single decision** at the channel entry: "is
  this connecting client the owner of this robot?" - rather than a per-event
  tenant-access lookup throughout the system.
- **Scaling is multiplication, not sharding.** 1000 users = 1000 robot
  processes.

**Not chosen: in-process per-principal sharding** (one robot, many users with
a `principal` field on every event). That route forces every component to be
principal-aware, makes the credential layer much harder, and complicates
failure semantics. The simplicity of "the robot IS a user" is worth more than
the resource savings of process sharing for the workloads we care about.

## Consequences

What gets easier:

- **Multi-user infrastructure is deployment, not architecture.** The
  in-robot code stays single-user; `principal` simplifies to a static value
  derived from `robot.identity`.
- The runtime credential layer ([ADR-0004](0004-credential-layers.md))
  becomes trivial: exactly one principal whose credentials the provider
  serves, so audit and access control collapse to "is the connecting client
  this robot's user?".

What gets harder:

- **Process-per-user resource cost.** 1000 users = 1000 robot processes, each
  carrying the full bus, persistence stack, and credential layer. Fine at
  tens of users. At large scale we re-evaluate - **not** by retro-fitting
  per-principal sharding, but by a multi-robot-orchestrator layer above this
  one (out of scope here; see the design doc's open questions).

## Relationship to other ADRs

- [ADR-0004 - Credential layers](0004-credential-layers.md): robot-per-user makes
  the runtime layer trivial (one principal).
- [ADR-0007 - Robot home and workspace](0007-robot-home-and-workspace.md): the
  per-user `~/.cephix/robots/<user>/` directory is that robot's home.
- The multi-mailbox tenancy, `tenant_id`, and `RunStatus` design that builds
  on this decision is in
  [design / mail-triage tenancy](../design/0001-mail-triage-tenancy.md); decisions
  there graduate into their own ADRs as they settle.
