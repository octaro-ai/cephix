"""Tests for the cephix CLI dispatch and smart-default behaviour.

These tests intentionally do **not** start a real robot. The
``_start_instance`` helper is patched out so we only assert that the
CLI picks the right code path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src import cli
from src.configuration import (
    default_workspace_for,
    ensure_home_config,
    register_robot_override,
    save_robot_config,
)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("CEPHIX_HOME", str(tmp_path))
    ensure_home_config(tmp_path)
    return tmp_path


def _make_bot(home_path: Path, robot_id: str, *, enabled: bool = True) -> None:
    workspace = default_workspace_for(robot_id, home_path)
    workspace.mkdir(parents=True, exist_ok=True)
    save_robot_config(
        workspace,
        {
            "id": robot_id,
            "name": robot_id.capitalize(),
            "enabled": enabled,
            "kernel": {"type": "echo"},
        },
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_says_none_configured(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No robots configured" in out


def test_list_prints_table(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _make_bot(home, "alpha")
    _make_bot(home, "beta", enabled=False)
    rc = cli.main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "alpha" in out
    assert "beta" in out
    assert "enabled" in out
    assert "disabled" in out


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_unknown_id_returns_error(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["start", "ghost"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ghost" in err


def test_start_calls_runner(home: Path) -> None:
    _make_bot(home, "alpha")
    with patch.object(cli, "_start_instance", return_value=0) as runner:
        rc = cli.main(["start", "alpha"])
    assert rc == 0
    assert runner.called
    instance = runner.call_args.args[0]
    assert instance.id == "alpha"


def test_start_disabled_warns(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _make_bot(home, "alpha", enabled=False)
    with patch.object(cli, "_start_instance", return_value=0):
        rc = cli.main(["start", "alpha"])
    err = capsys.readouterr().err
    assert rc == 0
    assert "disabled" in err


# ---------------------------------------------------------------------------
# Smart default
# ---------------------------------------------------------------------------


def test_smart_default_zero_bots_in_non_tty_errors(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    rc = cli.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no robots" in err.lower()


def test_smart_default_zero_bots_in_tty_runs_wizard(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    fake_instance = type(
        "FakeInstance",
        (),
        {"id": "alpha", "name": "Alpha", "enabled": True, "workspace": home},
    )()

    def fake_wizard(**kwargs: Any) -> Any:
        return fake_instance

    monkeypatch.setattr("src.onboarding.run_wizard", fake_wizard)
    rc = cli.main([])
    assert rc == 0


def test_smart_default_one_bot_starts_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    _make_bot(home, "alpha")
    with patch.object(cli, "_start_instance", return_value=0) as runner:
        rc = cli.main([])
    assert rc == 0
    assert runner.called
    assert runner.call_args.args[0].id == "alpha"


def test_smart_default_many_bots_in_non_tty_errors(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_bot(home, "alpha")
    _make_bot(home, "beta")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    rc = cli.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "multiple" in err.lower()


def test_smart_default_many_bots_in_tty_prompts(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_bot(home, "alpha")
    _make_bot(home, "beta")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    inputs = iter(["beta"])
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: next(inputs))
    with patch.object(cli, "_start_instance", return_value=0) as runner:
        rc = cli.main([])
    assert rc == 0
    assert runner.call_args.args[0].id == "beta"


def test_smart_default_many_bots_quit_returns_error(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_bot(home, "alpha")
    _make_bot(home, "beta")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    inputs = iter(["q"])
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: next(inputs))
    rc = cli.main([])
    assert rc == 1


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_unregisters_external_workspace(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing an out-of-convention bot deletes both index and workspace."""
    other_ws = home / "elsewhere" / "alpha"
    other_ws.mkdir(parents=True)
    save_robot_config(
        other_ws,
        {"id": "alpha", "name": "Alpha", "enabled": True, "kernel": {"type": "echo"}},
    )
    register_robot_override("alpha", other_ws, home)

    rc = cli.main(["remove", "alpha", "--yes"])
    assert rc == 0
    assert not other_ws.exists()
    rc = cli.main(["start", "alpha"])
    assert rc == 1


