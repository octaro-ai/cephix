"""``mcs.driver.clock`` -- wall-clock-time MCS ToolDriver.

One tool, ``current_time``. UTC by default; optional IANA timezone
parameter for an additional local representation. Reads
``datetime.now(timezone.utc)`` directly today; will be migrated to
a ``ClockPort`` injection once that lands in cephix.
"""

from mcs.driver.clock.tooldriver import ClockToolDriver

__all__ = ["ClockToolDriver"]
