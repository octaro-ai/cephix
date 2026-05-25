"""Tests for the MCS mail driver factory and adapter integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.tools.mcs_adapter import MCSToolDriverAdapter
from src.tools.mail_driver_factory import (
    MAIL_RISK_OVERRIDES,
    build_mail_driver,
    _resolve_connection,
)


# ---------------------------------------------------------------------------
# Fakes that mirror the MCS Tool / ToolParameter structure
# ---------------------------------------------------------------------------

@dataclass
class FakeToolParam:
    name: str
    description: str = ""
    required: bool = False
    schema: dict[str, Any] | None = None


@dataclass
class FakeTool:
    name: str
    title: str | None = None
    description: str | None = None
    parameters: list[FakeToolParam] | None = None

    def __post_init__(self) -> None:
        if self.parameters is None:
            self.parameters = []


class FakeMCSToolDriver:
    """Mimics the MCS MailToolDriver interface without real connections."""

    def __init__(self, tools: list[FakeTool] | None = None) -> None:
        self._tools = tools or _default_mail_tools()
        self._calls: list[tuple[str, dict]] = []

    def list_tools(self) -> list[FakeTool]:
        return list(self._tools)

    def execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self._calls.append((tool_name, arguments))
        return {"status": "ok", "tool": tool_name}


def _default_mail_tools() -> list[FakeTool]:
    return [
        FakeTool(name="list_folders", title="List folders"),
        FakeTool(name="list_messages", title="List messages",
                 parameters=[FakeToolParam(name="folder", description="Folder name"),
                             FakeToolParam(name="limit", description="Max messages", schema={"type": "integer"})]),
        FakeTool(name="fetch_message", title="Fetch message"),
        FakeTool(name="search_messages", title="Search messages"),
        FakeTool(name="move_message", title="Move message"),
        FakeTool(name="set_flags", title="Set flags"),
        FakeTool(name="create_folder", title="Create folder"),
        FakeTool(name="send_message", title="Send message"),
        FakeTool(name="send_html_message", title="Send HTML message"),
    ]


# ---------------------------------------------------------------------------
# Adapter tests with MCS Tool objects (not dicts)
# ---------------------------------------------------------------------------

class TestMCSAdapterWithToolObjects:

    def test_list_tools_returns_namespaced_definitions(self):
        adapter = MCSToolDriverAdapter(
            driver=FakeMCSToolDriver(),
            namespace="mail",
            risk_overrides=MAIL_RISK_OVERRIDES,
        )
        tools = adapter.list_tools()
        assert len(tools) == 9
        names = [t.name for t in tools]
        assert "mail.list_folders" in names
        assert "mail.send_message" in names

    def test_risk_class_metadata_applied(self):
        adapter = MCSToolDriverAdapter(
            driver=FakeMCSToolDriver(),
            namespace="mail",
            risk_overrides=MAIL_RISK_OVERRIDES,
        )
        by_name = {t.name: t for t in adapter.list_tools()}

        assert by_name["mail.list_folders"].metadata["risk_class"] == "read_only"
        assert by_name["mail.fetch_message"].metadata["risk_class"] == "read_only"
        assert by_name["mail.move_message"].metadata["risk_class"] == "low_risk_mutation"
        assert by_name["mail.send_message"].metadata["risk_class"] == "high_risk_mutation"
        assert by_name["mail.send_html_message"].metadata["risk_class"] == "high_risk_mutation"

    def test_parameters_converted_from_objects(self):
        adapter = MCSToolDriverAdapter(
            driver=FakeMCSToolDriver(),
            namespace="mail",
            risk_overrides=MAIL_RISK_OVERRIDES,
        )
        by_name = {t.name: t for t in adapter.list_tools()}
        list_msgs = by_name["mail.list_messages"]
        assert len(list_msgs.parameters) == 2
        assert list_msgs.parameters[0].name == "folder"
        assert list_msgs.parameters[1].type == "integer"

    def test_execute_strips_namespace(self):
        fake = FakeMCSToolDriver()
        adapter = MCSToolDriverAdapter(
            driver=fake,
            namespace="mail",
            risk_overrides=MAIL_RISK_OVERRIDES,
        )
        result = adapter.execute(None, "mail.list_messages", {"folder": "INBOX"})
        assert result["tool"] == "list_messages"
        assert fake._calls == [("list_messages", {"folder": "INBOX"})]

    def test_get_definition(self):
        adapter = MCSToolDriverAdapter(
            driver=FakeMCSToolDriver(),
            namespace="mail",
            risk_overrides=MAIL_RISK_OVERRIDES,
        )
        defn = adapter.get_definition("mail.fetch_message")
        assert defn is not None
        assert defn.metadata["original_name"] == "fetch_message"


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestBuildMailDriver:

    def test_returns_none_when_no_host(self):
        result = build_mail_driver(
            secret_resolver=lambda k: "",
            mail_config={},
        )
        assert result is None

    def test_returns_none_when_no_config(self):
        result = build_mail_driver(
            secret_resolver=lambda k: "",
            mail_config=None,
        )
        assert result is None


class TestResolveConnection:

    def test_resolves_user_and_password(self):
        secrets = {"MAIL_READ_USER": "alice", "MAIL_READ_PASSWORD": "s3cret"}
        result = _resolve_connection(
            {"host": "imap.example.com", "port": 993, "ssl": True},
            lambda k: secrets.get(k, ""),
            prefix="read",
        )
        assert result["host"] == "imap.example.com"
        assert result["user"] == "alice"
        assert result["password"] == "s3cret"
        assert result["port"] == 993
        assert result["ssl"] is True

    def test_custom_env_keys(self):
        secrets = {"MY_USER": "bob", "MY_PASS": "pw"}
        result = _resolve_connection(
            {"host": "imap.example.com", "user_env": "MY_USER", "password_env": "MY_PASS"},
            lambda k: secrets.get(k, ""),
            prefix="read",
        )
        assert result["user"] == "bob"
        assert result["password"] == "pw"

    def test_skips_empty_credentials(self):
        result = _resolve_connection(
            {"host": "imap.example.com"},
            lambda k: "",
            prefix="read",
        )
        assert "user" not in result
        assert "password" not in result
        assert result["host"] == "imap.example.com"
