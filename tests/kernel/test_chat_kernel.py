"""Tests for :class:`ChatKernel`.

Three groups:

- Construction validation (LLMActorPort required; EchoActor rejected).
- :meth:`plan` semantics (session resolution, history loading,
  firmware system prompt, ``session_new`` signalling via
  :class:`~src.bus.messages.KernelPhase`).
- :meth:`act` cost calculation via :class:`ModelCatalog`.
- :meth:`finalize` OCF-shaped persistence (user + assistant
  records with the OCF usage field vocabulary).

The kernel is exercised end-to-end against a real
:class:`AsyncioBus` so the :class:`KernelPhase` telemetry path is
covered as well.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.actor.echo import EchoActor
from src.actor.llm.types import ChatMessage, LLMReply, LLMUsage
from src.actor.llm.actor_base import LLMActorBase
from src.bus import (
    AsyncioBus,
    KERNEL_PHASE_TOPIC,
    KernelPhase,
    RobotEvent,
    RobotInput,
    RobotOutput,
)
from src.kernel.chat import ChatKernel
from src.utility.firmware_store.ports import FirmwareStorePort
from src.utility.model_catalog.ports import ModelCatalogPort
from src.utility.model_catalog.types import ModelPricing, ModelSpec
from src.persistence.filesystem.connection import FilesystemConnection
from src.persistence.filesystem.local_adapter import LocalFSAdapter
from src.utility.session_store import (
    FilesystemSessionStore,
    SessionMessage,
    new_message_id,
)


def _session_store(root: Path) -> FilesystemSessionStore:
    """Build a :class:`FilesystemSessionStore` rooted at ``root``."""
    connection = FilesystemConnection(adapter=LocalFSAdapter(), root=root)
    return FilesystemSessionStore(connection=connection)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubLLM(LLMActorBase):
    """Captures every actor_context; returns a configurable reply."""

    component_name = "stub.llm"

    def __init__(
        self,
        *,
        model_id: str = "gpt-stub",
        provider: str = "openai",
        reply_text: str = "hi back",
        usage: LLMUsage | None = None,
    ) -> None:
        super().__init__(model_id=model_id, provider=provider)
        self._reply_text = reply_text
        self._usage = usage or LLMUsage(tokens_in=10, tokens_out=5)
        self.contexts: list[dict[str, Any]] = []

    async def _chat_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMReply:
        del max_output_tokens, temperature
        # Snapshot the message list so the test can assert against it.
        self.contexts.append(
            {"messages": [{"role": m.role, "content": m.content} for m in messages]}
        )
        return LLMReply(
            text=self._reply_text,
            finish_reason="stop",
            usage=self._usage,
        )


class _StubFirmware(FirmwareStorePort):
    def __init__(
        self,
        documents: dict[str, str] | None = None,
        *,
        system_prompt: str | None = None,
    ) -> None:
        self._documents = documents or {}
        self._system_prompt = (
            system_prompt
            if system_prompt is not None
            else "\n\n".join(
                f"## {n}\n{c.strip()}"
                for n, c in self._documents.items()
                if c.strip()
            )
        )

    def documents(self):  # type: ignore[override]
        return dict(self._documents)

    def system_prompt(self) -> str:  # type: ignore[override]
        return self._system_prompt

    def refresh(self) -> None:  # type: ignore[override]
        return None


class _StubCatalog(ModelCatalogPort):
    def __init__(
        self,
        *,
        spec: ModelSpec | None = None,
        pricing: ModelPricing | None = None,
    ) -> None:
        self._spec = spec
        self._pricing = pricing
        self.spec_calls: list[tuple[str, str]] = []
        self.pricing_calls: list[tuple[str, str]] = []

    def lookup_spec(
        self, model_id: str, provider: str
    ) -> ModelSpec | None:  # type: ignore[override]
        self.spec_calls.append((model_id, provider))
        return self._spec

    def lookup_pricing(
        self, model_id: str, provider: str
    ) -> ModelPricing | None:  # type: ignore[override]
        self.pricing_calls.append((model_id, provider))
        return self._pricing


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_requires_llm_actor_port(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="LLMActorPort"):
            ChatKernel(
                actor=EchoActor(),
                firmware=_StubFirmware(),
                sessions=_session_store(tmp_path),
                model_catalog=_StubCatalog(),
            )

    def test_accepts_llm_actor(self, tmp_path: Path) -> None:
        kernel = ChatKernel(
            actor=_StubLLM(),
            firmware=_StubFirmware(),
            sessions=_session_store(tmp_path),
            model_catalog=_StubCatalog(),
        )
        assert kernel.component_name == "chat"

    def test_inherits_base_kernel_actor_timeout(self, tmp_path: Path) -> None:
        kernel = ChatKernel(
            actor=_StubLLM(),
            firmware=_StubFirmware(),
            sessions=_session_store(tmp_path),
            model_catalog=_StubCatalog(),
            actor_timeout=5.0,
        )
        assert kernel._actor_timeout == 5.0


# ---------------------------------------------------------------------------
# End-to-end helper
# ---------------------------------------------------------------------------


async def _drive_one_input(
    *,
    kernel: ChatKernel,
    bus: AsyncioBus,
    payload: dict[str, Any],
    message: str = "hi",
) -> tuple[list[RobotOutput], list[KernelPhase]]:
    outputs: list[RobotOutput] = []
    phases: list[KernelPhase] = []

    async def collect_out(event: RobotEvent) -> None:
        if isinstance(event, RobotOutput):
            outputs.append(event)

    async def collect_phase(event: RobotEvent) -> None:
        if isinstance(event, KernelPhase):
            phases.append(event)

    bus.subscribe("output.message", collect_out)
    bus.subscribe(KERNEL_PHASE_TOPIC, collect_phase)

    await bus.publish(
        RobotInput(
            topic="input.message",
            principal="user-1",
            source="channel.test",
            run_id="run-1",
            message=message,
            payload=payload,
        )
    )
    # Let the bus drain.
    for _ in range(5):
        await asyncio.sleep(0.02)
    return outputs, phases


async def _build_running_kernel(
    *,
    bus: AsyncioBus,
    sessions_dir: Path,
    firmware: _StubFirmware | None = None,
    catalog: _StubCatalog | None = None,
    actor: _StubLLM | None = None,
) -> tuple[
    ChatKernel, _StubLLM, FilesystemSessionStore, _StubFirmware, _StubCatalog
]:
    actor = actor or _StubLLM()
    firmware = firmware or _StubFirmware()
    sessions = _session_store(sessions_dir)
    catalog = catalog or _StubCatalog()
    kernel = ChatKernel(
        actor=actor,
        firmware=firmware,
        sessions=sessions,
        model_catalog=catalog,
        actor_timeout=2.0,
    )
    await actor.start()
    await kernel.start(bus)
    return kernel, actor, sessions, firmware, catalog


# ---------------------------------------------------------------------------
# plan() semantics
# ---------------------------------------------------------------------------


class TestPlan:
    async def test_uses_payload_session_id_when_present(
        self, tmp_path: Path
    ) -> None:
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            try:
                _, phases = await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": "sess_predefined"},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        plan = next(p for p in phases if p.phase == "planning")
        assert plan.details["session_id"] == "sess_predefined"
        # First contact with the kernel -> session_new=True.
        assert plan.details["session_new"] is True
        assert plan.message == "new session sess_predefined"

    async def test_mints_id_when_payload_has_none(
        self, tmp_path: Path
    ) -> None:
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            try:
                _, phases = await _drive_one_input(
                    kernel=kernel, bus=bus, payload={}
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        plan = next(p for p in phases if p.phase == "planning")
        sid = plan.details["session_id"]
        assert sid.startswith("sess_")
        assert plan.details["session_new"] is True

    async def test_existing_session_reports_session_new_false(
        self, tmp_path: Path
    ) -> None:
        # Seed an existing session.
        seed_store = _session_store(tmp_path)
        sid = "sess_resume"
        await seed_store.append(
            sid,
            SessionMessage(
                id=new_message_id(),
                created_at="2024-01-01T00:00:00Z",
                message=ChatMessage(role="user", content="hello"),
            ),
        )
        await seed_store.append(
            sid,
            SessionMessage(
                id=new_message_id(),
                created_at="2024-01-01T00:00:00Z",
                message=ChatMessage(role="assistant", content="hi"),
            ),
        )
        # Close the cached writer so the kernel's own store opens
        # the file fresh and the seeded lines are durable on disk.
        await seed_store.stop()

        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, _, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            try:
                _, phases = await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": sid},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        plan = next(p for p in phases if p.phase == "planning")
        assert plan.details["session_id"] == sid
        assert plan.details["session_new"] is False
        # No "new session ..." message on a resume.
        assert plan.message == ""
        # History was loaded (two prior messages).
        assert plan.details["history_messages"] == 2

    async def test_passes_history_and_system_prompt_to_actor(
        self, tmp_path: Path
    ) -> None:
        firmware = _StubFirmware(system_prompt="## CONST\nbe nice")
        seed_store = _session_store(tmp_path)
        sid = "sess_with_history"
        await seed_store.append(
            sid,
            SessionMessage(
                id=new_message_id(),
                created_at="2024-01-01T00:00:00Z",
                message=ChatMessage(role="user", content="prev user"),
            ),
        )
        await seed_store.append(
            sid,
            SessionMessage(
                id=new_message_id(),
                created_at="2024-01-01T00:00:00Z",
                message=ChatMessage(
                    role="assistant", content="prev assistant"
                ),
            ),
        )
        await seed_store.stop()

        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, _, _, _ = await _build_running_kernel(
                bus=bus,
                sessions_dir=tmp_path,
                firmware=firmware,
            )
            try:
                await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": sid},
                    message="current user",
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        assert len(actor.contexts) == 1
        wire = actor.contexts[0]["messages"]
        roles = [m["role"] for m in wire]
        # System (from firmware) + 2 history + 1 current user.
        assert roles == ["system", "user", "assistant", "user"]
        assert wire[0]["content"] == "## CONST\nbe nice"
        assert wire[1]["content"] == "prev user"
        assert wire[-1]["content"] == "current user"

    async def test_publishes_context_window_when_spec_available(
        self, tmp_path: Path
    ) -> None:
        catalog = _StubCatalog(
            spec=ModelSpec(
                model_id="gpt-stub",
                provider="openai",
                context_window_tokens=16_000,
                max_output_tokens=4_000,
            )
        )

        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, _, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path, catalog=catalog
            )
            try:
                _, phases = await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": "sess_spec"},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        plan = next(p for p in phases if p.phase == "planning")
        assert plan.details["context_window_tokens"] == 16_000


# ---------------------------------------------------------------------------
# act() cost calculation
# ---------------------------------------------------------------------------


class TestAct:
    async def test_cost_calculated_from_catalog_pricing(
        self, tmp_path: Path
    ) -> None:
        pricing = ModelPricing(
            model_id="gpt-stub",
            provider="openai",
            input_cost_per_token=0.001,
            output_cost_per_token=0.002,
        )
        catalog = _StubCatalog(pricing=pricing)
        actor = _StubLLM(
            usage=LLMUsage(tokens_in=10, tokens_out=20)
        )

        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, _, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path, catalog=catalog, actor=actor
            )
            try:
                _, phases = await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": "sess_cost"},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        act_phase = next(p for p in phases if p.phase == "acting")
        # 10 * 0.001 + 20 * 0.002 = 0.05
        assert act_phase.details["cost_usd"] == pytest.approx(0.05)

    async def test_cost_zero_when_pricing_missing(
        self, tmp_path: Path
    ) -> None:
        catalog = _StubCatalog(pricing=None)
        actor = _StubLLM(usage=LLMUsage(tokens_in=100, tokens_out=10))

        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, _, _, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path, catalog=catalog, actor=actor
            )
            try:
                _, phases = await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": "sess_no_price"},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        act = next(p for p in phases if p.phase == "acting")
        assert act.details["cost_usd"] == 0.0

    async def test_context_fill_ratio_published(
        self, tmp_path: Path
    ) -> None:
        catalog = _StubCatalog(
            spec=ModelSpec(
                model_id="gpt-stub",
                provider="openai",
                context_window_tokens=1_000,
                max_output_tokens=100,
            )
        )
        actor = _StubLLM(usage=LLMUsage(tokens_in=250, tokens_out=50))

        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, _, _, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path, catalog=catalog, actor=actor
            )
            try:
                _, phases = await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": "sess_fill"},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        act = next(p for p in phases if p.phase == "acting")
        assert act.details["context_fill_ratio"] == 0.25


# ---------------------------------------------------------------------------
# finalize() persists OCF-shaped JSONL
# ---------------------------------------------------------------------------


class TestFinalize:
    async def test_appends_user_then_assistant(self, tmp_path: Path) -> None:
        pricing = ModelPricing(
            model_id="gpt-stub",
            provider="openai",
            input_cost_per_token=0.0001,
            output_cost_per_token=0.0002,
        )
        catalog = _StubCatalog(pricing=pricing)
        actor = _StubLLM(
            reply_text="hello back",
            usage=LLMUsage(
                tokens_in=10, tokens_out=5, reasoning_tokens=2, cache_read_tokens=3
            ),
        )

        sid = "sess_persist"
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, _, sessions, _, _ = await _build_running_kernel(
                bus=bus,
                sessions_dir=tmp_path,
                catalog=catalog,
                actor=actor,
            )
            try:
                await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": sid},
                    message="hello there",
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        history = await sessions.messages(sid)
        assert len(history) == 2
        user, assistant = history
        assert user.message.role == "user"
        assert user.message.content == "hello there"
        assert assistant.message.role == "assistant"
        assert assistant.message.content == "hello back"

    async def test_assistant_record_uses_ocf_usage_field_names(
        self, tmp_path: Path
    ) -> None:
        pricing = ModelPricing(
            model_id="gpt-stub",
            provider="openai",
            input_cost_per_token=0.001,
            output_cost_per_token=0.002,
        )
        catalog = _StubCatalog(pricing=pricing)
        actor = _StubLLM(
            usage=LLMUsage(
                tokens_in=10,
                tokens_out=20,
                reasoning_tokens=4,
                cache_read_tokens=2,
                cache_write_tokens=1,
            )
        )

        sid = "sess_usage_shape"
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, _, sessions, _, _ = await _build_running_kernel(
                bus=bus,
                sessions_dir=tmp_path,
                catalog=catalog,
                actor=actor,
            )
            try:
                await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": sid},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        history = await sessions.messages(sid)
        assistant = history[-1]
        usage = assistant.usage or {}
        assert usage["input"] == 10
        assert usage["output"] == 20
        assert usage["thinking"] == 4
        assert usage["cache_read"] == 2
        assert usage["cache_write"] == 1
        # cost_usd is the kernel's contribution -- additionalProperty.
        assert usage["cost_usd"] == pytest.approx(
            10 * 0.001 + 20 * 0.002
        )
        assert assistant.model == "gpt-stub"

    async def test_history_survives_across_runs(self, tmp_path: Path) -> None:
        sid = "sess_chain"
        actor = _StubLLM(reply_text="reply-1")
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, _, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path, actor=actor
            )
            try:
                await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": sid},
                    message="first",
                )
                await _drive_one_input(
                    kernel=kernel,
                    bus=bus,
                    payload={"session_id": sid},
                    message="second",
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        # Two round-trips => four persisted records.
        history = await sessions.messages(sid)
        assert [m.message.role for m in history] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert history[0].message.content == "first"
        assert history[2].message.content == "second"

        # On the second turn the actor saw the first turn in its history.
        second_call = actor.contexts[-1]["messages"]
        roles = [m["role"] for m in second_call]
        # No firmware on this stub firmware -> no system prepend.
        assert roles == ["user", "assistant", "user"]
        assert second_call[0]["content"] == "first"
        assert second_call[-1]["content"] == "second"


# ---------------------------------------------------------------------------
# Session commands (chat.session.{new,list,open,rename})
# ---------------------------------------------------------------------------


async def _invoke_command(
    *,
    bus: AsyncioBus,
    action: str,
    payload: dict[str, Any] | None = None,
):
    """Publish a CommandRequest and return the matching CommandResponse."""
    from src.bus import (
        CommandRequest,
        CommandResponse,
        command_request_topic,
        command_response_topic,
    )

    responses: list[CommandResponse] = []

    async def collect(event: RobotEvent) -> None:
        if isinstance(event, CommandResponse):
            responses.append(event)

    bus.subscribe(command_response_topic(action), collect)
    await bus.publish(
        CommandRequest(
            topic=command_request_topic(action),
            principal="user-1",
            source="channel.test",
            run_id="run-cmd",
            correlation_id=f"cmd-{action}",
            action=action,
            payload=payload or {},
        )
    )
    for _ in range(5):
        await asyncio.sleep(0.02)
    assert responses, f"no CommandResponse for {action}"
    return responses[-1]


class TestSessionCommands:
    async def test_kernel_advertises_session_commands(self, tmp_path: Path) -> None:
        actions = {spec.action for spec in ChatKernel.provides_commands}
        assert actions == {
            "chat.session.new",
            "chat.session.list",
            "chat.session.open",
            "chat.session.rename",
        }

    async def test_session_new_creates_session(self, tmp_path: Path) -> None:
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            try:
                resp = await _invoke_command(bus=bus, action="chat.session.new")
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        assert resp.status == "ok"
        sid = resp.payload["session_id"]
        assert sid.startswith("sess_")
        summaries = await sessions.list_sessions()
        assert any(s.session_id == sid for s in summaries)

    async def test_session_list_returns_summaries(self, tmp_path: Path) -> None:
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            await sessions.append(
                "sess_known",
                SessionMessage(
                    id=new_message_id(),
                    created_at="2024-01-01T00:00:00Z",
                    message=ChatMessage(role="user", content="hi"),
                ),
            )
            try:
                resp = await _invoke_command(bus=bus, action="chat.session.list")
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        assert resp.status == "ok"
        ids = [s["session_id"] for s in resp.payload["sessions"]]
        assert "sess_known" in ids

    async def test_session_open_returns_history(self, tmp_path: Path) -> None:
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            await sessions.append(
                "sess_open",
                SessionMessage(
                    id=new_message_id(),
                    created_at="2024-01-01T00:00:00Z",
                    message=ChatMessage(role="user", content="remember this"),
                ),
            )
            try:
                resp = await _invoke_command(
                    bus=bus,
                    action="chat.session.open",
                    payload={"session_id": "sess_open"},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        assert resp.status == "ok"
        assert resp.payload["session_id"] == "sess_open"
        assert resp.payload["created"] is False
        assert resp.payload["messages"][0]["content"] == "remember this"

    async def test_session_open_without_id_errors(self, tmp_path: Path) -> None:
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, _, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            try:
                resp = await _invoke_command(
                    bus=bus, action="chat.session.open", payload={}
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        assert resp.status == "error"
        assert resp.error is not None
        assert resp.error.code == "command.handler_failed"

    async def test_session_rename_sets_title(self, tmp_path: Path) -> None:
        bus = AsyncioBus()
        await bus.start()
        try:
            kernel, actor, sessions, _, _ = await _build_running_kernel(
                bus=bus, sessions_dir=tmp_path
            )
            await sessions.append(
                "sess_rename",
                SessionMessage(
                    id=new_message_id(),
                    created_at="2024-01-01T00:00:00Z",
                    message=ChatMessage(role="user", content="x"),
                ),
            )
            try:
                resp = await _invoke_command(
                    bus=bus,
                    action="chat.session.rename",
                    payload={"session_id": "sess_rename", "title": "My Chat"},
                )
            finally:
                await kernel.stop()
                await actor.stop()
        finally:
            await bus.stop()

        assert resp.status == "ok"
        summaries = await sessions.list_sessions()
        (summary,) = [
            s for s in summaries if s.session_id == "sess_rename"
        ]
        assert summary.title == "My Chat"
