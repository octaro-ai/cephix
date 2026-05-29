"""Persist :class:`RobotAuditNote` events through an :class:`EventSink`."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from src.bus.messages import AUDIT_TOPIC, RobotAuditNote, RobotEvent
from src.bus.ports import BusPort, Subscription
from src.components import BusComponent, ComponentCategory
from src.persistence.sink import EventSink

logger = logging.getLogger(__name__)


class AuditNoteSink(BusComponent):
    """Subscribe to :data:`AUDIT_TOPIC` and persist every note.

    Unlike the :class:`BusRecorder`, the audit sink listens on a
    *single* topic. Anything that is not a :class:`RobotAuditNote`
    arriving on that topic is ignored with a warning -- the dedicated
    topic must stay clean.
    """

    component_name = "audit_note_sink"
    component_category = ComponentCategory.AUDIT
    component_description = (
        "Audit recorder. Subscribes to the curated audit topic and persists "
        "every RobotAuditNote via the configured EventSink."
    )

    def __init__(self, *, sink: EventSink) -> None:
        if not isinstance(sink, EventSink):
            raise TypeError(
                f"AuditNoteSink requires an EventSink, got {type(sink).__name__}"
            )
        self._sink = sink
        self._bus: BusPort | None = None
        self._subscription: Subscription | None = None

    async def start(self, bus: BusPort) -> None:
        if self._subscription is not None:
            return
        self._bus = bus
        self._subscription = bus.subscribe(AUDIT_TOPIC, self._record)
        await self.announce_lifecycle(bus, "ready")

    async def drain(self) -> None:
        try:
            await self._sink.flush()
        except Exception:
            logger.exception(
                "AuditNoteSink failed to flush its sink during drain"
            )

    async def stop(self) -> None:
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            finally:
                self._subscription = None
        try:
            await self._sink.close()
        except Exception:
            logger.exception(
                "AuditNoteSink failed to close its sink during stop"
            )
        self._bus = None

    async def _record(self, event: RobotEvent) -> None:
        if not isinstance(event, RobotAuditNote):
            logger.warning(
                "AuditNoteSink received non-audit event %s on %s; ignoring",
                type(event).__name__,
                AUDIT_TOPIC,
            )
            return
        try:
            record = self._serialize(event)
        except Exception:
            logger.exception(
                "AuditNoteSink failed to serialize note from actor %r",
                event.actor,
            )
            return
        try:
            await self._sink.append(record)
        except Exception:
            logger.exception(
                "AuditNoteSink failed to persist note from actor %r",
                event.actor,
            )

    @staticmethod
    def _serialize(event: RobotAuditNote) -> dict[str, Any]:
        record: dict[str, Any] = {"event_type": type(event).__name__}
        record.update(dataclasses.asdict(event))
        return record
