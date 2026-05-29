"""End-to-end smoke for the command layer.

Boots a real :class:`Robot` wired like the ``chatbot`` template
(bus + capability collector + chat kernel + websocket channel) but
with a mock LLM actor so no network/API key is needed. A real aiohttp
client then exercises the full path:

    collector -> harness.capabilities -> channel -> CLI command frame
    -> CommandRequest -> ChatKernel handler -> CommandResponse -> client

This is the automated stand-in for the manual ``dreamgirl`` smoketest.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp

from src.actor.llm.mock_actor import MockLLMActor
from src.bus import AsyncioBus
from src.channels import WebsocketChannel
from src.kernel.chat import ChatKernel
from src.robot import ControlPlaneConfig, Robot, RobotIdentity
from src.utility.capability_collector import CapabilityCollector
from src.utility.firmware_store.ports import FirmwareStorePort
from src.utility.model_catalog.ports import ModelCatalogPort
from src.utility.model_catalog.types import ModelPricing, ModelSpec
from src.utility.session_store import JsonlSessionStore


class _StubFirmware(FirmwareStorePort):
    def documents(self):  # type: ignore[override]
        return {}

    def system_prompt(self) -> str:  # type: ignore[override]
        return ""

    def refresh(self) -> None:  # type: ignore[override]
        return None


class _StubCatalog(ModelCatalogPort):
    def lookup_spec(self, model_id, provider):  # type: ignore[override]
        return ModelSpec(
            model_id=model_id,
            provider=provider,
            context_window_tokens=8000,
            max_output_tokens=1000,
        )

    def lookup_pricing(self, model_id, provider):  # type: ignore[override]
        return ModelPricing(
            model_id=model_id,
            provider=provider,
            input_cost_per_token=0.0,
            output_cost_per_token=0.0,
        )


async def _build_robot(tmp_path: Path) -> tuple[Robot, WebsocketChannel]:
    bus = AsyncioBus()
    collector = CapabilityCollector()
    actor = MockLLMActor()
    kernel = ChatKernel(
        actor=actor,
        firmware=_StubFirmware(),
        sessions=JsonlSessionStore(sessions_dir=tmp_path / "sessions"),
        model_catalog=_StubCatalog(),
    )
    channel = WebsocketChannel(host="127.0.0.1", port=0)
    robot = Robot(
        identity=RobotIdentity(id="dreamgirl", name="Dreamgirl"),
        components=[bus, collector, actor, kernel, channel],
        control_plane_config=ControlPlaneConfig(enabled=False),
        shutdown_grace=0.0,
    )
    return robot, channel


async def _recv_frame_of_type(ws, expected_type: str, *, timeout: float = 3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError(expected_type)
        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        frame = json.loads(msg.data)
        if frame.get("type") == expected_type:
            return frame


async def test_command_layer_end_to_end(tmp_path: Path) -> None:
    robot, channel = await _build_robot(tmp_path)
    async with robot:
        url = f"ws://127.0.0.1:{channel.actual_port}/ws"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url) as ws:
                # 1. Capability manifest advertises the session commands.
                caps = await _recv_frame_of_type(ws, "capabilities")
                actions = {c["action"] for c in caps["commands"]}
                assert {
                    "chat.session.new",
                    "chat.session.list",
                    "chat.session.open",
                    "chat.session.rename",
                } <= actions

                # 2. /new -> a fresh session id.
                await ws.send_json(
                    {
                        "type": "command",
                        "action": "chat.session.new",
                        "correlation_id": "c1",
                    }
                )
                resp = await _recv_frame_of_type(ws, "command_response")
                assert resp["status"] == "ok"
                sid = resp["payload"]["session_id"]
                assert sid.startswith("sess_")

                # 3. A normal chat turn lands in that session.
                await ws.send_json(
                    {
                        "type": "input",
                        "message": "hello robot",
                        "payload": {"session_id": sid},
                    }
                )
                out = await _recv_frame_of_type(ws, "output")
                assert out["status"] == "ok"

                # 4. /sessions lists the session with the turn we just had.
                await ws.send_json(
                    {
                        "type": "command",
                        "action": "chat.session.list",
                        "correlation_id": "c2",
                    }
                )
                listing = await _recv_frame_of_type(ws, "command_response")
                ids = [s["session_id"] for s in listing["payload"]["sessions"]]
                assert sid in ids

                # 5. /rename sets a title.
                await ws.send_json(
                    {
                        "type": "command",
                        "action": "chat.session.rename",
                        "correlation_id": "c3",
                        "payload": {"session_id": sid, "title": "Smoke chat"},
                    }
                )
                renamed = await _recv_frame_of_type(ws, "command_response")
                assert renamed["status"] == "ok"

                # 6. /open returns the persisted history.
                await ws.send_json(
                    {
                        "type": "command",
                        "action": "chat.session.open",
                        "correlation_id": "c4",
                        "payload": {"session_id": sid},
                    }
                )
                opened = await _recv_frame_of_type(ws, "command_response")
                assert opened["payload"]["session_id"] == sid
                contents = [m["content"] for m in opened["payload"]["messages"]]
                assert "hello robot" in contents
