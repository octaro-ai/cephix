"""Tests for the CLI client's pure slash-command parser."""

from __future__ import annotations

from src.cli_client import _parse_input


def test_blank_line_is_empty() -> None:
    assert _parse_input("   ").kind == "empty"


def test_plain_text_is_message() -> None:
    parsed = _parse_input("hello there")
    assert parsed.kind == "message"
    assert parsed.message == "hello there"


def test_new_maps_to_session_new() -> None:
    parsed = _parse_input("/new")
    assert parsed.kind == "command"
    assert parsed.action == "chat.session.new"
    assert parsed.args == {}


def test_sessions_and_list_alias() -> None:
    for line in ("/sessions", "/list"):
        parsed = _parse_input(line)
        assert parsed.kind == "command"
        assert parsed.action == "chat.session.list"


def test_open_requires_id() -> None:
    err = _parse_input("/open")
    assert err.kind == "error"
    assert "usage" in err.error

    ok = _parse_input("/open sess_123")
    assert ok.kind == "command"
    assert ok.action == "chat.session.open"
    assert ok.args == {"session_id": "sess_123"}


def test_rename_requires_id_and_title() -> None:
    err = _parse_input("/rename sess_1")
    assert err.kind == "error"

    ok = _parse_input("/rename sess_1 My great chat")
    assert ok.kind == "command"
    assert ok.action == "chat.session.rename"
    assert ok.args == {"session_id": "sess_1", "title": "My great chat"}


def test_help_is_local() -> None:
    assert _parse_input("/help").kind == "help"
    assert _parse_input("/?").kind == "help"


def test_unknown_slash_is_error() -> None:
    parsed = _parse_input("/bogus")
    assert parsed.kind == "error"
    assert "unknown command" in parsed.error


def test_slash_command_is_case_insensitive() -> None:
    assert _parse_input("/NEW").action == "chat.session.new"


def test_message_starting_with_text_not_slash() -> None:
    parsed = _parse_input("what is /etc/hosts?")
    assert parsed.kind == "message"
    assert parsed.message == "what is /etc/hosts?"
