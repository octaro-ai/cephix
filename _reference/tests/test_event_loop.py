from __future__ import annotations

import unittest

from src.domain import RobotEvent
from src.runtime.event_loop import RuntimeEventLoop


class RecordingKernel:
    def __init__(self) -> None:
        self.handled_events: list[RobotEvent] = []

    def handle_event(self, event: RobotEvent) -> None:
        self.handled_events.append(event)


class EventLoopTests(unittest.TestCase):
    def test_manual_queue_event_is_processed_before_new_source_events(self) -> None:
        class StubEventSource:
            def collect_new_events(self) -> list[RobotEvent]:
                return [RobotEvent(event_id="evt-source", event_type="source", source_channel="webhook")]

        kernel = RecordingKernel()
        loop = RuntimeEventLoop(kernel, StubEventSource())
        loop.push_event(RobotEvent(event_id="evt-manual", event_type="manual", source_channel="telegram"))

        did_run = loop.run_once()

        self.assertTrue(did_run)
        self.assertEqual("evt-manual", kernel.handled_events[0].event_id)
        self.assertEqual(1, len(loop.queue))

    def test_heartbeat_runs_when_no_external_events_exist(self) -> None:
        class StubEventSource:
            def collect_new_events(self) -> list[RobotEvent]:
                return []

        class StubHeartbeat:
            def build_idle_event(self) -> RobotEvent | None:
                return RobotEvent(event_id="evt-heartbeat", event_type="heartbeat.tick", source_channel="heartbeat")

        kernel = RecordingKernel()
        loop = RuntimeEventLoop(kernel, StubEventSource(), StubHeartbeat())

        did_run = loop.run_once()

        # Heartbeats return False (idle) so the service loop sleeps.
        self.assertFalse(did_run)
        self.assertEqual("evt-heartbeat", kernel.handled_events[0].event_id)

    def test_heartbeat_is_not_called_when_external_event_exists(self) -> None:
        class StubEventSource:
            def collect_new_events(self) -> list[RobotEvent]:
                return [RobotEvent(event_id="evt-source", event_type="message.received", source_channel="telegram")]

        class StubHeartbeat:
            def __init__(self) -> None:
                self.calls = 0

            def build_idle_event(self) -> RobotEvent | None:
                self.calls += 1
                return RobotEvent(event_id="evt-heartbeat", event_type="heartbeat.tick", source_channel="heartbeat")

        kernel = RecordingKernel()
        heartbeat = StubHeartbeat()
        loop = RuntimeEventLoop(kernel, StubEventSource(), heartbeat)

        did_run = loop.run_once()

        self.assertTrue(did_run)
        self.assertEqual("evt-source", kernel.handled_events[0].event_id)
        self.assertEqual(0, heartbeat.calls)

    def test_run_once_returns_false_when_no_work_exists(self) -> None:
        class StubEventSource:
            def collect_new_events(self) -> list[RobotEvent]:
                return []

        class StubHeartbeat:
            def build_idle_event(self) -> RobotEvent | None:
                return None

        kernel = RecordingKernel()
        loop = RuntimeEventLoop(kernel, StubEventSource(), StubHeartbeat())

        self.assertFalse(loop.run_once())
        self.assertEqual([], kernel.handled_events)


if __name__ == "__main__":
    unittest.main()
