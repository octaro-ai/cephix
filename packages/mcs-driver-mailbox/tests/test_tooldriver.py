"""Tests for the in-process :class:`MailboxToolDriver`.

The driver is pure (no transport), so the tests are pure too:
construct, call ``list_tools`` / ``execute_tool`` directly,
assert on the dicts. No mocks, no async.
"""

from __future__ import annotations

import pytest

from mcs.driver.core import MCSToolDriver, Tool

from mcs.driver.mailbox import MailboxToolDriver


def test_is_an_mcs_tool_driver() -> None:
    assert isinstance(MailboxToolDriver(), MCSToolDriver)


def test_list_tools_advertises_fetch_unread() -> None:
    driver = MailboxToolDriver()
    tools = driver.list_tools()
    assert [t.name for t in tools] == ["mailbox.fetch_unread"]
    tool = tools[0]
    assert isinstance(tool, Tool)
    assert tool.title
    assert tool.description
    param_names = {p.name for p in (tool.parameters or ())}
    assert {"mailbox_id", "limit"} <= param_names


def test_execute_returns_default_batch() -> None:
    driver = MailboxToolDriver()
    result = driver.execute_tool("mailbox.fetch_unread", {})
    assert result["mailbox_id"] == "stub-mailbox"
    assert len(result["messages"]) == 5
    first = result["messages"][0]
    assert first["id"] == "stub-msg-1"
    assert first["from"] == "sender1@example.com"
    assert "subject" in first and "snippet" in first


def test_execute_respects_limit_and_mailbox_id() -> None:
    driver = MailboxToolDriver()
    result = driver.execute_tool(
        "mailbox.fetch_unread", {"mailbox_id": "team-sales", "limit": 3}
    )
    assert result["mailbox_id"] == "team-sales"
    assert len(result["messages"]) == 3
    assert all(m["mailbox_id"] == "team-sales" for m in result["messages"])


def test_execute_clamps_limit_into_range() -> None:
    driver = MailboxToolDriver()
    assert len(driver.execute_tool(
        "mailbox.fetch_unread", {"limit": 0}
    )["messages"]) == 1
    assert len(driver.execute_tool(
        "mailbox.fetch_unread", {"limit": 9999}
    )["messages"]) == 50


def test_execute_unknown_tool_raises() -> None:
    driver = MailboxToolDriver()
    with pytest.raises(ValueError):
        driver.execute_tool("not-a-tool", {})
