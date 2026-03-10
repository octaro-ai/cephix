"""Tests for core memory (agent-curated key facts per user)."""

from __future__ import annotations

import tempfile
import unittest
from typing import Any

from src.domain import ExecutionContext
from src.memory.persistent import PersistentMemoryStore
from src.memory.store import InMemoryMemoryStore
from src.tools.system_tools import CORE_MEMORY_BUDGET, SystemToolHandlers


class InMemoryCoreMemoryTests(unittest.TestCase):
    def test_empty_by_default(self) -> None:
        store = InMemoryMemoryStore()
        self.assertEqual("", store.get_core_memory("user-1"))

    def test_set_and_get(self) -> None:
        store = InMemoryMemoryStore()
        store.set_core_memory("user-1", "Likes dark mode. Works on Cephix.")
        self.assertEqual("Likes dark mode. Works on Cephix.", store.get_core_memory("user-1"))

    def test_overwrite(self) -> None:
        store = InMemoryMemoryStore()
        store.set_core_memory("user-1", "Version 1")
        store.set_core_memory("user-1", "Version 2")
        self.assertEqual("Version 2", store.get_core_memory("user-1"))

    def test_per_user_isolation(self) -> None:
        store = InMemoryMemoryStore()
        store.set_core_memory("user-1", "Alice facts")
        store.set_core_memory("user-2", "Bob facts")
        self.assertEqual("Alice facts", store.get_core_memory("user-1"))
        self.assertEqual("Bob facts", store.get_core_memory("user-2"))

    def test_included_in_build_context(self) -> None:
        store = InMemoryMemoryStore()
        store.set_core_memory("user-1", "Key info here")
        ctx = store.build_context("user-1", None)
        self.assertEqual("Key info here", ctx["core_memory"])

    def test_build_context_empty_when_not_set(self) -> None:
        store = InMemoryMemoryStore()
        ctx = store.build_context("user-1", None)
        self.assertEqual("", ctx["core_memory"])


class PersistentCoreMemoryTests(unittest.TestCase):
    def test_set_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)
            store.set_core_memory("user-1", "Prefers concise answers.")
            self.assertEqual("Prefers concise answers.", store.get_core_memory("user-1"))

    def test_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = PersistentMemoryStore(tmpdir)
            store1.set_core_memory("user-1", "Important facts here.")

            store2 = PersistentMemoryStore(tmpdir)
            self.assertEqual("Important facts here.", store2.get_core_memory("user-1"))

    def test_included_in_build_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)
            store.set_core_memory("user-1", "Core info")
            ctx = store.build_context("user-1", None)
            self.assertEqual("Core info", ctx["core_memory"])

    def test_empty_when_not_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)
            self.assertEqual("", store.get_core_memory("user-1"))


class CoreMemoryToolHandlerTests(unittest.TestCase):
    def _make_ctx(self) -> ExecutionContext:
        return ExecutionContext(
            run_id="run-1",
            robot_id="robot-1",
            user_id="user-1",
            conversation_id="conv-1",
            channel="test",
            trace_id="trace-1",
        )

    def test_read_empty(self) -> None:
        memory = InMemoryMemoryStore()
        handlers = SystemToolHandlers(memory=memory)
        h = handlers.get_handlers()
        result = h["core_memory.read"](self._make_ctx(), {"user_id": "user-1"})
        self.assertEqual("", result["content"])
        self.assertEqual(0, result["length"])
        self.assertEqual(CORE_MEMORY_BUDGET, result["budget"])

    def test_write_and_read(self) -> None:
        memory = InMemoryMemoryStore()
        handlers = SystemToolHandlers(memory=memory)
        h = handlers.get_handlers()
        ctx = self._make_ctx()

        # Write
        result = h["core_memory.update"](ctx, {
            "user_id": "user-1",
            "content": "- Prefers dark mode\n- Works on Cephix\n- Speaks German",
        })
        self.assertTrue(result["stored"])

        # Read back
        result = h["core_memory.read"](ctx, {"user_id": "user-1"})
        self.assertIn("Prefers dark mode", result["content"])
        self.assertIn("Speaks German", result["content"])

    def test_budget_enforced(self) -> None:
        memory = InMemoryMemoryStore()
        handlers = SystemToolHandlers(memory=memory)
        h = handlers.get_handlers()
        ctx = self._make_ctx()

        result = h["core_memory.update"](ctx, {
            "user_id": "user-1",
            "content": "X" * (CORE_MEMORY_BUDGET + 1),
        })
        self.assertFalse(result["stored"])
        self.assertIn("exceeds budget", result["error"])

    def test_exactly_at_budget_is_ok(self) -> None:
        memory = InMemoryMemoryStore()
        handlers = SystemToolHandlers(memory=memory)
        h = handlers.get_handlers()
        ctx = self._make_ctx()

        result = h["core_memory.update"](ctx, {
            "user_id": "user-1",
            "content": "X" * CORE_MEMORY_BUDGET,
        })
        self.assertTrue(result["stored"])
        self.assertEqual(CORE_MEMORY_BUDGET, result["length"])

    def test_update_replaces_previous(self) -> None:
        memory = InMemoryMemoryStore()
        handlers = SystemToolHandlers(memory=memory)
        h = handlers.get_handlers()
        ctx = self._make_ctx()

        h["core_memory.update"](ctx, {"user_id": "user-1", "content": "Version 1"})
        h["core_memory.update"](ctx, {"user_id": "user-1", "content": "Version 2"})

        result = h["core_memory.read"](ctx, {"user_id": "user-1"})
        self.assertEqual("Version 2", result["content"])

    def test_defaults_to_ctx_user_id(self) -> None:
        memory = InMemoryMemoryStore()
        handlers = SystemToolHandlers(memory=memory)
        h = handlers.get_handlers()
        ctx = self._make_ctx()

        h["core_memory.update"](ctx, {"content": "Auto user"})
        result = h["core_memory.read"](ctx, {})
        self.assertEqual("Auto user", result["content"])
        self.assertEqual("user-1", result["user_id"])


if __name__ == "__main__":
    unittest.main()
