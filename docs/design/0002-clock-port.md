# FRD-0002 - Central clock port

- Status: design / partially in effect
- Builds on: [ADR-0006 - Central time source](../adr/0006-central-time-source.md) (accepted)
- Origin: extracted from ADR-0006 (migration steps + follow-ups) when ADRs
  were narrowed to record only made decisions.

The principle is settled and binding (ADR-0006): all wall-clock time is UTC,
`now()` and `monotonic()` are different functions, and the robot owns the
clock - libraries do not inject their own. Today that principle lives only in
the `_now_iso()` helper. This document designs the `ClockPort` that turns the
principle into a single injected dependency, plus the migration that enforces
it.

## Target shape

One canonical clock all components consult by DI rather than reaching for
`import time` / `import datetime`. The boot-time wiring guarantees exactly one
clock instance, one timezone (UTC), one monotonic source.

```python
class ClockPort(ABC):
    @abstractmethod
    def now(self) -> datetime: ...        # wall-clock UTC instant
    @abstractmethod
    def monotonic(self) -> float: ...     # steadily-increasing seconds
```

- `now()` -> wall-clock UTC instant. For "what timestamp goes on this
  event", "is this token expired", "when is the next cron fire", "what
  bucket does this metric belong to".
- `monotonic()` -> seconds with no wall-clock relationship. For "how long
  did this take", "schedule a wakeup N seconds from now", "rate-limit
  window", "watchdog timeout".

Default `SystemClock` delegates to `datetime.now(UTC)` and `time.monotonic()`.
A `ManualClock` (or `FrozenClock`) is injected in tests so scheduling, expiry,
and ordering tests become deterministic - `ManualClock.advance(60)` instead of
`unittest.mock.patch("time.time", ...)` and wall-clock sleeps.

The clock is a CONNECTION-tier or UTILITY-tier component (exact boot level
decided in an implementation ADR when it lands). Every scheduling /
timestamping component takes it by constructor injection.

## Migration steps

1. Introduce a `ClockPort` ABC with `now()` and `monotonic()`, and a
   `SystemClock` default that delegates to `datetime.now(UTC)` and
   `time.monotonic()`.
2. Wire it through DI for `_now_iso`, the heartbeat tick loop, the kernel run
   anchors, and any other current call site (a grep of `datetime.now`,
   `time.time`, `time.monotonic`, `tzinfo` across `src/` finds ~7 files
   today; the count grows with scheduling, TTLs, replay windows, idle
   detection, rate limiting, time-bucketed metrics).
3. Add a `ManualClock` to the test helpers.
4. Add a ruff rule (or `forbid` regex in CI) that rejects naive
   `datetime.now()` and `datetime.utcnow()` in `src/`. The ADR is the
   rationale to point at when "but I just need a quick timestamp" comes up.
5. Audit-pass the logging configuration so log records use UTC too
   (default `%(asctime)s` is local-time and trivially inconsistent with the
   UTC instant on the same RobotEvent), so a grep across telemetry + log
   file lines up.

The current heartbeat fix already satisfies "use `now()` for the emitted
`event.timestamp`" but not yet "use `monotonic()` for the sleep-until math";
that second half is part of step 2, not a regression.

## Open questions

- **Local-time cron flag.** Cron is UTC by decision (`0 8 * * *` = 8 AM UTC).
  If operator friction warrants it, a per-entry `tz: Europe/Berlin` that
  translates once at the YAML-parse boundary is a follow-up - its own ADR
  when the need is real.
- **Exact boot tier** of the clock component (CONNECTION vs UTILITY) -
  settled in the implementation ADR.

## Related

- [ADR-0005 / mail-triage tenancy](0001-mail-triage-tenancy.md): run-state TTL,
  retry back-off, and "abandoned at boot" all need a clock; they get this
  one.
- A future "ConfigStore: hot-reload" design will use `monotonic()` for
  debounce / poll intervals - the same time domain this port commits to.
