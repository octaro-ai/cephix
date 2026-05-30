# 0006 - Central time source: one clock, one timezone, no library defaults

- Status: proposed
- Date: 2026-05-30

## Context

The first cron-driven component (`HeartbeatChannel`) shipped with a
two-line bug that took a live boot to surface: croniter was anchored
on a naive `datetime.now()`, croniter treats naive datetimes as UTC,
the next-fire result was a UTC epoch -- but the wait loop compared it
against `time.time()` (also UTC epoch). In MESZ (UTC+2) every
"next minute" landed ~7200s in the future. The cron loop happily
slept for two hours instead of firing at the next minute boundary.

The fix was trivial (anchor croniter on `time.time()` so both sides
of the comparison sit in the same reference frame), but the bug
class is not. Three independent time concepts are at play across
the robot and they all look the same until they don't:

- **Wall-clock**: `datetime.now()` (naive, local) -- what humans
  read on the clock on the wall. Library-dependent: some libs
  interpret naive as UTC, some as local, some refuse.
- **UTC instant**: `datetime.now(UTC)` / `time.time()` -- one
  unambiguous point on the timeline, no timezone semantics.
- **Monotonic**: `time.monotonic()` -- a steadily-increasing
  number with no relation to wall-clock; used for measuring
  *durations* without being lied to by NTP corrections, DST
  transitions, leap seconds, or the user manually changing the
  system clock.

The existing codebase silently encoded "we use UTC everywhere":
`RobotEvent.timestamp` is built by [`_now_iso()`][_now_iso] which
calls `datetime.now(UTC).isoformat()`, telemetry.jsonl entries
carry `+00:00`, audit.jsonl too. But that convention lived only
in one helper and was never written down; each new component
re-decided what "now" means on its own. The heartbeat got it
wrong by importing `datetime` and calling `.now()` -- a perfectly
ordinary line of Python that happened to disagree with the rest
of the system.

A grep of `datetime.now`, `time.time`, `time.monotonic`, and
`tzinfo` across `src/` finds the call sites in seven files
already, and the count will only grow as scheduling, retention
policies, TTLs, replay windows, idle detection, rate limiting,
and time-bucketed metrics arrive. Each of those is a place where
"naive vs aware", "local vs UTC", and "lib-default timezone"
risk a one-line bug like the heartbeat one. The next bug will
not be as visible -- a timestamp 5 minutes off in an audit
record, or a session that "ages out" 8 hours too early, will
slip through any test that does not assert on exact instants.

[_now_iso]: ../../src/bus/messages.py

## Decision

Cephix gets **one canonical time source** all components consult,
under a `ClockPort` (or whatever the eventual name turns out to
be in code -- the ADR commits to the *concept*, not the spelling).
Three rules, in priority order:

### 1. All wall-clock time is UTC

Every wall-clock-derived value the robot ever computes, persists,
emits, or compares is a UTC instant. This is already the
de-facto convention -- `_now_iso` emits `+00:00`, the bus stack
reads `+00:00` back. The ADR makes it explicit:

- `datetime.now()` (naive) is forbidden in robot code. Use
  `datetime.now(UTC)` if you need a `datetime`, `time.time()` if
  you need an epoch float.
- `datetime.utcnow()` is also forbidden -- it returns a *naive*
  datetime which loses the UTC tag the moment it gets serialized
  or compared. `datetime.now(UTC)` is the only correct spelling.
- Cron expressions are interpreted in UTC. `0 8 * * *` means
  "8 AM UTC", not "8 AM in whatever timezone the operator's
  laptop happens to be in". If a user-facing scheduler later
  wants local-time semantics, that translation happens once at
  the YAML-parse boundary, never inside scheduling primitives.
- ISO-8601 strings are always serialized with an explicit `+00:00`
  (or `Z`) suffix. Naive ISO strings are not accepted on the bus.

### 2. Wall-clock and duration are different functions

The clock port exposes (at least) two methods, and they answer
different questions:

- `now()` -> wall-clock UTC instant. For "what timestamp goes on
  this event", "is this token expired", "when is the next cron
  fire", "what bucket does this metric belong to".
- `monotonic()` -> a steadily-increasing seconds value with no
  wall-clock relationship. For "how long did this take",
  "schedule me a wakeup N seconds from now", "rate-limit window",
  "watchdog timeout".

Mixing them is a category error. `time.time()` going backwards
during an NTP correction breaks a sleep-until-deadline loop;
`time.monotonic()` does not. Conversely, `monotonic()` cannot
be put on a `RobotEvent.timestamp` because it has no calendar
meaning.

