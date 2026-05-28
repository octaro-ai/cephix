"""Tests for :class:`ModelMetadataService` and :class:`BundledLiteLLMSource`.

Two layers exercised:

- The bundled source loads :file:`src/llm/data/models.json` and
  produces a snapshot keyed on ``(provider, model_id)``.
- The metadata service maps the raw row dict into
  :class:`ModelSpec` / :class:`ModelPricing` and serves both via
  the :class:`ModelCatalogPort` / :class:`PricingPort` views.

The audit-trail behaviour around :meth:`refresh` is verified via a
fake source that bumps its ``snapshot_id``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.bus import AsyncioBus, AUDIT_TOPIC, RobotAuditNote, RobotEvent
from src.components import ComponentCategory
from src.llm.metadata_service import ModelMetadataService
from src.llm.ports import ModelCatalogPort, PricingPort
from src.llm.sources import BundledLiteLLMSource
from src.llm.types import ModelPricing, ModelSpec


# ---------------------------------------------------------------------------
# BundledLiteLLMSource
# ---------------------------------------------------------------------------


async def test_bundled_source_loads_default_snapshot() -> None:
    source = BundledLiteLLMSource()
    rows = await source.load()
    assert len(rows) >= 5
    assert ("openai", "gpt-5") in rows
    assert ("anthropic", "claude-3-5-sonnet-20241022") in rows
    assert ("mock", "echo") in rows
    assert source.snapshot_id == "bundled-2026-05"


async def test_bundled_source_skips_malformed_rows(tmp_path: Any) -> None:
    file_path = tmp_path / "models.json"
    file_path.write_text(
        '{"_snapshot_id": "test", "models": {'
        '"good": {"model_id": "g", "provider": "p"},'
        '"bad-no-id": {"provider": "p"},'
        '"bad-no-provider": {"model_id": "g"}'
        "}}",
        encoding="utf-8",
    )
    source = BundledLiteLLMSource(file_path=file_path)
    rows = await source.load()
    assert rows == {("p", "g"): {"model_id": "g", "provider": "p"}}


async def test_bundled_source_rejects_non_json_root(tmp_path: Any) -> None:
    file_path = tmp_path / "models.json"
    file_path.write_text("[]", encoding="utf-8")
    source = BundledLiteLLMSource(file_path=file_path)
    with pytest.raises(ValueError, match="JSON root must be an object"):
        await source.load()


# ---------------------------------------------------------------------------
# ModelMetadataService -- lookup
# ---------------------------------------------------------------------------


async def test_metadata_service_returns_spec_with_first_class_fields() -> None:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    try:
        await service.start(bus)
        spec = service.lookup_spec("gpt-5", "openai")
        assert spec is not None
        assert spec.model_id == "gpt-5"
        assert spec.provider == "openai"
        assert spec.context_window_tokens == 272_000
        assert spec.max_output_tokens == 128_000
        assert spec.supports_function_calling is True
        assert spec.supports_vision is True
        assert spec.supports_response_schema is True
        # Cost-only fields should NOT leak into spec.extras.
        assert "input_cost_per_token" not in spec.extras
        # Unknown-but-passed-through capabilities land in extras.
        assert spec.extras.get("supports_prompt_caching") is True
        assert spec.extras.get("supports_reasoning") is True
        assert spec.extras.get("mode") == "chat"
    finally:
        await service.stop()
        await bus.stop()


async def test_metadata_service_returns_pricing() -> None:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    try:
        await service.start(bus)
        pricing = service.lookup_pricing("gpt-4o-mini", "openai")
        assert pricing is not None
        assert pricing.input_cost_per_token == pytest.approx(0.00000015)
        assert pricing.output_cost_per_token == pytest.approx(0.0000006)
    finally:
        await service.stop()
        await bus.stop()


async def test_metadata_service_returns_none_for_unknown_model() -> None:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    try:
        await service.start(bus)
        assert service.lookup_spec("does-not-exist", "openai") is None
        assert service.lookup_pricing("gpt-5", "fake-provider") is None
    finally:
        await service.stop()
        await bus.stop()


async def test_metadata_service_views_implement_their_ports() -> None:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    try:
        await service.start(bus)
        catalog = service.as_catalog_port()
        pricing = service.as_pricing_port()
        assert isinstance(catalog, ModelCatalogPort)
        assert isinstance(pricing, PricingPort)
        spec = catalog.lookup("ollama/llama3.2".split("/")[1], "ollama")
        assert spec is not None
        assert spec.context_window_tokens == 131_072
        prc = pricing.lookup("llama3.2", "ollama")
        assert prc is not None
        assert prc.input_cost_per_token == 0.0
    finally:
        await service.stop()
        await bus.stop()


def test_metadata_service_metadata() -> None:
    assert ModelMetadataService.component_name == "model-metadata"
    assert ModelMetadataService.component_category is (
        ComponentCategory.GOVERNANCE
    )


# ---------------------------------------------------------------------------
# ModelMetadataService -- refresh + audit trail
# ---------------------------------------------------------------------------


class _FakeSource:
    """Mutable source the tests can flip to simulate a refresh.

    Lets us prove the metadata service:
    - is silent on the initial load (no audit note);
    - emits a ``pricing.refresh`` audit note when ``refresh()``
      detects a new snapshot_id;
    - emits a ``pricing.refresh.failed`` audit note when the
      source raises during refresh.
    """

    def __init__(
        self,
        *,
        rows: dict[tuple[str, str], dict[str, Any]],
        snapshot_id: str,
    ) -> None:
        self._rows = dict(rows)
        self._snapshot_id = snapshot_id
        self._fail_next = False

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    def set(
        self,
        *,
        rows: dict[tuple[str, str], dict[str, Any]],
        snapshot_id: str,
    ) -> None:
        self._rows = dict(rows)
        self._snapshot_id = snapshot_id

    def fail_next_load(self) -> None:
        self._fail_next = True

    async def load(self) -> dict[tuple[str, str], dict[str, Any]]:
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("fake-source failure")
        return dict(self._rows)


async def _audit_collector(bus: AsyncioBus) -> list[RobotAuditNote]:
    notes: list[RobotAuditNote] = []

    async def handler(event: RobotEvent) -> None:
        if isinstance(event, RobotAuditNote):
            notes.append(event)

    bus.subscribe(AUDIT_TOPIC, handler)
    return notes


async def test_initial_load_is_audit_silent() -> None:
    bus = AsyncioBus()
    await bus.start()
    notes = await _audit_collector(bus)

    source = _FakeSource(
        rows={("p", "m"): {"model_id": "m", "provider": "p"}},
        snapshot_id="v1",
    )
    service = ModelMetadataService(source=source)
    try:
        await service.start(bus)
        await asyncio.sleep(0.02)
    finally:
        await service.stop()
        await bus.stop()

    assert notes == []


async def test_refresh_publishes_audit_note_when_snapshot_changes() -> None:
    bus = AsyncioBus()
    await bus.start()
    notes = await _audit_collector(bus)

    source = _FakeSource(
        rows={("p", "m"): {"model_id": "m", "provider": "p"}},
        snapshot_id="v1",
    )
    service = ModelMetadataService(source=source)
    try:
        await service.start(bus)
        # No change yet -> no audit.
        changed = await service.refresh()
        assert changed is False
        assert notes == []

        # Change snapshot id and refresh.
        source.set(
            rows={
                ("p", "m"): {"model_id": "m", "provider": "p"},
                ("p2", "m2"): {"model_id": "m2", "provider": "p2"},
            },
            snapshot_id="v2",
        )
        changed = await service.refresh()
        assert changed is True
        assert service.lookup_spec("m2", "p2") is not None
        await asyncio.sleep(0.02)
    finally:
        await service.stop()
        await bus.stop()

    assert len(notes) == 1
    note = notes[0]
    assert note.action == "pricing.refresh"
    assert note.source == "model-metadata"
    assert note.details["before_snapshot_id"] == "v1"
    assert note.details["after_snapshot_id"] == "v2"
    assert note.details["rows"] == 2


async def test_refresh_failure_publishes_audit_and_reraises() -> None:
    bus = AsyncioBus()
    await bus.start()
    notes = await _audit_collector(bus)

    source = _FakeSource(
        rows={("p", "m"): {"model_id": "m", "provider": "p"}},
        snapshot_id="v1",
    )
    service = ModelMetadataService(source=source)
    try:
        await service.start(bus)
        source.fail_next_load()
        with pytest.raises(RuntimeError, match="fake-source failure"):
            await service.refresh()
        await asyncio.sleep(0.02)
    finally:
        await service.stop()
        await bus.stop()

    assert len(notes) == 1
    note = notes[0]
    assert note.action == "pricing.refresh.failed"
    assert "fake-source failure" in note.details["reason"]
