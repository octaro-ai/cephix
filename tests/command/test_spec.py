"""Tests for :class:`CommandSpec` and :class:`RiskClass`."""

from __future__ import annotations

import pytest

from src.command import CommandSpec, RiskClass


def test_spec_defaults() -> None:
    spec = CommandSpec(action="chat.session.new", handler="cmd_session_new")
    assert spec.action == "chat.session.new"
    assert spec.handler == "cmd_session_new"
    assert spec.label == ""
    assert spec.args_schema == {}
    assert spec.risk_class is RiskClass.READ_ONLY
    assert spec.discriminator is None
    assert spec.ui_hints == {}


def test_spec_requires_action() -> None:
    with pytest.raises(ValueError, match="non-empty action"):
        CommandSpec(action="", handler="cmd_x")


def test_spec_requires_handler() -> None:
    with pytest.raises(ValueError, match="non-empty handler"):
        CommandSpec(action="chat.session.new", handler="")


def test_manifest_entry_serialization() -> None:
    spec = CommandSpec(
        action="mail.send",
        handler="cmd_send",
        label="Send mail",
        description="Send an email",
        args_schema={"to": "string"},
        risk_class=RiskClass.HIGH_RISK_MUTATION,
        discriminator="gmail",
        ui_hints={"group": "mail"},
    )
    entry = spec.manifest_entry(
        owner_component="tool.mail", owner_instance_id="abc123"
    )
    assert entry == {
        "action": "mail.send",
        "label": "Send mail",
        "description": "Send an email",
        "args_schema": {"to": "string"},
        "risk_class": "high_risk_mutation",
        "discriminator": "gmail",
        "ui_hints": {"group": "mail"},
        "owner_component": "tool.mail",
        "owner_instance_id": "abc123",
    }


def test_manifest_entry_copies_mutable_fields() -> None:
    schema = {"to": "string"}
    hints = {"group": "mail"}
    spec = CommandSpec(
        action="mail.send",
        handler="cmd_send",
        args_schema=schema,
        ui_hints=hints,
    )
    entry = spec.manifest_entry(owner_component="x", owner_instance_id="y")
    entry["args_schema"]["to"] = "mutated"
    entry["ui_hints"]["group"] = "mutated"
    # Original spec data is untouched.
    assert spec.args_schema == {"to": "string"}
    assert spec.ui_hints == {"group": "mail"}