Internal scheduling (the heartbeat tick loop, future actor
timeouts, future watchdog) **must** use `monotonic()` for the
"sleep until" math, and **must** use `now()` for the
`event.timestamp` they emit. The heartbeat fix in this iteration
satisfies the second half but not yet the first; that is
follow-up work, not a regression.

### 3. The robot owns the clock; libraries do not

No library may be allowed to silently inject its own time
notion. Two specific instances of the failure mode this rule
exists to prevent:

- **croniter naive-datetime-is-UTC**. Caught this iteration.
- **logging `%(asctime)s`**. By default uses local-time strings;
  trivially inconsistent with the UTC instant on the same
  RobotEvent in the same record. Out of scope for the immediate
  fix; called out so it does not surprise us later.

Components that take a clock-shaped dependency (anything that
schedules, anything that timestamps, anything that times-out,
anything that ages records) accept the `ClockPort` by DI rather
than reaching for `import time` / `import datetime`. The
boot-time wiring guarantees there is exactly one clock instance
in the robot, with one timezone (UTC) and one monotonic source.

Concrete consequence for tests: instead of sprinkling
`unittest.mock.patch("time.time", ...)` across the suite, tests
construct a `ManualClock` (or `FrozenClock`) and inject it.
Scheduling, expiry, and ordering tests stop depending on
wall-clock sleeps and become deterministic.

## Consequences

What gets easier:

- **No more "every component decides what now means"**. The
  heartbeat bug is structurally impossible once `datetime.now()`
  in robot code is a lint failure and the only path to "now" is
  through the clock port.
- **Deterministic time-aware tests**. `ManualClock.advance(60)`
  lets us assert "after one minute the heartbeat has fired
  exactly once" without sleeping, without flaky timing, without
  croniter timezone math sneaking into the test.
- **Audit consistency**. Every timestamp on every record across
  telemetry, audit, sessions, run-status, and bus traffic shares
  the same epoch -- comparing across streams is byte-for-byte
  meaningful.
- **TZ becomes a presentation concern, not a robot concern**.
  Channels render to the operator's local time on the way out
  if they want to; the robot internals never have to translate.

What gets harder:

- **Boot-time wiring gains another port**. The clock is a
  CONNECTION-tier or UTILITY-tier component (exact level
  decided in the implementation ADR), and every scheduling /
  timestamping component now takes it by constructor injection.
  Three call sites to swap out today (`_now_iso`, the heartbeat
  loop, the kernel run anchors); more will arrive.
- **Lint / review burden**. Banning `datetime.now()` (naive),
  `datetime.utcnow()`, and unbounded `import time` requires a
  ruff rule (or at least a pre-commit grep) and a code-review
  habit. The ADR is the rationale we point to when "but I just
  need a quick timestamp" comes up.
- **Cron is UTC**. Operators editing `heartbeats.yaml` in a
  non-UTC timezone need to know that `0 8 * * *` is "8 AM UTC",
  not their local clock. The YAML must say so, the firmware
  prompt for any cron-introducing agent must say so, and a
  future "local-time cron" flag (per-entry `tz: Europe/Berlin`)
  is a follow-up ADR if the friction warrants it.

## Migration steps (informational, not part of the decision)

These are the in-tree actions that follow from this ADR; they are
not the decision itself.

1. Introduce a `ClockPort` ABC with `now()` and `monotonic()`,
   and a `SystemClock` default that delegates to
   `datetime.now(UTC)` and `time.monotonic()`.
2. Wire it through DI for `_now_iso`, the heartbeat tick loop,
   the kernel run anchors, and any other current call site
   surfaced by the grep above.
3. Add a `ManualClock` to the test helpers.
4. Add a ruff rule (or `forbid` regex in CI) that rejects naive
   `datetime.now()` and `datetime.utcnow()` in `src/`.
5. Audit-pass the logging configuration so log records use UTC
   too (so a grep across telemetry + log file lines up).

## Relationship to other ADRs

- [0005 - Robot per user, multi-mailbox tenancy](0005-robot-per-user-and-multi-mailbox-tenancy.md):
  introduced `RunStatus` retained events. Run-state TTL, retry
  back-off, and "abandoned at boot" decisions all need a clock;
  the present ADR ensures they get the same one as the rest of
  the system.
- A future "ConfigStore: hot-reload" ADR will likely use
  `monotonic()` for debounce / poll intervals; this ADR pre-
  commits to that being the right time domain.
