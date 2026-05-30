"""Telemetry component: records every bus event through an
:class:`EventStreamProviderPort`.

The :class:`BusRecorder` is the reference telemetry component: it
subscribes via :meth:`BusPort.subscribe_all`, serializes each event
to a plain dict and hands it to its configured provider on its
configured channel. The provider decides what to do with it (append
to a JSONL file, push to a message queue, batch into a database, ...).

Boot order: the recorder is in :attr:`ComponentCategory.TELEMETRY`
which boots right after persistence and the bus, and shuts down
right before them. As a result the recorder sees the entire
userspace lifetime, including every other component's start,
attach, drain and stop.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from src.bus.messages import LIFECYCLE_TOPIC, RobotEvent, RobotLifecycle
from src.bus.ports import BusPort, Subscription
from src.components import BusComponent, ComponentCategory, RobotComponent
from src.persistence import EventStreamProviderPort

logger = logging.getLogger(__name__)


class BusRecorder(BusComponent):
    """Persist every event delivered on the bus through an
    :class:`EventStreamProviderPort`."""

    component_name = "bus_recorder"
    component_category = ComponentCategory.TELEMETRY
    component_description = (
        "Telemetry recorder. Subscribes to every bus event and appends "
        "it through the configured EventStreamProviderPort on the "
        "configured channel (default: 'telemetry')."
    )

    def __init__(
        self,
        *,
        provider: EventStreamProviderPort,
        channel: str = "telemetry",
    ) -> None:
        if not isinstance(provider, EventStreamProviderPort):
            raise TypeError(
                "BusRecorder requires an EventStreamProviderPort, got "
                f"{type(provider).__name__}"
            )
        if not channel:
            raise ValueError("BusRecorder.channel must be non-empty")
        self._provider = provider
        self._channel = channel
        # ``_scoped_channel`` is what we hand to the provider on
        # every append. It defaults to the configured channel but
        # gets prefixed with the current ``robot_run_id`` after
        # ``start`` reads the retained boot event off the bus, so
        # this run's records land in ``logs/<run_id>/<channel>.jsonl``
        # instead of appending to a shared file across boots.
        self._scoped_channel = channel
        self._bus: BusPort | None = None
        self._subscription: Subscription | None = None

    @property
    def channel(self) -> str:
        return self._channel

    async def start(self, bus: BusPort) -> None:
        if self._subscription is not None:
            return
        self._bus = bus
        # Surface the provider -> recorder wiring at the lifecycle
        # boundary, symmetric to the adapter -> connection ->
        # provider chain that ran in levels 0-2. The robot logs
        # ``attached`` right after this returns, so the log reads:
        #
        #   === Boot Level 6 (TELEMETRY) ===
        #   FilesystemEventStreamProvider (xxx) injected into BusRecorder (yyy)
        #   BusRecorder (yyy) attached
        provider_id = getattr(self._provider, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s) on channel %r",
            type(self._provider).__name__,
            provider_id,
            type(self).__name__,
            self.instance_id,
            self._channel,
        )
        # Stream anchor: write the retained ``RobotLifecycle.boot``
        # as the first record so the file begins with "this is who
        # I am, this is what was on the manifest at boot time".
        # ``subscribe_all`` deliberately does not replay retained
        # events (otherwise a recorder restart would double-write
        # every component's ``ComponentLifecycle.ready`` snapshot),
        # so the anchor is pulled explicitly here. The phase 2
        # ordering guarantees ``boot`` is already in the retained
        # slot at this point.
        #
        # The same event carries the current ``robot_run_id``; we
        # latch it into the scoped channel before writing the
        # anchor so even the anchor itself lands under the
        # per-run subdir.
        boot = bus.retained(LIFECYCLE_TOPIC)
        if isinstance(boot, RobotLifecycle):
            if boot.robot_run_id:
                self._scoped_channel = f"{boot.robot_run_id}/{self._channel}"
            try:
                await self._record(boot)
            except Exception:
                logger.exception(
                    "BusRecorder: failed to write retained boot anchor"
                )
        self._subscription = bus.subscribe_all(self._record)
        # Mount and lifecycle announcement go out AFTER subscribe_all
        # so the recorder catches its own events in the stream. (If we
        # mounted before subscribing, the recorder would miss its own
        # ``mounted`` event because live publishes only reach the
        # current ``_all_subscriptions``.)
        if isinstance(self._provider, RobotComponent):
            await self.publish_mount(
                bus,
                slot="provider",
                mounted=self._provider,
                extra_metadata={"channel": self._channel},
            )
        await self.announce_lifecycle(bus, "ready")

    async def _drain(self) -> None:
        try:
            await self._provider.flush(self._scoped_channel)
        except Exception:
            logger.exception(
                "BusRecorder: failed to flush channel %r during drain",
                self._scoped_channel,
            )

    async def _stop(self) -> None:
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
        try:
            record = self._serialize(event)
        except Exception:
            logger.exception(
                "BusRecorder failed to serialize event on topic %r", event.topic
            )
            return
        try:
            await self._provider.append(self._scoped_channel, record)
        except Exception:
            logger.exception(
                "BusRecorder failed to persist event on topic %r (channel %r)",
                event.topic,
                self._channel,
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
