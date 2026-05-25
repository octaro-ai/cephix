"""Tests for context compaction (conversation summarization)."""

from __future__ import annotations

import tempfile
import unittest

from src.domain import InteractionRecord
from src.memory.compaction import NullCompactor, TruncatingCompactor
from src.memory.persistent import PersistentMemoryStore
from src.memory.store import InMemoryMemoryStore


class NullCompactorTests(unittest.TestCase):
    def test_always_returns_empty(self) -> None:
        c = NullCompactor()
        interactions = [InteractionRecord(user_text="Hi", robot_text="Hello")]
        self.assertEqual("", c.compact(interactions))

    def test_empty_list(self) -> None:
        self.assertEqual("", NullCompactor().compact([]))


class TruncatingCompactorTests(unittest.TestCase):
    def test_single_interaction(self) -> None:
        c = TruncatingCompactor()
        result = c.compact([InteractionRecord(user_text="Hi", robot_text="Hello")])
        self.assertIn("1 earlier message", result)
        self.assertIn("User: Hi", result)
        self.assertIn("Robot: Hello", result)

    def test_multiple_interactions(self) -> None:
        c = TruncatingCompactor()
        interactions = [
            InteractionRecord(user_text="Hi", robot_text="Hello"),
            InteractionRecord(user_text="Wie geht's?", robot_text="Gut!"),
            InteractionRecord(user_text="Was ist 2+2?", robot_text="4"),
        ]
        result = c.compact(interactions)
        self.assertIn("3 earlier message", result)
        self.assertEqual(3, result.count("- User:"))

    def test_long_response_is_truncated(self) -> None:
        c = TruncatingCompactor(max_response_chars=20)
        result = c.compact([
            InteractionRecord(user_text="Tell me a story", robot_text="A" * 100),
        ])
        self.assertIn("...", result)
        # The truncated response should be max_response_chars + "..."
        self.assertNotIn("A" * 100, result)

    def test_empty_list_returns_empty(self) -> None:
        self.assertEqual("", TruncatingCompactor().compact([]))


class InMemoryCompactionTests(unittest.TestCase):
    """InMemoryMemoryStore compaction integration."""

    def _fill_store(self, store: InMemoryMemoryStore, n: int) -> None:
        for i in range(n):
            store.remember_interaction(
                user_id="user-1",
                conversation_id="conv-1",
                user_text=f"Message {i}",
                robot_text=f"Reply {i}",
            )

    def test_no_summary_below_threshold(self) -> None:
        store = InMemoryMemoryStore(compaction_threshold=10)
        self._fill_store(store, 8)
        ctx = store.build_context("user-1", "conv-1")
        self.assertEqual("", ctx["conversation_summary"])

    def test_no_summary_with_null_compactor(self) -> None:
        store = InMemoryMemoryStore(compaction_threshold=5)
        self._fill_store(store, 12)
        ctx = store.build_context("user-1", "conv-1")
        # NullCompactor is default → empty summary even above threshold
        self.assertEqual("", ctx["conversation_summary"])

    def test_summary_with_truncating_compactor(self) -> None:
        store = InMemoryMemoryStore(
            compactor=TruncatingCompactor(),
            compaction_threshold=5,
            recent_window=3,
        )
        self._fill_store(store, 10)
        ctx = store.build_context("user-1", "conv-1")

        # Recent window = last 3
        self.assertEqual(3, len(ctx["recent_interactions"]))
        # Summary covers 7 older messages (10 - 3)
        self.assertIn("7 earlier message", ctx["conversation_summary"])
        self.assertIn("Message 0", ctx["conversation_summary"])
        self.assertNotIn("Message 9", ctx["conversation_summary"])

    def test_recent_window_is_configurable(self) -> None:
        store = InMemoryMemoryStore(
            compactor=TruncatingCompactor(),
            compaction_threshold=5,
            recent_window=2,
        )
        self._fill_store(store, 8)
        ctx = store.build_context("user-1", "conv-1")
        self.assertEqual(2, len(ctx["recent_interactions"]))
        self.assertIn("6 earlier message", ctx["conversation_summary"])


class PersistentCompactionTests(unittest.TestCase):
    """PersistentMemoryStore compaction integration."""

    def _fill_store(self, store: PersistentMemoryStore, n: int) -> None:
        for i in range(n):
            store.remember_interaction(
                user_id="user-1",
                conversation_id="conv-1",
                user_text=f"Message {i}",
                robot_text=f"Reply {i}",
            )

    def test_no_summary_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir, compaction_threshold=10)
            self._fill_store(store, 8)
            ctx = store.build_context("user-1", "conv-1")
            self.assertEqual("", ctx["conversation_summary"])

    def test_summary_with_truncating_compactor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(
                tmpdir,
                compactor=TruncatingCompactor(),
                compaction_threshold=5,
                recent_window=3,
            )
            self._fill_store(store, 10)
            ctx = store.build_context("user-1", "conv-1")

            self.assertEqual(3, len(ctx["recent_interactions"]))
            self.assertIn("7 earlier message", ctx["conversation_summary"])

    def test_summary_is_cached_on_disk(self) -> None:
        """Summary file is created and reused on subsequent calls."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(
                tmpdir,
                compactor=TruncatingCompactor(),
                compaction_threshold=3,
                recent_window=2,
            )
            self._fill_store(store, 6)

            # First call builds summary
            ctx1 = store.build_context("user-1", "conv-1")
            summary1 = ctx1["conversation_summary"]

            # Second call should use cached summary (same content)
            ctx2 = store.build_context("user-1", "conv-1")
            self.assertEqual(summary1, ctx2["conversation_summary"])

    def test_summary_survives_restart(self) -> None:
        """Summary cache persists across store instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = PersistentMemoryStore(
                tmpdir,
                compactor=TruncatingCompactor(),
                compaction_threshold=3,
                recent_window=2,
            )
            self._fill_store(store1, 6)
            ctx1 = store1.build_context("user-1", "conv-1")

            # New instance, same dir
            store2 = PersistentMemoryStore(
                tmpdir,
                compactor=TruncatingCompactor(),
                compaction_threshold=3,
                recent_window=2,
            )
            ctx2 = store2.build_context("user-1", "conv-1")
            self.assertEqual(ctx1["conversation_summary"], ctx2["conversation_summary"])

    def test_summary_rebuilds_when_interactions_grow(self) -> None:
        """Adding more interactions invalidates the cached summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(
                tmpdir,
                compactor=TruncatingCompactor(),
                compaction_threshold=3,
                recent_window=2,
            )
            self._fill_store(store, 5)
            ctx1 = store.build_context("user-1", "conv-1")
            self.assertIn("3 earlier message", ctx1["conversation_summary"])

            # Add more interactions
            self._fill_store(store, 3)  # now 8 total
            ctx2 = store.build_context("user-1", "conv-1")
            self.assertIn("6 earlier message", ctx2["conversation_summary"])


if __name__ == "__main__":
    unittest.main()
