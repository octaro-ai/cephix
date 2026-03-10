"""Tests for session commands (session.new, session.list)."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.domain import ControlRequest
from src.gateways.websocket import WebSocketChannel, _ClientSession
from src.memory import InMemoryMemoryStore, PersistentMemoryStore
from src.service import RobotService
from src.control import InMemoryPairingRegistry

import tempfile
from pathlib import Path


class SessionNewTests(unittest.TestCase):
    """session.new generates a new conversation_id and sends it back."""

    def test_session_new_returns_conversation_id(self) -> None:
        pairings = InMemoryPairingRegistry()
        channel = WebSocketChannel(pairings=pairings)

        ws = AsyncMock()
        ws.closed = False
        session = _ClientSession(
            client_id="c1",
            ws=ws,
            remote_addr="127.0.0.1",
            authenticated=True,
            granted_scopes=frozenset({"chat"}),
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                channel._handle_session_new(session)
            )
        finally:
            loop.close()

        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        self.assertEqual("session.new", payload["type"])
        self.assertTrue(payload["conversation_id"].startswith("conv_"))

    def test_two_session_new_calls_generate_different_ids(self) -> None:
        pairings = InMemoryPairingRegistry()
        channel = WebSocketChannel(pairings=pairings)

        ws = AsyncMock()
        ws.closed = False
        session = _ClientSession(
            client_id="c1",
            ws=ws,
            remote_addr="127.0.0.1",
            authenticated=True,
            granted_scopes=frozenset({"chat"}),
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(channel._handle_session_new(session))
            loop.run_until_complete(channel._handle_session_new(session))
        finally:
            loop.close()

        calls = ws.send_json.call_args_list
        id1 = calls[0][0][0]["conversation_id"]
        id2 = calls[1][0][0]["conversation_id"]
        self.assertNotEqual(id1, id2)


class SessionListControlRequestTests(unittest.TestCase):
    """session.list creates a control request for the service to handle."""

    def test_session_list_creates_control_request(self) -> None:
        pairings = InMemoryPairingRegistry()
        channel = WebSocketChannel(pairings=pairings)

        ws = AsyncMock()
        ws.closed = False
        session = _ClientSession(
            client_id="c1",
            ws=ws,
            remote_addr="127.0.0.1",
            authenticated=True,
            granted_scopes=frozenset({"chat"}),
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                channel._handle_session_list(session, {})
            )
        finally:
            loop.close()

        requests = channel.drain_control_requests()
        self.assertEqual(1, len(requests))
        self.assertEqual("session.list", requests[0].request_type)
        self.assertEqual("c1", requests[0].recipient_id)


class ServiceSessionListTests(unittest.TestCase):
    """RobotService handles session.list by querying memory."""

    def _make_service(self, memory: Any) -> RobotService:
        robot = MagicMock()
        robot.kernel.memory = memory
        robot.channels = []
        robot.runtime = MagicMock()
        robot.control_plane = MagicMock()
        return RobotService(robot=robot)

    def test_session_list_returns_conversations(self) -> None:
        memory = InMemoryMemoryStore()
        memory.remember_interaction(
            user_id="user-1", conversation_id="alpha", user_text="a", robot_text="b",
        )
        memory.remember_interaction(
            user_id="user-1", conversation_id="beta", user_text="c", robot_text="d",
        )

        service = self._make_service(memory)
        request = ControlRequest(
            request_id="ctrl-1",
            source_channel="ws",
            recipient_id="c1",
            request_type="session.list",
        )
        response = service._handle_control_request(request)

        self.assertEqual("session.list", response["type"])
        self.assertIn("alpha", response["conversations"])
        self.assertIn("beta", response["conversations"])

    def test_session_list_empty_when_no_conversations(self) -> None:
        memory = InMemoryMemoryStore()
        service = self._make_service(memory)
        request = ControlRequest(
            request_id="ctrl-1",
            source_channel="ws",
            recipient_id="c1",
            request_type="session.list",
        )
        response = service._handle_control_request(request)

        self.assertEqual("session.list", response["type"])
        self.assertEqual([], response["conversations"])

    def test_session_list_with_persistent_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = PersistentMemoryStore(tmpdir)
            memory.remember_interaction(
                user_id="user-1", conversation_id="sess-1", user_text="hi", robot_text="hello",
            )
            memory.remember_interaction(
                user_id="user-1", conversation_id="sess-2", user_text="bye", robot_text="ciao",
            )

            service = self._make_service(memory)
            request = ControlRequest(
                request_id="ctrl-1",
                source_channel="ws",
                recipient_id="c1",
                request_type="session.list",
            )
            response = service._handle_control_request(request)

            self.assertEqual("session.list", response["type"])
            self.assertIn("sess-1", response["conversations"])
            self.assertIn("sess-2", response["conversations"])


class InMemoryListConversationsTests(unittest.TestCase):
    """InMemoryMemoryStore.list_conversations() works correctly."""

    def test_empty_store(self) -> None:
        store = InMemoryMemoryStore()
        self.assertEqual([], store.list_conversations())

    def test_returns_sorted_keys(self) -> None:
        store = InMemoryMemoryStore()
        store.remember_interaction(
            user_id="u1", conversation_id="beta", user_text="a", robot_text="b",
        )
        store.remember_interaction(
            user_id="u1", conversation_id="alpha", user_text="c", robot_text="d",
        )
        self.assertEqual(["alpha", "beta"], store.list_conversations())


if __name__ == "__main__":
    unittest.main()
