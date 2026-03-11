from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import aiohttp

from src.app import build_websocket_service
from src.configuration import global_env_path, onboard_robot_instance, save_secret


class WebSocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._async_cleanup)
        self.log_path = Path(self._tmpdir.name) / "events.jsonl"

    async def _async_cleanup(self) -> None:
        service = getattr(self, "service", None)
        task = getattr(self, "task", None)
        if service is not None:
            await service.stop()
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tmpdir.cleanup()

    async def _start_service(self, onboarded: bool = True, **kwargs: object) -> None:
        kwargs.setdefault("home_dir", self._tmpdir.name)
        robot_id = str(kwargs.get("robot_id", "main"))
        robot_name = str(kwargs.get("robot_name", robot_id))
        if onboarded:
            onboard_robot_instance(
                robot_id=robot_id,
                robot_name=robot_name,
                home_override=self._tmpdir.name,
            )
        self.service = build_websocket_service(
            host="127.0.0.1",
            port=0,
            event_log_path=self.log_path,
            **kwargs,
        )
        self.task = asyncio.create_task(self.service.run_forever())
        await self._wait_for_server()
        self.ws_channel = self.service.channels[0]
        self.url = f"ws://127.0.0.1:{self.ws_channel.bound_port}/ws"

    async def _wait_for_server(self) -> None:
        for _ in range(50):
            channel = self.service.channels[0]
            if getattr(channel, "bound_port", 0):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"http://127.0.0.1:{channel.bound_port}/health",
                            timeout=aiohttp.ClientTimeout(total=1),
                        ) as response:
                            if response.status == 200:
                                return
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        self.fail("WebSocket service did not start in time")

    async def test_ping_reports_auth_state(self) -> None:
        await self._start_service(onboarded=False)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url) as ws:
                greeting = await ws.receive_json()
                self.assertEqual("auth_required", greeting["type"])
                self.assertTrue(greeting["server"]["onboarding_required"])

                await ws.send_json({"type": "ping"})
                pong = await ws.receive_json()
                self.assertEqual("info", pong["type"])
                self.assertFalse(pong["authenticated"])
                self.assertEqual([], pong["granted_scopes"])

    async def test_chat_is_rejected_until_robot_is_onboarded(self) -> None:
        await self._start_service(onboarded=False)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "chat-local",
                        "requested_scopes": ["chat"],
                    }
                )
                auth = await ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])
                self.assertTrue(auth["server"]["onboarding_required"])

                await ws.send_json(
                    {
                        "type": "message",
                        "sender_id": "owner",
                        "conversation_id": "ws-conv-1",
                        "content": "Hi",
                    }
                )
                error = await ws.receive_json()
                self.assertEqual("error", error["type"])
                self.assertIn("onboarding required", error["content"].lower())

    async def test_admin_can_onboard_robot_over_control_plane(self) -> None:
        await self._start_service(onboarded=False, admin_token="admin-token")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "admin-console",
                        "admin_token": "admin-token",
                        "requested_scopes": ["admin"],
                    }
                )
                auth = await ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])
                self.assertTrue(auth["server"]["onboarding_required"])

                await ws.send_json({"type": "admin.onboarding.status"})
                status = await ws.receive_json()
                self.assertEqual("admin.onboarding.status", status["type"])
                self.assertFalse(status["status"]["onboarded"])

                await ws.send_json(
                    {
                        "type": "admin.onboarding.apply",
                        "robot_name": "Dreamgirl",
                        "admin_token": "admin-token",
                        "access_token": "chat-token",
                    }
                )
                applied = await ws.receive_json()
                self.assertEqual("admin.onboarding.apply", applied["type"])
                self.assertTrue(applied["onboarded"])
                self.assertTrue(Path(applied["workspace_path"]).exists())
                self.assertTrue(Path(applied["instance_env_path"]).exists())
                self.assertTrue(Path(applied["robot_config_path"]).exists())

            async with session.ws_connect(self.url) as chat_ws:
                await chat_ws.receive_json()
                await chat_ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "chat-local",
                        "token": "chat-token",
                        "requested_scopes": ["chat"],
                    }
                )
                auth = await chat_ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])
                self.assertFalse(auth["server"]["onboarding_required"])

    async def test_onboarding_resolves_global_secret_via_layered_fallback(self) -> None:
        save_secret("CEPHIX_MAIN_WS_ACCESS_TOKEN", "central-chat-token", global_env_path(self._tmpdir.name))
        await self._start_service(onboarded=False, admin_token="admin-token")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "admin-console",
                        "admin_token": "admin-token",
                        "requested_scopes": ["admin"],
                    }
                )
                await ws.receive_json()

                await ws.send_json({"type": "admin.onboarding.status"})
                status = await ws.receive_json()
                self.assertTrue(status["status"]["global_secret_candidates"]["CEPHIX_MAIN_WS_ACCESS_TOKEN"])

                await ws.send_json(
                    {
                        "type": "admin.onboarding.apply",
                        "robot_name": "Dreamgirl",
                        "admin_token": "admin-token",
                    }
                )
                applied = await ws.receive_json()
                self.assertTrue(applied["onboarded"])

    async def test_local_loopback_chat_roundtrip_without_token(self) -> None:
        await self._start_service()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "chat-local",
                        "requested_scopes": ["chat"],
                    }
                )
                auth = await ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])
                self.assertEqual(["chat"], auth["granted_scopes"])

                await ws.send_json(
                    {
                        "type": "message",
                        "sender_id": "owner",
                        "conversation_id": "ws-conv-1",
                        "content": "Was ist neu in meinem Postkorb?",
                    }
                )

                saw_response = False
                for _ in range(20):
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    self.assertEqual(aiohttp.WSMsgType.TEXT, msg.type)
                    payload = json.loads(msg.data)
                    if payload["type"] == "ack":
                        continue
                    if payload["type"] == "response":
                        saw_response = True
                        self.assertIn("Ich habe 3 neue Nachrichten gefunden", payload["content"])
                        break

                self.assertTrue(saw_response)
                self.assertTrue(self.log_path.exists())

    async def test_telemetry_requires_scope(self) -> None:
        await self._start_service()
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "chat-local",
                        "requested_scopes": ["chat"],
                    }
                )
                auth = await ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])

                await ws.send_json({"type": "subscribe_telemetry", "enabled": True})
                response = await ws.receive_json()
                self.assertEqual("error", response["type"])
                self.assertIn("Telemetry scope required", response["content"])

    async def test_debug_telemetry_requires_token_even_on_loopback(self) -> None:
        await self._start_service(access_token="debug-token")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "chat-local",
                        "token": "debug-token",
                        "requested_scopes": ["chat", "telemetry"],
                    }
                )
                auth = await ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])
                self.assertEqual(["chat", "telemetry"], auth["granted_scopes"])

                await ws.send_json({"type": "subscribe_telemetry", "enabled": True})
                ack = await ws.receive_json()
                self.assertEqual("ack", ack["type"])

                await ws.send_json(
                    {
                        "type": "message",
                        "sender_id": "owner",
                        "conversation_id": "ws-conv-1",
                        "content": "Was ist neu in meinem Postkorb?",
                    }
                )

                saw_response = False
                telemetry_types: set[str] = set()
                for _ in range(30):
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    self.assertEqual(aiohttp.WSMsgType.TEXT, msg.type)
                    payload = json.loads(msg.data)
                    if payload["type"] == "ack":
                        continue
                    if payload["type"] == "response":
                        saw_response = True
                    if payload["type"] == "telemetry":
                        telemetry_types.add(payload["event"]["event_type"])
                    if saw_response and "tool.requested" in telemetry_types:
                        break

                self.assertTrue(saw_response)
                self.assertIn("input.received", telemetry_types)
                self.assertIn("tool.requested", telemetry_types)

    async def test_pairing_flow_requires_admin_approval(self) -> None:
        await self._start_service(
            access_token="chat-token",
            admin_token="admin-token",
            auto_approve_loopback=False,
        )

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.ws_connect(self.url) as chat_ws:
                await chat_ws.receive_json()
                await chat_ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "remote-chat-device",
                        "token": "chat-token",
                        "requested_scopes": ["chat"],
                    }
                )
                pairing = await chat_ws.receive_json()
                self.assertEqual("auth.pairing_required", pairing["type"])
                self.assertEqual("remote-chat-device", pairing["device_id"])

            async with session.ws_connect(self.url) as admin_ws:
                await admin_ws.receive_json()
                await admin_ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "admin-console",
                        "admin_token": "admin-token",
                        "requested_scopes": ["admin"],
                    }
                )
                auth = await admin_ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])

                await admin_ws.send_json({"type": "admin.pairing.list"})
                listing = await admin_ws.receive_json()
                self.assertEqual("admin.pairing.list", listing["type"])
                self.assertEqual(1, len(listing["pairings"]))
                self.assertEqual("remote-chat-device", listing["pairings"][0]["device_id"])

                await admin_ws.send_json({"type": "admin.pairing.approve", "device_id": "remote-chat-device"})
                approval = await admin_ws.receive_json()
                self.assertEqual("admin.pairing.approve", approval["type"])
                self.assertTrue(approval["approved"])

            async with session.ws_connect(self.url) as chat_ws:
                await chat_ws.receive_json()
                await chat_ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "remote-chat-device",
                        "token": "chat-token",
                        "requested_scopes": ["chat"],
                    }
                )
                auth = await chat_ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])
                self.assertEqual(["chat"], auth["granted_scopes"])

    async def test_admin_status_exposes_loaded_firmware(self) -> None:
        await self._start_service(admin_token="admin-token")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "admin-console",
                        "admin_token": "admin-token",
                        "requested_scopes": ["admin"],
                    }
                )
                auth = await ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])

                await ws.send_json({"type": "admin.status"})
                status = await ws.receive_json()
                self.assertEqual("admin.status", status["type"])
                self.assertIn("AGENTS.md", status["status"]["loaded_firmware"])
                self.assertEqual("governed-tool-executor", status["status"]["tool_execution_backend"])

    async def test_user_token_cannot_access_admin_control_plane(self) -> None:
        await self._start_service(access_token="chat-token", admin_token="admin-token")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "chat-local",
                        "token": "chat-token",
                        "requested_scopes": ["chat"],
                    }
                )
                auth = await ws.receive_json()
                self.assertEqual("auth.ok", auth["type"])
                self.assertEqual(["chat", "telemetry"], auth["granted_scopes"])

                await ws.send_json({"type": "admin.status"})
                error = await ws.receive_json()
                self.assertEqual("error", error["type"])
                self.assertIn("Admin scope required", error["content"])

    async def test_service_hosts_digital_robot_aggregate(self) -> None:
        await self._start_service()
        self.assertEqual("main", self.service.robot.robot_id)
        self.assertIs(self.service.runtime, self.service.robot.runtime)
        self.assertIsNotNone(self.service.robot.control_plane)
        self.assertEqual(["ws"], self.service.robot.control_plane.get_status()["registered_channels"])

    async def test_status_exposes_workspace_and_config_paths(self) -> None:
        await self._start_service(admin_token="admin-token", home_dir=self._tmpdir.name, robot_id="workspace-robot")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(self.url) as ws:
                await ws.receive_json()
                await ws.send_json(
                    {
                        "type": "auth.hello",
                        "device_id": "admin-console",
                        "admin_token": "admin-token",
                        "requested_scopes": ["admin"],
                    }
                )
                await ws.receive_json()

                await ws.send_json({"type": "admin.status"})
                status = await ws.receive_json()
                self.assertEqual("workspace-robot", status["status"]["robot_id"])
                self.assertTrue(str(status["status"]["home_config_path"]).endswith("cephix.yaml"))
                self.assertTrue(str(status["status"]["robot_config_path"]).endswith("robot.yaml"))
                self.assertIn("workspace-robot", str(status["status"]["workspace_path"]))


if __name__ == "__main__":
    unittest.main()
