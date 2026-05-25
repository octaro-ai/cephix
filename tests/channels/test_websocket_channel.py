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

from src.bus import AsyncioBus, RobotEvent, RobotInput, RobotOutput
from src.channels import WebsocketChannel
from src.kernel import EchoKernel
from src.robot import Robot


async def _build_robot(
    *, robot_id: str | None = None, robot_name: str | None = None
) -> tuple[Robot, WebsocketChannel]:
    bus = AsyncioBus()
    kernel = EchoKernel()
    channel = WebsocketChannel(host="127.0.0.1", port=0)
    robot = Robot(
        bus=bus,
        kernel=kernel,
        channels=[channel],
        robot_id=robot_id,
        robot_name=robot_name,
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

                await ws.send_json({"type": "input", "text": "hello"})

                response_msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                assert response_msg.type == aiohttp.WSMsgType.TEXT
                data = json.loads(response_msg.data)

                assert data["type"] == "output"
                assert data["text"] == "echo: hello"
                assert data["source"] == "kernel.echo"
                assert data["run_id"].startswith("run-ws-")


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

                await ws_a.send_json({"type": "input", "text": "from-a"})

                msg = await asyncio.wait_for(ws_a.receive(), timeout=2.0)
                payload = json.loads(msg.data)
                assert payload["text"] == "echo: from-a"

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
                    await ws.send_json({"type": "input", "text": "hi"})
                    await asyncio.sleep(0.05)
        finally:
            await channel.stop()
    finally:
        await bus.stop()

    assert len(inputs) == 1
    event = inputs[0]
    assert event.text == "hi"
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
                    await ws.send_json({"type": "input", "text": "real"})
                    await asyncio.sleep(0.05)
        finally:
            await channel.stop()
    finally:
        await bus.stop()

    assert [event.text for event in inputs] == ["real"]


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
    """The channel learns identity from the retained RobotReady on the bus."""
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
    """RobotShutdown broadcast triggers a 'shutdown' frame to every session."""
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
                    assert frame["reason"] == "lifecycle.stop"

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
                            source="kernel.echo",
                            run_id="run-not-known",
                            text="nope",
                        )
                    )
                    with pytest.raises(asyncio.TimeoutError):
                        await asyncio.wait_for(ws.receive(), timeout=0.3)
        finally:
            await channel.stop()
    finally:
        await bus.stop()
