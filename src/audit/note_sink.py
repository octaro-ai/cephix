"""Persist :class:`RobotAuditNote` events through an
:class:`EventStreamProviderPort`."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from src.bus.messages import AUDIT_TOPIC, RobotAuditNote, RobotEvent
from src.bus.ports import BusPort, Subscription
from src.components import BusComponent, ComponentCategory, RobotComponent
from src.persistence import EventStreamProviderPort

logger = logging.getLogger(__name__)


class AuditNoteSink(BusComponent):
    """Subscribe to :data:`AUDIT_TOPIC` and persist every note through
    an :class:`EventStreamProviderPort`.

    Unlike the :class:`BusRecorder`, the audit sink listens on a
    *single* topic. Anything that is not a :class:`RobotAuditNote`
    arriving on that topic is ignored with a warning -- the dedicated
    topic must stay clean.
    """

    component_name = "audit_note_sink"
    component_category = ComponentCategory.AUDIT
    component_description = (
        "Audit recorder. Subscribes to the curated audit topic and "
        "appends every RobotAuditNote through the configured "
        "EventStreamProviderPort on the configured channel "
        "(default: 'audit')."
    )

    def __init__(
        self,
        *,
        provider: EventStreamProviderPort,
        channel: str = "audit",
    ) -> None:
        if not isinstance(provider, EventStreamProviderPort):
            raise TypeError(
                "AuditNoteSink requires an EventStreamProviderPort, got "
                f"{type(provider).__name__}"
            )
        if not channel:
            raise ValueError("AuditNoteSink.channel must be non-empty")
        self._provider = provider
        self._channel = channel
        self._bus: BusPort | None = None
        self._subscription: Subscription | None = None

    @property
    def channel(self) -> str:
        return self._channel

    async def start(self, bus: BusPort) -> None:
        if self._subscription is not None:
            return
        self._bus = bus
        # Surface the provider -> sink wiring at the lifecycle
        # boundary, symmetric to the adapter -> connection ->
        # provider chain that ran in levels 0-2. The robot logs
        # ``attached`` right after this returns, so the log reads:
        #
        #   === Boot Level 7 (AUDIT) ===
        #   FilesystemEventStreamProvider (xxx) injected into AuditNoteSink (yyy)
        #   AuditNoteSink (yyy) attached
        provider_id = getattr(self._provider, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s) on channel %r",
            type(self._provider).__name__,
            provider_id,
            type(self).__name__,
            self.instance_id,
            self._channel,
        )
        if isinstance(self._provider, RobotComponent):
            await self.publish_mount(
                bus,
                slot="provider",
                mounted=self._provider,
                extra_metadata={"channel": self._channel},
            )
        self._subscription = bus.subscribe(AUDIT_TOPIC, self._record)
        await self.announce_lifecycle(bus, "ready")

    async def drain(self) -> None:
        try:
            await self._provider.flush(self._channel)
        except Exception:
            logger.exception(
                "AuditNoteSink: failed to flush channel %r during drain",
                self._channel,
            )

    async def stop(self) -> None:
        if self._bus is not None:
            if isinstance(self._provider, RobotComponent):
                await self.publish_mount(
                    self._bus,
                    slot="provider",
                    mounted=None,
                    phase="unmounted",
                )
            await self.announce_lifecycle(self._bus, "shutdown")
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            finally:
                self._subscription = None
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
                "AuditNoteSink failed to serialize note from component %r",
                event.component,
            )
            return
        try:
            await self._provider.append(self._channel, record)
        except Exception:
            logger.exception(
                "AuditNoteSink failed to persist note from component %r "
                "(channel %r)",
                event.component,
                self._channel,
            )

    @staticmethod
    def _serialize(event: RobotAuditNote) -> dict[str, Any]:
        record: dict[str, Any] = {"event_type": type(event).__name__}
        record.update(dataclasses.asdict(event))
        return record
