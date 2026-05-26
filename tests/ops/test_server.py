"""End-to-end tests for the ControlPlane WebSocket server.

Spins up a real aiohttp server on an ephemeral port, connects with a
real aiohttp client, and verifies authentication, the operation
dispatch, and the shutdown bridge.
"""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from src.bus import AsyncioBus
from src.kernel import EchoKernel
from src.ops.server import ControlPlane, ControlPlaneAuthRequired
from src.robot import ControlPlaneConfig, Robot, RobotIdentity


def _robot(
    *,
    cp_config: ControlPlaneConfig | None = None,
    token: str | None = "test-token",
) -> Robot:
    return Robot(
        identity=RobotIdentity(id="x", name="X"),
        components=[AsyncioBus(), EchoKernel()],
        control_plane_config=cp_config
        if cp_config is not None
        else ControlPlaneConfig(enabled=False),
        control_plane_token=token,
        shutdown_grace=0.0,
    )


async def _make_running_plane(
    *,
    token: str = "test-token",
) -> tuple[ControlPlane, Robot]:
    cfg = ControlPlaneConfig(host="127.0.0.1", port=0, port_range=(0, 0))
    robot = _robot(cp_config=cfg, token=token)
    plane = ControlPlane(config=cfg, token=token, robot=robot)
    await plane.start()
    return plane, robot


async def test_control_plane_binds_to_ephemeral_port() -> None:
    plane, _ = await _make_running_plane()
    try:
        assert plane.actual_port is not None
        assert plane.endpoint is not None
        assert plane.endpoint.startswith("ws://127.0.0.1:")
        assert plane.endpoint.endswith("/control")
    finally:
        await plane.stop()


async def test_auth_then_status_round_trip() -> None:
    plane, _ = await _make_running_plane(token="hunter2")
    try:
        url = plane.endpoint
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                await ws.send_json({"type": "auth", "token": "hunter2"})
                ack = json.loads(
                    (await asyncio.wait_for(ws.receive(), timeout=2.0)).data
                )
                assert ack == {"type": "auth.ok"}

                await ws.send_json(
                    {"type": "request", "id": "r1", "op": "status"}
                )
                resp = json.loads(
                    (await asyncio.wait_for(ws.receive(), timeout=2.0)).data
                )
                assert resp["type"] == "response"
                assert resp["id"] == "r1"
                assert resp["ok"] is True
                assert resp["result"]["identity"]["id"] == "x"
    finally:
        await plane.stop()


async def test_invalid_token_rejected() -> None:
    plane, _ = await _make_running_plane(token="hunter2")
    try:
        url = plane.endpoint
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                await ws.send_json({"type": "auth", "token": "wrong"})
                resp = json.loads(
                    (await asyncio.wait_for(ws.receive(), timeout=2.0)).data
                )
                assert resp["type"] == "auth.fail"
                assert resp["reason"] == "invalid token"
                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
                assert msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                )
    finally:
        await plane.stop()


async def test_request_before_auth_is_rejected() -> None:
    plane, _ = await _make_running_plane(token="hunter2")
    try:
        url = plane.endpoint
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                await ws.send_json(
                    {"type": "request", "id": "r1", "op": "status"}
                )
                resp = json.loads(
                    (await asyncio.wait_for(ws.receive(), timeout=2.0)).data
                )
                assert resp["type"] == "auth.fail"
    finally:
        await plane.stop()


async def test_shutdown_op_invokes_robot_request_shutdown() -> None:
    received: list[bool] = []

    plane, robot = await _make_running_plane(token="hunter2")

    async def fake_request_shutdown(*, force: bool = False) -> None:
        received.append(force)

    robot.request_shutdown = fake_request_shutdown  # type: ignore[method-assign]

    try:
        url = plane.endpoint
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                await ws.send_json({"type": "auth", "token": "hunter2"})
                await asyncio.wait_for(ws.receive(), timeout=2.0)
                await ws.send_json(
                    {
                        "type": "request",
                        "id": "r2",
                        "op": "shutdown",
                        "params": {"force": True},
                    }
                )
                resp = json.loads(
                    (await asyncio.wait_for(ws.receive(), timeout=2.0)).data
                )
                assert resp["ok"] is True
                assert resp["result"] == {"ok": True, "force": True}
        await asyncio.sleep(0)
        assert received == [True]
    finally:
        await plane.stop()


async def test_unknown_op_returns_error() -> None:
    plane, _ = await _make_running_plane(token="hunter2")
    try:
        url = plane.endpoint
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                await ws.send_json({"type": "auth", "token": "hunter2"})
                await asyncio.wait_for(ws.receive(), timeout=2.0)
                await ws.send_json(
                    {"type": "request", "id": "rx", "op": "unknown.op"}
                )
                resp = json.loads(
                    (await asyncio.wait_for(ws.receive(), timeout=2.0)).data
                )
                assert resp["ok"] is False
                assert "unknown" in resp["error"].lower()
    finally:
        await plane.stop()


async def test_start_without_token_refuses_to_bind() -> None:
    """Deny-by-default: no token = no control plane, period."""
    cfg = ControlPlaneConfig(host="127.0.0.1", port=0, port_range=(0, 0))
    robot = _robot(cp_config=cfg, token=None)
    plane = ControlPlane(config=cfg, token=None, robot=robot)
    with pytest.raises(ControlPlaneAuthRequired):
        await plane.start()
    assert plane.actual_port is None


async def test_robot_skips_control_plane_without_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Robot logs an error and continues booting without a control plane."""
    import logging

    robot = _robot(
        cp_config=ControlPlaneConfig(enabled=True),
        token=None,
    )
    with caplog.at_level(logging.ERROR, logger="src.robot"):
        await robot.start()
    try:
        assert robot.control_plane is None
        assert robot.control_plane_endpoint is None
        assert any(
            "control plane is enabled but no token" in rec.message
            for rec in caplog.records
        )
    finally:
        await robot.stop()


async def test_port_resolution_falls_back_when_preferred_busy() -> None:
    """If the preferred port is busy the plane binds another one."""
    blocker = await asyncio.start_server(
        lambda r, w: None, host="127.0.0.1", port=0
    )
    try:
        held = blocker.sockets[0].getsockname()[1]
        cfg = ControlPlaneConfig(
            host="127.0.0.1",
            port=held,
            port_range=(held, held),  # exhaust the range; falls back to 0
        )
        robot = _robot(cp_config=cfg, token="t")
        plane = ControlPlane(config=cfg, token="t", robot=robot)
        await plane.start()
        try:
            assert plane.actual_port is not None
            assert plane.actual_port != held
        finally:
            await plane.stop()
    finally:
        blocker.close()
        await blocker.wait_closed()
