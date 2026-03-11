from __future__ import annotations

from src.domain import RobotEvent
from src.ports import EventSourcePort, HeartbeatPort
from src.runtime.kernel import DigitalRobotKernel


class RuntimeEventLoop:
    """
    Outer runtime loop.

    The kernel processes one run deterministically.
    The runtime loop keeps the robot alive and only wakes the kernel
    when actual input arrives.
    """

    def __init__(
        self,
        kernel: DigitalRobotKernel,
        event_source: EventSourcePort | None = None,
        heartbeat: HeartbeatPort | None = None,
    ) -> None:
        self.kernel = kernel
        self.event_source = event_source
        self.heartbeat = heartbeat
        self.queue: list[RobotEvent] = []

    def push_event(self, event: RobotEvent) -> None:
        self.queue.append(event)

    def run_once(self) -> bool:
        if self.event_source is not None:
            self.queue.extend(self.event_source.collect_new_events())

        # Heartbeats fire only when idle and are treated as background work
        # — they do NOT count as "did work" so the service loop sleeps.
        if not self.queue and self.heartbeat is not None:
            heartbeat_event = self.heartbeat.build_idle_event()
            if heartbeat_event is not None:
                self.kernel.handle_event(heartbeat_event)
            return False

        if not self.queue:
            return False
        next_event = self.queue.pop(0)
        self.kernel.handle_event(next_event)
        return True
