"""End-to-end tests for :class:`WebsocketChannel`.

The tests spin up a real aiohttp server on an ephemeral port, connect
with a real aiohttp client, and verify that the round-trip
RobotInput -> kernel -> RobotOutput -> client works.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from src.actor import EchoActor
from src.bus import AsyncioBus, RobotEvent, RobotInput, RobotOutput
from src.channels import WebsocketChannel
from src.kernel import BaseKernel
from src.robot import ControlPlaneConfig, Robot, RobotIdentity


async def _build_robot(
    *, robot_id: str | None = None, robot_name: str | None = None
) -> tuple[Robot, WebsocketChannel]:
    bus = AsyncioBus()
    actor = EchoActor()
    kernel = BaseKernel(actor=actor)
    channel = WebsocketChannel(host="127.0.0.1", port=0)
    robot = Robot(
        identity=RobotIdentity(id=robot_id, name=robot_name),
        components=[bus, actor, kernel, channel],
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=0.0,
    )
    return robot, channel


async def test_round_trip_input_echo() -> None:
    robot, channel = await _build_robot()
    async with robot:
        port = channel.actual_port
        assert port is not None
        url = f"ws://127.0.0.1:{port}/ws"

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                welcome_msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                assert welcome_msg.type == aiohttp.WSMsgType.TEXT
                welcome = json.loads(welcome_msg.data)
                assert welcome["type"] == "welcome"
                assert isinstance(welcome["session_id"], str)
                assert "robot" not in welcome  # anonymous robot omits the block

                await ws.send_json({"type": "input", "message": "hello"})

                response_msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                assert response_msg.type == aiohttp.WSMsgType.TEXT
                data = json.loads(response_msg.data)

                assert data["type"] == "output"
                assert data["status"] == "ok"
                assert data["message"] == "echo: hello"
                assert data["source"] == "kernel.base"
                assert data["run_id"].startswith("run-ws-")
                assert "error" not in data


async def test_routes_outputs_only_to_originating_session() -> None:
    robot, channel = await _build_robot()
    async with robot:
        port = channel.actual_port
        assert port is not None
        url = f"ws://127.0.0.1:{port}/ws"

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws_a, session.ws_connect(url) as ws_b:
                await asyncio.wait_for(ws_a.receive(), timeout=2.0)
                await asyncio.wait_for(ws_b.receive(), timeout=2.0)

                await ws_a.send_json({"type": "input", "message": "from-a"})

                msg = await asyncio.wait_for(ws_a.receive(), timeout=2.0)
                payload = json.loads(msg.data)
                assert payload["message"] == "echo: from-a"

                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws_b.receive(), timeout=0.3)


async def test_publishes_robot_input_with_session_payload() -> None:
    bus = AsyncioBus()
    inputs: list[RobotInput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotInput):
            inputs.append(event)

    bus.subscribe("input.message", collect)

    channel = WebsocketChannel(host="127.0.0.1", port=0)
    await bus.start()
    try:
        await channel.start(bus)
        try:
            url = f"ws://127.0.0.1:{channel.actual_port}/ws"
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url) as ws:
                    welcome = json.loads(
                        (await asyncio.wait_for(ws.receive(), timeout=2.0)).data
                    )
                    session_id = welcome["session_id"]
                    await ws.send_json({"type": "input", "message": "hi"})
                    await asyncio.sleep(0.05)
        finally:
            await channel.stop()
    finally:
        await bus.stop()

    assert len(inputs) == 1
    event = inputs[0]
    assert event.message == "hi"
    assert event.source == "channel.websocket"
    assert event.principal.endswith(session_id)
    assert event.payload.get("session_id") == session_id


async def test_ignores_non_json_frames() -> None:
    bus = AsyncioBus()
    inputs: list[RobotInput] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, RobotInput):
            inputs.append(event)

    bus.subscribe("input.message", collect)

    channel = WebsocketChannel(host="127.0.0.1", port=0)
    await bus.start()
    try:
        await channel.start(bus)
        try:
            url = f"ws://127.0.0.1:{channel.actual_port}/ws"
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url) as ws:
                    await asyncio.wait_for(ws.receive(), timeout=2.0)
                    await ws.send_str("not json")
                    await ws.send_json({"type": "input", "message": "real"})
                    await asyncio.sleep(0.05)
        finally:
            await channel.stop()
    finally:
        await bus.stop()

    assert [event.message for event in inputs] == ["real"]


async def test_stop_closes_open_sessions() -> None:
    robot, channel = await _build_robot()
    await robot.start()
    try:
        url = f"ws://127.0.0.1:{channel.actual_port}/ws"
        async with aiohttp.ClientSession() as session:
            ws = await session.ws_connect(url)
            try:
                await asyncio.wait_for(ws.receive(), timeout=2.0)
                assert channel.session_count == 1

                await robot.stop()
                await asyncio.sleep(0.05)
                assert channel.session_count == 0
            finally:
                if not ws.closed:
                    await ws.close()
    finally:
        if not robot._stop_event.is_set():
            await robot.stop()


async def test_welcome_carries_robot_identity_from_lifecycle_event() -> None:
    """The channel learns identity from the retained lifecycle ``ready`` event."""
    robot, channel = await _build_robot(robot_id="dreamgirl", robot_name="Dreamgirl")

    async with robot:
        url = f"ws://127.0.0.1:{channel.actual_port}/ws"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                welcome = json.loads(msg.data)

    assert welcome["type"] == "welcome"
    assert welcome["robot"] == {"id": "dreamgirl", "name": "Dreamgirl"}


async def test_channel_announces_shutdown_to_open_sessions() -> None:
    """Lifecycle ``shutdown`` event triggers a 'shutdown' frame to every session."""
    robot, channel = await _build_robot(robot_id="alpha", robot_name="Alpha")

    await robot.start()
    try:
        url = f"ws://127.0.0.1:{channel.actual_port}/ws"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                await asyncio.wait_for(ws.receive(), timeout=2.0)  # welcome
                stop_task = asyncio.create_task(robot.stop())

                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                # the next frame is either the shutdown notice or a close
                if msg.type == aiohttp.WSMsgType.TEXT:
                    frame = json.loads(msg.data)
                    assert frame["type"] == "shutdown"
                    assert frame["message"] == "Robot shutting down"

                await asyncio.wait_for(stop_task, timeout=2.0)
    finally:
        if not robot._stop_event.is_set():
            await robot.stop()


async def test_drops_outputs_for_unknown_run() -> None:
    bus = AsyncioBus()
    channel = WebsocketChannel(host="127.0.0.1", port=0)

    await bus.start()
    try:
        await channel.start(bus)
        try:
            url = f"ws://127.0.0.1:{channel.actual_port}/ws"
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url) as ws:
                    await asyncio.wait_for(ws.receive(), timeout=2.0)

                    await bus.publish(
                        RobotOutput(
                            topic="output.message",
                            principal="user",
                            source="kernel.base",
                            run_id="run-not-known",
                            message="nope",
                        )
                    )
                    with pytest.raises(asyncio.TimeoutError):
                        await asyncio.wait_for(ws.receive(), timeout=0.3)
        finally:
            await channel.stop()
    finally:
        await bus.stop()


# ---------------------------------------------------------------------------
# port_range fallback (analogous to the ControlPlane resolver)
# ---------------------------------------------------------------------------


def test_port_range_validation_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="low must be <= high"):
        WebsocketChannel(host="127.0.0.1", port=0, port_range=[100, 50])


def test_port_range_validation_rejects_wrong_arity() -> None:
    with pytest.raises(ValueError, match="2-element sequence"):
        WebsocketChannel(host="127.0.0.1", port=0, port_range=[100])


def test_port_range_validation_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        WebsocketChannel(host="127.0.0.1", port=0, port_range=[-1, 100])


async def test_port_range_walks_to_next_free_port_when_preferred_busy() -> None:
    """Conflict on the preferred port silently rolls forward in the range.

    Boots two channels: the first claims the OS-picked port, the second
    requests that exact port with a small range and must end up on the
    second-best slot (or, ultimately, on port 0 via the OS fallback).
    The point is that ``actual_port`` differs from the preferred one
    and ``start()`` does not raise.
    """
    bus_a = AsyncioBus()
    bus_b = AsyncioBus()
    occupant = WebsocketChannel(host="127.0.0.1", port=0)
    await bus_a.start()
    try:
        await occupant.start(bus_a)
        try:
            taken = occupant.actual_port
            assert taken is not None

            await bus_b.start()
            try:
                # Tiny range so the resolver definitely walks past
                # ``taken`` either to the very next port or to ``0``.
                second = WebsocketChannel(
                    host="127.0.0.1",
                    port=taken,
                    port_range=[taken, taken + 5],
                )
                await second.start(bus_b)
                try:
                    assert second.actual_port is not None
                    assert second.actual_port != taken
                finally:
                    await second.stop()
            finally:
                await bus_b.stop()
        finally:
            await occupant.stop()
    finally:
        await bus_a.stop()


async def test_port_conflict_without_range_raises() -> None:
    """Without a configured ``port_range`` a conflict surfaces loudly.

    Backwards-compatible default: a fixed-port deployment that suddenly
    finds its port in use should fail at ``start()`` rather than
    silently switch -- the operator chose a fixed port for a reason.
    """
    bus_a = AsyncioBus()
    bus_b = AsyncioBus()
    occupant = WebsocketChannel(host="127.0.0.1", port=0)
    await bus_a.start()
    try:
        await occupant.start(bus_a)
        try:
            taken = occupant.actual_port
            assert taken is not None

            await bus_b.start()
            try:
                second = WebsocketChannel(host="127.0.0.1", port=taken)
                with pytest.raises(OSError):
                    await second.start(bus_b)
            finally:
                await bus_b.stop()
        finally:
            await occupant.stop()
    finally:
        await bus_a.stop()
