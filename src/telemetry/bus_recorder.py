"""Telemetry component that records every bus event to an :class:`EventSink`.

The :class:`BusRecorder` is the reference telemetry component: it
subscribes via :meth:`BusPort.subscribe_all`, serializes each event
to a plain dict and hands it to the configured sink. The sink decides
what to do with it (write to JSONL, push to a message queue, batch
into a database, ...).

Boot order: the recorder is in :attr:`ComponentCategory.TELEMETRY`
which boots right after the bus and shuts down right before it. As
a result the recorder sees the entire userspace lifetime, including
every other component's start, attach, drain and stop.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from src.bus.messages import RobotEvent
from src.bus.ports import BusPort, Subscription
from src.components import BusComponent, ComponentCategory
from src.persistence.sink import EventSink

logger = logging.getLogger(__name__)


class BusRecorder(BusComponent):
    """Persist every event delivered on the bus through an :class:`EventSink`."""

    component_type = "bus_recorder"
    component_category = ComponentCategory.TELEMETRY
    component_description = (
        "Telemetry recorder. Subscribes to every bus event and persists it "
        "via the configured EventSink."
    )
    component_wizard_fields = ()

    def __init__(self, *, sink: EventSink) -> None:
        if not isinstance(sink, EventSink):
            raise TypeError(
                f"BusRecorder requires an EventSink, got {type(sink).__name__}"
            )
        self._sink = sink
        self._bus: BusPort | None = None
        self._subscription: Subscription | None = None

    async def start(self, bus: BusPort) -> None:
        if self._subscription is not None:
            return
        self._bus = bus
        self._subscription = bus.subscribe_all(self._record)

    async def drain(self) -> None:
        try:
            await self._sink.flush()
        except Exception:
            logger.exception("BusRecorder failed to flush its sink during drain")

    async def stop(self) -> None:
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            finally:
                self._subscription = None
        try:
            await self._sink.close()
        except Exception:
            logger.exception("BusRecorder failed to close its sink during stop")
        self._bus = None

    async def _record(self, event: RobotEvent) -> None:
        try:
            record = self._serialize(event)
        except Exception:
            logger.exception(
                "BusRecorder failed to serialize event on topic %r", event.topic
            )
            return
        try:
            await self._sink.append(record)
        except Exception:
            logger.exception(
                "BusRecorder failed to persist event on topic %r", event.topic
            )

    @staticmethod
    def _serialize(event: RobotEvent) -> dict[str, Any]:
        """Render ``event`` as a JSONable dict.

        We rely on dataclasses for the field walk so any new event
        field becomes visible to the recorder automatically. The
        runtime ``event_type`` makes the record self-describing for
        readers that don't carry the schema.
        """
        record: dict[str, Any] = {"event_type": type(event).__name__}
        if dataclasses.is_dataclass(event):
            record.update(dataclasses.asdict(event))
        else:
            record.update(getattr(event, "__dict__", {}))
        return record
