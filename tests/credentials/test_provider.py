"""Tests for :class:`CredentialProvider`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.bus.asyncio_bus import AsyncioBus
from src.bus.messages import AUDIT_TOPIC, RobotAuditNote
from src.components import ComponentCategory
from src.credentials.exceptions import CredentialNotFound
from src.credentials.ports import CredentialStorePort
from src.credentials.provider import CredentialProvider


class _DictStore(CredentialStorePort):
    """Minimal CredentialStorePort for testing.

    Inherits from the abstract port explicitly: that is what the
    ABC-based interface requires of every implementation.
    """

    def __init__(self, name: str, values: dict[str, str]) -> None:
        self._name = name
        self._values = dict(values)

    @property
    def name(self) -> str:
        return self._name

    def lookup(self, key: str) -> str | None:
        return self._values.get(key)

    def has_key(self, key: str) -> bool:
        return key in self._values


class TestProviderConstruction:
    def test_empty_provider_has_no_stores(self) -> None:
        provider = CredentialProvider(stores=[])
        assert provider.store_names == ()

    def test_store_names_in_order(self) -> None:
        provider = CredentialProvider(
            stores=[_DictStore("a", {}), _DictStore("b", {})]
        )
        assert provider.store_names == ("a", "b")

    def test_rejects_non_store(self) -> None:
        with pytest.raises(TypeError, match="CredentialStorePort"):
            CredentialProvider(stores=["not-a-store"])  # type: ignore[list-item]

    def test_category_is_bus_utility(self) -> None:
        assert CredentialProvider.component_category is ComponentCategory.BUS_UTILITY

    def test_component_name(self) -> None:
        assert CredentialProvider.component_name == "credentials"


class TestSyncResolution:
    def test_first_store_wins(self) -> None:
        provider = CredentialProvider(
            stores=[
                _DictStore("first", {"KEY": "from-first"}),
                _DictStore("second", {"KEY": "from-second"}),
            ]
        )
        assert provider.resolve_sync("KEY") == "from-first"

    def test_falls_back_to_later_store(self) -> None:
        provider = CredentialProvider(
            stores=[
                _DictStore("first", {}),
                _DictStore("second", {"KEY": "from-second"}),
            ]
        )
        assert provider.resolve_sync("KEY") == "from-second"

    def test_unresolved_raises(self) -> None:
        provider = CredentialProvider(
            stores=[_DictStore("a", {}), _DictStore("b", {})]
        )
        with pytest.raises(CredentialNotFound) as excinfo:
            provider.resolve_sync("MISSING")
        assert excinfo.value.key == "MISSING"
        assert excinfo.value.stores_tried == ("a", "b")

    def test_unresolved_includes_requester_in_message(self) -> None:
        provider = CredentialProvider(stores=[])
        with pytest.raises(CredentialNotFound) as excinfo:
            provider.resolve_sync("MISSING", requester="builder")
        assert "builder" in str(excinfo.value)
        assert excinfo.value.requester == "builder"

    def test_empty_provider_always_raises(self) -> None:
        provider = CredentialProvider(stores=[])
        with pytest.raises(CredentialNotFound):
            provider.resolve_sync("X")

    def test_has_key(self) -> None:
        provider = CredentialProvider(
            stores=[
                _DictStore("a", {"YES": "v"}),
                _DictStore("b", {"OTHER": "v2"}),
            ]
        )
        assert provider.has_key("YES") is True
        assert provider.has_key("OTHER") is True
        assert provider.has_key("NO") is False


class TestAsyncResolution:
    async def test_async_resolve_returns_value(self) -> None:
        provider = CredentialProvider(stores=[_DictStore("a", {"K": "v"})])
        assert await provider.resolve("K") == "v"

    async def test_async_resolve_raises_for_missing(self) -> None:
        provider = CredentialProvider(stores=[_DictStore("a", {})])
        with pytest.raises(CredentialNotFound):
            await provider.resolve("MISSING")


class TestAuditEmission:
    async def test_resolve_emits_credential_resolved_audit(self) -> None:
        bus = AsyncioBus()
        await bus.start()
        notes: list[RobotAuditNote] = []
        bus.subscribe(
            AUDIT_TOPIC,
            lambda e: _capture(notes, e),
        )

        provider = CredentialProvider(stores=[_DictStore("a", {"K": "v"})])
        try:
            await provider.start(bus)
            value = await provider.resolve("K", requester="test")
            assert value == "v"
            await asyncio.sleep(0.05)  # let the audit deliver
        finally:
            await provider.stop()
            await bus.stop()

        assert any(
            isinstance(n, RobotAuditNote)
            and n.action == "credential.resolved"
            and n.details.get("key") == "K"
            and n.details.get("served_by") == "a"
            and n.details.get("requester") == "test"
            for n in notes
        )

    async def test_resolve_does_not_leak_value_in_audit(self) -> None:
        bus = AsyncioBus()
        await bus.start()
        notes: list[RobotAuditNote] = []
        bus.subscribe(AUDIT_TOPIC, lambda e: _capture(notes, e))

        provider = CredentialProvider(stores=[_DictStore("a", {"K": "supersecret"})])
        try:
            await provider.start(bus)
            await provider.resolve("K", requester="test")
            await asyncio.sleep(0.05)
        finally:
            await provider.stop()
            await bus.stop()

        for note in notes:
            assert "supersecret" not in str(note.details), (
                f"audit note leaked the value: {note.details}"
            )

    async def test_unresolved_emits_credential_not_found_audit(self) -> None:
        bus = AsyncioBus()
        await bus.start()
        notes: list[RobotAuditNote] = []
        bus.subscribe(AUDIT_TOPIC, lambda e: _capture(notes, e))

        provider = CredentialProvider(stores=[_DictStore("a", {})])
        try:
            await provider.start(bus)
            with pytest.raises(CredentialNotFound):
                await provider.resolve("MISSING", requester="test")
            await asyncio.sleep(0.05)
        finally:
            await provider.stop()
            await bus.stop()

        assert any(
            isinstance(n, RobotAuditNote)
            and n.action == "credential.not_found"
            and n.details.get("key") == "MISSING"
            and n.details.get("stores_tried") == ["a"]
            for n in notes
        )

    async def test_resolve_sync_without_bus_does_not_raise(self) -> None:
        """Boot-time path: provider not yet started, sync resolve still works."""
        provider = CredentialProvider(stores=[_DictStore("a", {"K": "v"})])
        # No bus attached yet. resolve_sync should still return.
        assert provider.resolve_sync("K") == "v"


async def _capture(notes: list, event: Any) -> None:
    notes.append(event)
