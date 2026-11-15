# ADR-0006 - Central time source: UTC everywhere, one clock, no library defaults

- Status: accepted
- Date: 2026-05-30

## Context

The first cron-driven component (`HeartbeatChannel`) shipped with a two-line
bug that took a live boot to surface: croniter was anchored on a naive
`datetime.now()`, croniter treats naive datetimes as UTC, the next-fire
result was a UTC epoch -- but the wait loop compared it against `time.time()`
(also UTC epoch). In MESZ (UTC+2) every "next minute" landed ~7200s in the
future; the cron loop slept two hours instead of firing at the next minute.

The fix was trivial; the bug *class* is not. Three time concepts are at play
across the robot and they look the same until they don't:

- **Wall-clock**: `datetime.now()` (naive, local). Library-dependent: some
  libs read naive as UTC, some as local, some refuse.
- **UTC instant**: `datetime.now(UTC)` / `time.time()` -- one unambiguous
  point on the timeline.
- **Monotonic**: `time.monotonic()` -- a steadily-increasing number with no
  wall-clock relationship; for measuring *durations* without being lied to by
  NTP corrections, DST, leap seconds, or a manual clock change.

The codebase silently encoded "we use UTC everywhere" in one helper
([`_now_iso()`][_now_iso] -> `datetime.now(UTC).isoformat()`), but the
convention was never written down, so each new component re-decided what "now"
means. A grep of `datetime.now`, `time.time`, `time.monotonic`, `tzinfo`
finds call sites in ~7 files already, and the count grows with scheduling,
TTLs, replay windows, idle detection, rate limiting, and time-bucketed
metrics. Each is a place a one-line bug like the heartbeat's can hide -- and
the next one (a timestamp 5 minutes off, a session aging out 8 hours early)
will not be as visible.

[_now_iso]: ../../src/bus/messages.py

## Decision

Cephix has **one canonical time notion**, expressed as three binding rules.
(The implementation - a `ClockPort` injected by DI - is designed in
[design / clock port](../design/0002-clock-port.md); this ADR commits to the
*rules*, not the spelling.)

### 1. All wall-clock time is UTC

Every wall-clock value the robot computes, persists, emits, or compares is a
UTC instant.

- `datetime.now()` (naive) is forbidden in robot code. Use
  `datetime.now(UTC)` for a `datetime`, `time.time()` for an epoch float.
- `datetime.utcnow()` is also forbidden -- it returns a *naive* datetime that
  loses the UTC tag on serialize/compare. `datetime.now(UTC)` is the only
  correct spelling.
- Cron expressions are interpreted in UTC. `0 8 * * *` means "8 AM UTC". If a
  user-facing scheduler later wants local time, that translation happens once
  at the YAML-parse boundary, never inside scheduling primitives.
- ISO-8601 strings always carry an explicit `+00:00` / `Z`. Naive ISO strings
  are not accepted on the bus.

### 2. Wall-clock and duration are different functions

- `now()` -> wall-clock UTC instant. For event timestamps, expiry checks,
  next-cron-fire, metric buckets.
- `monotonic()` -> steadily-increasing seconds, no calendar meaning. For
  durations, "sleep N seconds from now", rate-limit windows, watchdog
  timeouts.

Mixing them is a category error: `time.time()` going backwards during an NTP
correction breaks a sleep-until-deadline loop, `time.monotonic()` does not;
conversely `monotonic()` cannot be a `RobotEvent.timestamp`. Internal
scheduling **must** use `monotonic()` for the "sleep until" math and `now()`
for the `event.timestamp` it emits.

### 3. The robot owns the clock; libraries do not

No library may silently inject its own time notion (croniter's
naive-is-UTC; logging's local-time `%(asctime)s`). Anything that schedules,
timestamps, times-out, or ages records takes the clock by DI rather than
reaching for `import time` / `import datetime`. Boot-time wiring guarantees
exactly one clock instance, one timezone (UTC), one monotonic source - which
also makes time-aware tests deterministic via an injected manual clock
instead of patching `time.time`.

## Consequences

What gets easier:

- **No more "every component decides what now means".** Once naive
  `datetime.now()` is a lint failure and the only path to "now" is the clock,
  the heartbeat bug class is structurally impossible.
- **Deterministic time-aware tests** (advance a manual clock instead of
  sleeping).
- **Audit consistency.** Every timestamp across telemetry, audit, sessions,
  run-status, and bus traffic shares one epoch - cross-stream comparison is
  byte-for-byte meaningful.
- **Timezone becomes a presentation concern**, handled by channels on the way
  out; robot internals never translate.

What gets harder:

- **Another port at boot**, and every scheduling/timestamping component takes
  it by injection.
- **Lint / review burden.** Banning naive `datetime.now()` / `utcnow()`
  needs a ruff rule and a review habit; this ADR is the rationale to cite.
- **Cron is UTC**, which operators in other timezones must know. A per-entry
  local-time flag is possible later but is a separate decision.

The implementation, migration call-sites, the lint rule, and the deferred
local-time-cron flag are designed in
[design / clock port](../design/0002-clock-port.md).

## Relationship to other ADRs

- [ADR-0005 - Robot per user](0005-robot-per-user-and-multi-mailbox-tenancy.md)
  and its [tenancy design](../design/0001-mail-triage-tenancy.md): run-state TTL,
  retry back-off, and "abandoned at boot" all need a clock; this ADR ensures
  they get the same one.
