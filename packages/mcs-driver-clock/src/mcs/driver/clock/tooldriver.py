"""``ClockToolDriver`` -- current wall-clock time over MCS.

Exposes a single tool ``current_time`` that returns the current
UTC instant in two equivalent forms: ``iso_utc`` (ISO-8601 string
with the explicit ``+00:00`` suffix) and ``epoch_seconds`` (a
float). If the caller hands an IANA timezone name in the
``timezone`` argument, the response additionally carries the
local representation as ``iso_local`` plus the canonical
``timezone`` name. Unknown timezone names do **not** fail the
call -- they are reported as ``timezone_error`` and the UTC half
is returned regardless, so an LLM that mistypes the zone still
gets a useful answer.

Reads the wall clock directly via ``datetime.now(timezone.utc)``.
Once cephix introduces a central ``ClockPort`` (ADR 0006) the
driver will accept a clock instance for full test determinism.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcs.driver.core import (
    DriverBinding,
    DriverMeta,
    MCSToolDriver,
    Tool,
    ToolParameter,
)


_TOOL_CURRENT_TIME = "current_time"


@dataclass(frozen=True)
class _ClockDriverMeta(DriverMeta):
    id: str = "mcs.driver.clock.v1"
    name: str = "Clock ToolDriver"
    version: str = "0.1.0"
    bindings: tuple[DriverBinding, ...] = (
        DriverBinding(capability="clock", adapter="*", spec_format="Custom"),
    )
    supported_llms: None = None
    capabilities: tuple[str, ...] = ("orchestratable",)


class ClockToolDriver(MCSToolDriver):
    """MCS ToolDriver exposing the wall-clock as ``current_time``."""

    meta: DriverMeta = _ClockDriverMeta()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=_TOOL_CURRENT_TIME,
                title="Current time",
                description=(
                    "Return the current wall-clock time. The response "
                    "always carries the UTC instant (``iso_utc`` as "
                    "ISO-8601, ``epoch_seconds`` as a float). Pass "
                    "``timezone`` (an IANA name like ``Europe/Berlin`` "
                    "or ``America/New_York``) to additionally receive "
                    "the local representation as ``iso_local``. An "
                    "unknown timezone reports ``timezone_error`` and "
                    "still returns the UTC half."
                ),
                parameters=[
                    ToolParameter(
                        name="timezone",
                        description=(
                            "Optional IANA timezone name for an "
                            "additional local-time field."
                        ),
                        required=False,
                        schema={"type": "string"},
                    ),
                ],
            ),
        ]

    def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        if tool_name != _TOOL_CURRENT_TIME:
            raise ValueError(
                f"ClockToolDriver: unknown tool {tool_name!r}; "
                f"available: {[t.name for t in self.list_tools()]}"
            )
        now_utc = datetime.now(timezone.utc)
        result: dict[str, Any] = {
            "iso_utc": now_utc.isoformat(),
            "epoch_seconds": now_utc.timestamp(),
        }
        tz_name = arguments.get("timezone")
        if isinstance(tz_name, str) and tz_name:
            try:
                tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                result["timezone_error"] = str(exc) or f"unknown timezone {tz_name!r}"
            else:
                result["timezone"] = tz_name
                result["iso_local"] = now_utc.astimezone(tz).isoformat()
        return result
