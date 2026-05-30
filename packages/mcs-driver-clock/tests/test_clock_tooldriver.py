"""Tests for the in-process :class:`ClockToolDriver`."""

from __future__ import annotations

import time

import pytest

from mcs.driver.core import MCSToolDriver, Tool

from mcs.driver.clock import ClockToolDriver


def test_is_an_mcs_tool_driver() -> None:
    assert isinstance(ClockToolDriver(), MCSToolDriver)


def test_list_tools_advertises_current_time() -> None:
    driver = ClockToolDriver()
    tools = driver.list_tools()
    assert [t.name for t in tools] == ["current_time"]
    tool = tools[0]
    assert isinstance(tool, Tool)
    assert tool.title
    assert tool.description
    param_names = {p.name for p in (tool.parameters or ())}
    assert "timezone" in param_names


def test_execute_default_returns_utc_only() -> None:
    driver = ClockToolDriver()
    before = time.time()
    result = driver.execute_tool("current_time", {})
    after = time.time()
    assert "iso_utc" in result
    assert result["iso_utc"].endswith("+00:00")
    # Tolerate sub-microsecond float rounding between time.time() and
    # datetime(...).timestamp(); the meaningful invariant is "within
    # the call window", not strict ordering.
    assert before - 1.0 <= result["epoch_seconds"] <= after + 1.0
    assert "iso_local" not in result
    assert "timezone" not in result
    assert "timezone_error" not in result


def test_execute_with_timezone_adds_local_repr() -> None:
    driver = ClockToolDriver()
    result = driver.execute_tool("current_time", {"timezone": "Europe/Berlin"})
    assert result["timezone"] == "Europe/Berlin"
    assert "iso_local" in result
    # Berlin is UTC+1 or UTC+2 depending on DST; offset must be one of those.
    assert result["iso_local"].endswith("+01:00") or result["iso_local"].endswith("+02:00")


def test_execute_with_unknown_timezone_soft_fails() -> None:
    driver = ClockToolDriver()
    result = driver.execute_tool("current_time", {"timezone": "Mars/Olympus"})
    assert "iso_utc" in result  # UTC half still present
    assert "timezone_error" in result
    assert "iso_local" not in result


def test_execute_empty_string_timezone_is_ignored() -> None:
    driver = ClockToolDriver()
    result = driver.execute_tool("current_time", {"timezone": ""})
    assert "iso_utc" in result
    assert "iso_local" not in result
    assert "timezone_error" not in result


def test_execute_unknown_tool_raises() -> None:
    driver = ClockToolDriver()
    with pytest.raises(ValueError):
        driver.execute_tool("not-a-tool", {})
