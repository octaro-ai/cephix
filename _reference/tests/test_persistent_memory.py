"""Tests for PersistentMemoryStore -- file-backed memory that survives restarts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.memory.persistent import PersistentMemoryStore


class PersistentMemoryTests(unittest.TestCase):
    def test_facts_survive_restart(self) -> None:
        """Facts written in one instance are readable in a new instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = PersistentMemoryStore(tmpdir)
            store1.remember_fact("user-1", "preference", "likes dark mode", score=0.9)
            store1.remember_fact("user-1", "project", "working on Cephix", score=0.8)

            # Simulate restart: new instance, same directory
            store2 = PersistentMemoryStore(tmpdir)
            ctx = store2.build_context("user-1", None)

            self.assertEqual(2, len(ctx["facts"]))
            self.assertEqual("likes dark mode", ctx["facts"][0]["content"])
            self.assertEqual("working on Cephix", ctx["facts"][1]["content"])

    def test_interactions_survive_restart(self) -> None:
        """Chat history written in one instance is readable in a new instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = PersistentMemoryStore(tmpdir)
            store1.remember_interaction(
                user_id="user-1",
                conversation_id="conv-1",
                user_text="Hi",
                robot_text="Hallo!",
            )
            store1.remember_interaction(
                user_id="user-1",
                conversation_id="conv-1",
                user_text="Was kannst du?",
                robot_text="Ich kann deinen Postkorb prüfen.",
            )

            store2 = PersistentMemoryStore(tmpdir)
            ctx = store2.build_context("user-1", "conv-1")

            self.assertEqual(2, len(ctx["recent_interactions"]))
            self.assertEqual("Hi", ctx["recent_interactions"][0]["user_text"])
            self.assertEqual("Was kannst du?", ctx["recent_interactions"][1]["user_text"])

    def test_new_session_has_facts_but_no_history(self) -> None:
        """A new conversation_id sees learned facts but no chat history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)

            # Session 1: interact and learn
            store.remember_interaction(
                user_id="user-1",
                conversation_id="session-1",
                user_text="Ich mag kurze Antworten",
                robot_text="Notiert.",
            )
            store.remember_fact("user-1", "preference", "prefers concise answers")

            # Session 2: new conversation
            ctx = store.build_context("user-1", "session-2")

            # Facts survive
            self.assertEqual(1, len(ctx["facts"]))
            self.assertEqual("prefers concise answers", ctx["facts"][0]["content"])
            # History is empty (new session)
            self.assertEqual(0, len(ctx["recent_interactions"]))

    def test_resume_old_session(self) -> None:
        """Returning to an old conversation_id restores its history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)

            store.remember_interaction(
                user_id="user-1",
                conversation_id="session-1",
                user_text="Starte Projekt Alpha",
                robot_text="Projekt Alpha gestartet.",
            )

            # Switch to session-2, then back to session-1
            store.remember_interaction(
                user_id="user-1",
                conversation_id="session-2",
                user_text="Neues Thema",
                robot_text="OK.",
            )

            ctx = store.build_context("user-1", "session-1")
            self.assertEqual(1, len(ctx["recent_interactions"]))
            self.assertEqual("Starte Projekt Alpha", ctx["recent_interactions"][0]["user_text"])

    def test_cross_channel_facts(self) -> None:
        """Facts learned in one channel are visible in another."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)

            # Learn via Telegram session
            store.remember_fact("user-1", "project", "Projekt Alpha")
            store.remember_interaction(
                user_id="user-1",
                conversation_id="tg-conv-001",
                user_text="Starte Projekt Alpha",
                robot_text="OK.",
            )

            # Access via WebSocket session
            ctx = store.build_context("user-1", "ws-session-new")

            # Facts visible cross-channel
            self.assertEqual(1, len(ctx["facts"]))
            self.assertEqual("Projekt Alpha", ctx["facts"][0]["content"])
            # But no Telegram chat history
            self.assertEqual(0, len(ctx["recent_interactions"]))

    def test_duplicate_fact_updates_score(self) -> None:
        """Writing the same fact again updates score instead of duplicating."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)
            store.remember_fact("user-1", "preference", "likes dark mode", score=0.5)
            store.remember_fact("user-1", "preference", "likes dark mode", score=0.9)

            ctx = store.build_context("user-1", None)
            self.assertEqual(1, len(ctx["facts"]))
            self.assertEqual(0.9, ctx["facts"][0]["score"])

    def test_list_conversations(self) -> None:
        """list_conversations returns all known session IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PersistentMemoryStore(tmpdir)
            store.remember_interaction(
                user_id="user-1", conversation_id="alpha", user_text="a", robot_text="b",
            )
            store.remember_interaction(
                user_id="user-1", conversation_id="beta", user_text="c", robot_text="d",
            )

            conversations = store.list_conversations()
            self.assertEqual(["alpha", "beta"], conversations)


if __name__ == "__main__":
    unittest.main()
