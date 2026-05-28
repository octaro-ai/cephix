"""Tests for the built-in credential stores."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.credentials.exceptions import CredentialStoreError
from src.credentials.stores.env import EnvCredentialStore
from src.credentials.stores.process_env import ProcessEnvCredentialStore


class TestEnvCredentialStore:
    def test_lookup_returns_value_for_known_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_KEY=sk-test\nFOO=bar\n", encoding="utf-8")
        store = EnvCredentialStore(env_file)
        assert store.lookup("OPENAI_KEY") == "sk-test"
        assert store.lookup("FOO") == "bar"

    def test_lookup_returns_none_for_unknown_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_KEY=sk-test\n", encoding="utf-8")
        store = EnvCredentialStore(env_file)
        assert store.lookup("MISSING") is None

    def test_missing_file_is_tolerated(self, tmp_path: Path) -> None:
        """A non-existent path makes the store empty, not error."""
        store = EnvCredentialStore(tmp_path / "does-not-exist.env")
        assert store.lookup("ANY") is None
        assert store.has_key("ANY") is False

    def test_has_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n", encoding="utf-8")
        store = EnvCredentialStore(env_file)
        assert store.has_key("KEY") is True
        assert store.has_key("OTHER") is False

    def test_default_name_uses_parent_directory(self, tmp_path: Path) -> None:
        bot_dir = tmp_path / "my-bot"
        bot_dir.mkdir()
        env_file = bot_dir / ".env"
        env_file.write_text("", encoding="utf-8")
        store = EnvCredentialStore(env_file)
        assert store.name == "env:my-bot"

    def test_explicit_name_overrides_default(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        store = EnvCredentialStore(env_file, name="custom-store")
        assert store.name == "custom-store"

    def test_path_property(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        store = EnvCredentialStore(env_file)
        assert store.path == env_file

    def test_tilde_expansion(self, tmp_path: Path, monkeypatch) -> None:
        """``~`` in path is expanded to the user home."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=val\n", encoding="utf-8")
        store = EnvCredentialStore("~/.env")
        assert store.lookup("KEY") == "val"

    def test_quoted_values_parsed_correctly(self, tmp_path: Path) -> None:
        """Quoted values, including ones containing spaces, are unquoted."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            'API_KEY="sk-with-spaces and stuff"\nPLAIN=hello\n',
            encoding="utf-8",
        )
        store = EnvCredentialStore(env_file)
        assert store.lookup("API_KEY") == "sk-with-spaces and stuff"
        assert store.lookup("PLAIN") == "hello"

    def test_comments_ignored(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# top comment\nKEY=val\n# another\n",
            encoding="utf-8",
        )
        store = EnvCredentialStore(env_file)
        assert store.lookup("KEY") == "val"


class TestProcessEnvCredentialStore:
    def test_snapshot_default_captures_environ(self, monkeypatch) -> None:
        monkeypatch.setenv("CEPHIX_TEST_KEY", "snapshot-value")
        store = ProcessEnvCredentialStore()
        assert store.lookup("CEPHIX_TEST_KEY") == "snapshot-value"

    def test_snapshot_isolates_from_later_changes(self, monkeypatch) -> None:
        """A snapshotted store ignores os.environ mutations after construction."""
        monkeypatch.setenv("CEPHIX_TEST_KEY", "before")
        store = ProcessEnvCredentialStore(snapshot=True)
        monkeypatch.setenv("CEPHIX_TEST_KEY", "after")
        assert store.lookup("CEPHIX_TEST_KEY") == "before"

    def test_live_view_sees_environ_mutations(self, monkeypatch) -> None:
        """``snapshot=False`` reads ``os.environ`` on every lookup."""
        monkeypatch.setenv("CEPHIX_LIVE_KEY", "before")
        store = ProcessEnvCredentialStore(snapshot=False)
        monkeypatch.setenv("CEPHIX_LIVE_KEY", "after")
        assert store.lookup("CEPHIX_LIVE_KEY") == "after"

    def test_lookup_returns_none_for_unknown_key(self) -> None:
        store = ProcessEnvCredentialStore()
        assert store.lookup("CEPHIX_DEFINITELY_NOT_SET_XXXX") is None

    def test_default_name(self) -> None:
        assert ProcessEnvCredentialStore().name == "process-env"

    def test_explicit_name(self) -> None:
        assert ProcessEnvCredentialStore(name="ci-env").name == "ci-env"

    def test_has_key(self, monkeypatch) -> None:
        monkeypatch.setenv("CEPHIX_HAS_KEY_TEST", "x")
        store = ProcessEnvCredentialStore()
        assert store.has_key("CEPHIX_HAS_KEY_TEST") is True
        assert store.has_key("CEPHIX_DEFINITELY_NOT_SET") is False
