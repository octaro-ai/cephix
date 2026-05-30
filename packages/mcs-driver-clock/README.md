# mcs-driver-clock

`ClockToolDriver` -- an MCS ToolDriver that returns the current wall-clock time.

One tool, `current_time`. Always returns UTC as ISO-8601 plus an epoch-seconds float; if the caller passes an IANA timezone name (e.g. `Europe/Berlin`) the response also carries the local representation.

## Usage

```python
from mcs.driver.clock import ClockToolDriver

driver = ClockToolDriver()
driver.execute_tool("current_time", {})
# {'iso_utc': '2026-05-30T20:15:32.123456+00:00', 'epoch_seconds': 1748636132.123456}

driver.execute_tool("current_time", {"timezone": "Europe/Berlin"})
# {'iso_utc': '...+00:00', 'epoch_seconds': ..., 'timezone': 'Europe/Berlin',
#  'iso_local': '2026-05-30T22:15:32.123456+02:00'}

driver.execute_tool("current_time", {"timezone": "Mars/Olympus"})
# {'iso_utc': '...', 'epoch_seconds': ..., 'timezone_error': "No time zone found ..."}
# Unknown timezones soft-fail so the LLM still gets UTC back.
```

The driver reads `datetime.now(timezone.utc)` directly today. Once Cephix introduces a `ClockPort` (planned in cephix ADR 0006) the driver will accept a clock instance for full test determinism.
