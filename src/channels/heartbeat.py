"""``HeartbeatChannel`` -- cron-scheduled bus emitter, one entry per config.

The heartbeat is modelled as a CHANNEL (boot priority 11): it brings
input *into* the bot from the outside (here: a clock), much like a
WebSocket channel brings input from a user. The robot itself does
not know that "a heartbeat ticked" -- it sees regular bus events
arriving on the topic each heartbeat config nominates.

Architecturally the channel works on behalf of an implicit user
(the robot's owner): every published event carries a configurable
principal (defaulting to the channel's ``default_principal``), never
an externally connected client.

Cycle, per configured heartbeat:

1. Compute the next fire time via croniter and sleep until then.
2. Build the configured event (a :class:`ComponentRequest` or
   :class:`RobotInput`, picked by ``emit.type`` in the entry's
   YAML) and **publish fire-and-forget**.
3. Go back to (1).

Nothing about the result is awaited. If anyone cares -- a future
RuleBasedKernel listening on ``tool.invoke`` for the matching
correlation, a chat kernel reacting to ``input.heartbeat``, an
audit subscriber -- they subscribe to the bus separately. That
decoupling means the heartbeat tick latency is bounded by the
publish call, not by downstream tool execution.

Configuration is loaded from a :class:`ConfigStorePort` (today
backed by ``configs/heartbeats.yaml``) so the heartbeat code never
has to be edited when the operator wants to add another scheduled
event. The channel knows nothing about specific tools, mailboxes
or kernels; everything is data.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from croniter import croniter

from src.bus.messages import ComponentRequest, RobotEvent, RobotInput
from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.components import ComponentCategory
from src.utility.config_store.ports import ConfigStorePort

logger = logging.getLogger(__name__)


_DEFAULT_CONFIG_KEY = "heartbeats"
_DEFAULT_PRINCIPAL = "robot:heartbeat"


class EmitType(str, Enum):
    """What kind of event a heartbeat tick publishes."""

    COMPONENT_REQUEST = "component_request"
    ROBOT_INPUT = "robot_input"


@dataclass(frozen=True)
class HeartbeatConfig:
    """One scheduled heartbeat parsed from the config store.

    - ``id`` -- short identifier used for correlation-id prefix,
      run-id prefix, and log output.
    - ``cron`` -- standard 5-field cron expression
      (``minute hour day-of-month month day-of-week``) parsed by
      croniter. Step values and ranges are supported.
    - ``emit_type`` -- which event kind to build per tick.
    - ``emit`` -- the raw ``emit`` mapping from YAML, with
      ``principal`` already filled from the channel default if the
      entry omitted it. The per-type builder reads its fields here.
    """

    id: str
    cron: str
    emit_type: EmitType
    emit: dict[str, Any]


class HeartbeatChannel(ChannelPort):
    """Cron-driven multi-heartbeat channel.

    Constructor wiring (Convention-DI from the builder):

    - ``config_store`` -- where the heartbeat list is loaded from.
      Eagerly read in :meth:`start`. Reference is kept for a future
      :meth:`refresh` path.
    - ``config_key`` -- which key to ask the store for, default
      ``"heartbeats"``.
    - ``default_principal`` -- principal used for every emitted
      event whose entry does not set ``emit.principal``. Comes from
      ``defaults.yaml`` via the Library-layer merge.

    Lifecycle:

    - :meth:`start` reads the list, parses each entry, and spawns
      one independent task per valid heartbeat. Invalid entries log
      and are skipped (one bad config does not break the channel).
    - :meth:`_stop` cancels every task. Nothing to drain -- the bus
      already absorbed every previous tick fire-and-forget.
    """

    component_name = "heartbeat"
    component_category = ComponentCategory.CHANNEL
    component_description = (
        "Scheduled bus-only channel. Reads a list of heartbeat "
        "configs from a ConfigStorePort (today: configs/"
        "heartbeats.yaml). For each entry, runs an independent "
        "cron-scheduled tick loop and publishes the configured "
        "event (ComponentRequest or RobotInput) fire-and-forget. "
        "Holds no direct reference to tool layers, kernels or any "
        "other downstream consumer -- the bus is the only contact "
        "surface."
    )

    def __init__(
        self,
        *,
        config_store: ConfigStorePort,
        config_key: str = _DEFAULT_CONFIG_KEY,
        default_principal: str = _DEFAULT_PRINCIPAL,
    ) -> None:
        if not isinstance(config_store, ConfigStorePort):
            raise TypeError(
                "HeartbeatChannel.config_store must implement "
                "ConfigStorePort, got "
                f"{type(config_store).__name__}"
            )
        if not isinstance(config_key, str) or not config_key:
            raise ValueError(
                "HeartbeatChannel.config_key must be a non-empty string"
            )
        if not isinstance(default_principal, str) or not default_principal:
            raise ValueError(
                "HeartbeatChannel.default_principal must be a non-empty string"
            )

        self._config_store = config_store
        self._config_key = config_key
        self._default_principal = default_principal

        self._bus: BusPort | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._configs: list[HeartbeatConfig] = []

    # ---- BusComponent lifecycle --------------------------------------------

    async def start(self, bus: BusPort) -> None:
        if self._tasks:
            return
        self._bus = bus

        store_id = getattr(self._config_store, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s)",
            type(self._config_store).__name__,
            store_id,
            type(self).__name__,
            self.instance_id,
        )

        raw_entries = self._config_store.configs(self._config_key)
        self._configs = self._parse_entries(raw_entries)

        if not self._configs:
            logger.info(
                "%s (%s) ready with no heartbeats (config key %r empty)",
                type(self).__name__,
                self.instance_id,
                self._config_key,
            )
        else:
            ids = ", ".join(cfg.id for cfg in self._configs)
            logger.info(
                "%s (%s) scheduled %d heartbeat(s) from %s (%s): %s",
                type(self).__name__,
                self.instance_id,
                len(self._configs),
                type(self._config_store).__name__,
                store_id,
                ids,
            )

        for cfg in self._configs:
            task = asyncio.create_task(
                self._loop(cfg),
                name=f"heartbeat:{cfg.id}",
            )
            self._tasks.append(task)

        await self.announce_lifecycle(bus, "ready")

    async def _stop(self) -> None:
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        self._bus = None

    # ---- Config parsing ----------------------------------------------------

    def _parse_entries(
        self, raw_entries: list[dict[str, Any]]
    ) -> list[HeartbeatConfig]:
        """Parse store entries into :class:`HeartbeatConfig` instances.

        Per-entry failure mode: log a warning, skip the entry,
        continue. A typo in one entry should never prevent the
        others from scheduling.
        """
        parsed: list[HeartbeatConfig] = []
        for index, raw in enumerate(raw_entries):
            try:
                cfg = self._parse_entry(raw)
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(
                    "HeartbeatChannel: skipping heartbeat entry #%d: %s",
                    index,
                    exc,
                )
                continue
            parsed.append(cfg)
        return parsed

    def _parse_entry(self, raw: dict[str, Any]) -> HeartbeatConfig:
        hb_id = raw.get("id")
        if not isinstance(hb_id, str) or not hb_id:
            raise ValueError("entry must have a non-empty 'id'")
        cron = raw.get("cron")
        if not isinstance(cron, str) or not cron:
            raise ValueError(f"heartbeat {hb_id!r}: 'cron' must be a non-empty string")
        if not croniter.is_valid(cron):
            raise ValueError(
                f"heartbeat {hb_id!r}: cron expression {cron!r} is not valid"
            )

        emit = raw.get("emit")
        if not isinstance(emit, dict):
            raise ValueError(
                f"heartbeat {hb_id!r}: 'emit' must be a mapping"
            )
        emit = dict(emit)

        raw_type = emit.get("type")
        if not isinstance(raw_type, str) or not raw_type:
            raise ValueError(
                f"heartbeat {hb_id!r}: 'emit.type' must be a non-empty string"
            )
        try:
            emit_type = EmitType(raw_type)
        except ValueError as exc:
            valid = ", ".join(t.value for t in EmitType)
            raise ValueError(
                f"heartbeat {hb_id!r}: emit.type {raw_type!r} not in "
                f"({valid})"
            ) from exc

        topic = emit.get("topic")
        if not isinstance(topic, str) or not topic:
            raise ValueError(
                f"heartbeat {hb_id!r}: 'emit.topic' must be a non-empty string"
            )

        # Fill principal from the channel default if the entry omitted it.
        emit.setdefault("principal", self._default_principal)
        principal = emit["principal"]
        if not isinstance(principal, str) or not principal:
            raise ValueError(
                f"heartbeat {hb_id!r}: 'emit.principal' must be a non-empty string"
            )

        return HeartbeatConfig(
            id=hb_id, cron=cron, emit_type=emit_type, emit=emit
        )

    # ---- Loop --------------------------------------------------------------

    async def _loop(self, cfg: HeartbeatConfig) -> None:
        """Sleep until cron next-fire, publish, repeat. Cancels cleanly.

        croniter is anchored on the current epoch timestamp rather
        than ``datetime.now()`` so the next-fire computation is
        timezone-agnostic and matches ``time.time()`` exactly. A
        naive ``datetime.now()`` is treated as UTC by croniter,
        which adds a wall-clock offset to every delay equal to the
        local timezone (e.g. +2h in MESZ) and effectively breaks
        scheduling.
        """
        iterator = croniter(cfg.cron, time.time())
        tick = 0
        try:
            while True:
                next_fire = iterator.get_next(float)
                delay = max(0.0, next_fire - time.time())
                await asyncio.sleep(delay)
                tick += 1
                await self._fire(cfg, tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "HeartbeatChannel: tick loop for %r died; no further "
                "ticks for this heartbeat until the channel is restarted",
                cfg.id,
            )

    async def _fire(self, cfg: HeartbeatConfig, tick: int) -> None:
        """Build and publish one event for ``cfg``. Best-effort, log on error."""
        if self._bus is None:
            return
        run_id = f"hb-{cfg.id}-{tick:08d}"
        try:
            event = self._build_event(cfg, run_id=run_id)
        except Exception:
            logger.exception(
                "HeartbeatChannel: failed to build event for %r (tick %d)",
                cfg.id,
                tick,
            )
            return
        try:
            await self._bus.publish(event)
        except Exception:
            logger.exception(
                "HeartbeatChannel: failed to publish event for %r (tick %d)",
                cfg.id,
                tick,
            )

    # ---- Emit builders -----------------------------------------------------

    def _build_event(
        self, cfg: HeartbeatConfig, *, run_id: str
    ) -> RobotEvent:
        """Dispatch on ``cfg.emit_type`` to the matching builder."""
        if cfg.emit_type is EmitType.COMPONENT_REQUEST:
            return self._build_component_request(cfg, run_id=run_id)
        if cfg.emit_type is EmitType.ROBOT_INPUT:
            return self._build_robot_input(cfg, run_id=run_id)
        raise ValueError(
            f"heartbeat {cfg.id!r}: no builder for emit type {cfg.emit_type!r}"
        )

    def _build_component_request(
        self, cfg: HeartbeatConfig, *, run_id: str
    ) -> ComponentRequest:
        emit = cfg.emit
        action = emit.get("action")
        if not isinstance(action, str) or not action:
            raise ValueError(
                f"heartbeat {cfg.id!r}: component_request needs a "
                "non-empty 'action'"
            )
        payload = emit.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError(
                f"heartbeat {cfg.id!r}: 'emit.payload' must be a mapping"
            )
        correlation_id = f"hb-{cfg.id}-{uuid.uuid4().hex[:12]}"
        return ComponentRequest(
            topic=emit["topic"],
            principal=emit["principal"],
            source=self.component_name,
            source_id=self.instance_id,
            run_id=run_id,
            correlation_id=correlation_id,
            action=action,
            payload=dict(payload),
        )

    def _build_robot_input(
        self, cfg: HeartbeatConfig, *, run_id: str
    ) -> RobotInput:
        emit = cfg.emit
        message = emit.get("message")
        if message is not None and not isinstance(message, str):
            raise ValueError(
                f"heartbeat {cfg.id!r}: 'emit.message' must be a string"
            )
        payload = emit.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError(
                f"heartbeat {cfg.id!r}: 'emit.payload' must be a mapping"
            )
        return RobotInput(
            topic=emit["topic"],
            principal=emit["principal"],
            source=self.component_name,
            source_id=self.instance_id,
            run_id=run_id,
            message=message,
            payload=dict(payload),
        )