def test_remove_deletes_convention_workspace(home: Path) -> None:
    """Default behaviour: workspace under ~/.cephix/robots/<id>/ is wiped."""
    _make_bot(home, "alpha")
    workspace = default_workspace_for("alpha", home)
    assert workspace.exists()
    rc = cli.main(["remove", "alpha", "--yes"])
    assert rc == 0
    assert not workspace.exists()
    # Re-running smart default in TTY mode should NOT find the bot any more.
    rc = cli.main(["list"])
    assert rc == 0


def test_remove_aborts_without_confirmation(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_bot(home, "alpha")
    workspace = default_workspace_for("alpha", home)
    inputs = iter([""])
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: next(inputs))
    rc = cli.main(["remove", "alpha"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "aborted" in out
    # Workspace must still be there when the user said no.
    assert workspace.exists()


# ---------------------------------------------------------------------------
# disable / enable
# ---------------------------------------------------------------------------


def test_disable_flips_enabled_flag(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_bot(home, "alpha", enabled=True)
    rc = cli.main(["disable", "alpha"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "disabled" in out
    workspace = default_workspace_for("alpha", home)
    cfg = save_robot_config  # avoid lint about unused import
    from src.configuration import load_robot_config

    assert load_robot_config(workspace)["enabled"] is False


def test_enable_flips_enabled_flag(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_bot(home, "alpha", enabled=False)
    rc = cli.main(["enable", "alpha"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "enabled" in out
    workspace = default_workspace_for("alpha", home)
    from src.configuration import load_robot_config

    assert load_robot_config(workspace)["enabled"] is True


def test_disable_is_idempotent(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_bot(home, "alpha", enabled=False)
    rc = cli.main(["disable", "alpha"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already disabled" in out


def test_disable_unknown_id_returns_error(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main(["disable", "ghost"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ghost" in err


# ---------------------------------------------------------------------------
# init (delegates to wizard)
# ---------------------------------------------------------------------------


def test_init_delegates_to_wizard(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_instance = type(
        "FakeInstance",
        (),
        {"id": "alpha", "name": "Alpha", "enabled": True, "workspace": home},
    )()

    captured: dict[str, Any] = {}

    def fake_wizard(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return fake_instance

    monkeypatch.setattr("src.onboarding.run_wizard", fake_wizard)
    rc = cli.main(["init", "Alpha Bot"])
    assert rc == 0
    assert captured["name"] == "Alpha Bot"


def test_init_returns_error_when_wizard_aborts(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.onboarding.run_wizard", lambda **kwargs: None)
    rc = cli.main(["init", "Foo"])
    assert rc == 1


# ---------------------------------------------------------------------------
# _resolve_log_file
# ---------------------------------------------------------------------------


def test_resolve_log_file_explicit_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit --log-file argument always wins, even on a TTY."""
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    resolved = cli._resolve_log_file("/var/log/custom.log", workspace=tmp_path)
    assert resolved == "/var/log/custom.log"
    # Workspace logs/ directory must NOT be auto-created in the
    # explicit-path case; the user is in charge.
    assert not (tmp_path / "logs").exists()


def test_resolve_log_file_tty_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In an interactive terminal, default to stderr (None)."""
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    resolved = cli._resolve_log_file(None, workspace=tmp_path)
    assert resolved is None
    assert not (tmp_path / "logs").exists()


def test_resolve_log_file_non_tty_writes_to_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detached / daemon runs auto-route to <workspace>/logs/cephix.log."""
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    resolved = cli._resolve_log_file(None, workspace=tmp_path)
    assert resolved == str(tmp_path / "logs" / "cephix.log")
    # The directory is created lazily so the file handler can attach
    # without further setup.
    assert (tmp_path / "logs").is_dir()
