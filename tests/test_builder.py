"""Tests for src.builder.build_robot_from_config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.actor.echo import EchoActor
from src.audit.note_sink import AuditNoteSink
from src.builder import build_robot_from_config
from src.bus.asyncio_bus import AsyncioBus
from src.channels.websocket import WebsocketChannel
from src.kernel.base import BaseKernel
from src.registry import ConfigError
from src.robot import Robot
from src.telemetry.bus_recorder import BusRecorder

# control plane is not what these tests are about; force it off so the
# builder produces a robot that won't try to bind ports if anyone
# actually starts it.
_CP_OFF: dict[str, Any] = {"control_plane": {"enabled": False}}

# An actor is now mandatory: the kernel always has someone to consult.
# Tests that don't care which actor get a default echo.
_DEFAULT_ACTOR: dict[str, Any] = {"actor": {"name": "echo"}}


def _cfg(extra: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(_CP_OFF)
    merged.update(_DEFAULT_ACTOR)
    merged.update(extra)
    return merged


def _channels_of(robot: Robot) -> tuple[WebsocketChannel, ...]:
    """Pull the WebsocketChannel(s) out of robot.components."""
    return tuple(
        c for c in robot.components if isinstance(c, WebsocketChannel)
    )


def _bus_of(robot: Robot) -> AsyncioBus:
    bus = robot.components[0]
    assert isinstance(bus, AsyncioBus)
    return bus


def _kernel_of(robot: Robot) -> BaseKernel:
    for c in robot.components:
        if isinstance(c, BaseKernel):
            return c
    raise AssertionError("no BaseKernel in robot.components")


def _actor_of(robot: Robot) -> EchoActor | None:
    for c in robot.components:
        if isinstance(c, EchoActor):
            return c
    return None


def test_builder_assembles_minimum_robot() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "id": "x",
                "name": "X",
                "enabled": True,
                "kernel": {"name": "base"},
            }
        )
    )
    assert isinstance(robot, Robot)
    assert isinstance(_bus_of(robot), AsyncioBus)
    assert isinstance(_kernel_of(robot), BaseKernel)
    assert isinstance(_actor_of(robot), EchoActor)
    assert _channels_of(robot) == ()


def test_builder_uses_default_bus_when_missing() -> None:
    robot = build_robot_from_config(_cfg({"kernel": {"name": "base"}}))
    assert isinstance(_bus_of(robot), AsyncioBus)


def test_builder_assembles_channels() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"name": "base"},
                "channels": [{"name": "websocket", "port": 0}],
            }
        )
    )
    channels = _channels_of(robot)
    assert len(channels) == 1
    assert isinstance(channels[0], WebsocketChannel)


def test_builder_passes_actor_kwargs() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"name": "base"},
                "actor": {"name": "echo", "prefix": "yo: "},
            }
        )
    )
    actor = _actor_of(robot)
    assert isinstance(actor, EchoActor)
    assert actor._prefix == "yo: "  # type: ignore[attr-defined]


def test_builder_injects_actor_into_kernel() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"name": "base"},
                "actor": {"name": "echo", "prefix": "yo: "},
            }
        )
    )
    kernel = _kernel_of(robot)
    actor = _actor_of(robot)
    assert actor is not None
    assert kernel._actor is actor  # type: ignore[attr-defined]


def test_builder_passes_kernel_kwargs() -> None:
    robot = build_robot_from_config(
        _cfg({"kernel": {"name": "base", "actor_timeout": 12.5}})
    )
    kernel = _kernel_of(robot)
    assert kernel._actor_timeout == 12.5  # type: ignore[attr-defined]


def test_builder_merges_defaults_with_robot_yaml() -> None:
    defaults = {
        "kernel": {"name": "base", "actor_timeout": 60.0},
        "actor": {"name": "echo", "prefix": "default: "},
        "channels": [{"name": "websocket", "port": 9999}],
        "control_plane": {"enabled": False},
    }
    robot_yaml = {
        "actor": {"prefix": "override: "},
    }
    robot = build_robot_from_config(robot_yaml, defaults=defaults)
    kernel = _kernel_of(robot)
    actor = _actor_of(robot)
    channels = _channels_of(robot)
    assert kernel._actor_timeout == 60.0  # type: ignore[attr-defined]
    assert actor is not None
    assert actor._prefix == "override: "  # type: ignore[attr-defined]
    assert channels[0]._port == 9999  # type: ignore[attr-defined]


def test_builder_robot_yaml_replaces_default_channels() -> None:
    defaults = {
        "channels": [{"name": "websocket", "port": 1111}],
        "control_plane": {"enabled": False},
        "actor": {"name": "echo"},
    }
    robot_yaml = {
        "kernel": {"name": "base"},
        "channels": [{"name": "websocket", "port": 2222}],
    }
    robot = build_robot_from_config(robot_yaml, defaults=defaults)
    channels = _channels_of(robot)
    assert len(channels) == 1
    assert channels[0]._port == 2222  # type: ignore[attr-defined]


def test_builder_rejects_missing_kernel() -> None:
    with pytest.raises(ConfigError, match="kernel"):
        build_robot_from_config(_cfg({"id": "x"}))


def test_builder_rejects_missing_actor() -> None:
    """The actor section is mandatory: the kernel always needs one."""
    cfg = dict(_CP_OFF)
    cfg["kernel"] = {"name": "base"}
    with pytest.raises(ConfigError, match="actor"):
        build_robot_from_config(cfg)


def test_builder_rejects_non_dict_top_level() -> None:
    with pytest.raises(ConfigError, match="mapping"):
        build_robot_from_config([])  # type: ignore[arg-type]


def test_builder_rejects_non_list_channels() -> None:
    with pytest.raises(ConfigError, match="channels"):
        build_robot_from_config(
            _cfg({"kernel": {"name": "base"}, "channels": {"name": "websocket"}})
        )


def test_builder_rejects_actor_that_is_not_an_actor_port() -> None:
    """If the actor: spec resolves to a non-ActorPort, fail loudly."""
    cfg = dict(_CP_OFF)
    cfg["kernel"] = {"name": "base"}
    cfg["actor"] = {"name": "asyncio"}  # AsyncioBus is not an ActorPort
    with pytest.raises(ConfigError, match="ActorPort"):
        build_robot_from_config(cfg)


def test_builder_propagates_identity_to_robot() -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "id": "alpha",
                "name": "Alpha",
                "enabled": False,
                "kernel": {"name": "base"},
            }
        )
    )
    assert isinstance(robot, Robot)
    assert robot.identity.id == "alpha"
    assert robot.identity.name == "Alpha"


def test_builder_handles_missing_identity() -> None:
    robot = build_robot_from_config(_cfg({"kernel": {"name": "base"}}))
    assert robot.identity.id is None
    assert robot.identity.name is None


def test_builder_loads_control_plane_token_from_workspace_env(
    tmp_path: Path,
) -> None:
    """If a workspace is given, the builder reads the .env for the token."""
    (tmp_path / ".env").write_text(
        "CEPHIX_CONTROL_PLANE_TOKEN=secret-token-xyz\n",
        encoding="utf-8",
    )
    robot = build_robot_from_config(
        _cfg({"id": "x", "kernel": {"name": "base"}}),
        workspace=tmp_path,
    )
    assert robot._control_plane_token == "secret-token-xyz"  # type: ignore[attr-defined]


def test_builder_skips_observers_without_workspace() -> None:
    """Without a workspace the JSONL provider has nowhere to anchor;
    telemetry and audit are silently skipped."""
    robot = build_robot_from_config(_cfg({"kernel": {"name": "base"}}))
    assert not any(isinstance(c, BusRecorder) for c in robot.components)
    assert not any(isinstance(c, AuditNoteSink) for c in robot.components)


def test_builder_wires_telemetry_and_audit_via_persistence(
    tmp_path: Path,
) -> None:
    """With a workspace, the central persistence layer is built and
    used to wire telemetry+audit. The default channels resolve to
    ``<workspace>/logs/telemetry.jsonl`` and
    ``<workspace>/logs/audit.jsonl``."""
    robot = build_robot_from_config(
        _cfg({"kernel": {"name": "base"}}),
        workspace=tmp_path,
    )
    recorders = [c for c in robot.components if isinstance(c, BusRecorder)]
    sinks = [c for c in robot.components if isinstance(c, AuditNoteSink)]
    assert len(recorders) == 1
    assert len(sinks) == 1
    recorder_sink_path = recorders[0]._sink._path  # type: ignore[attr-defined]
    audit_sink_path = sinks[0]._sink._path  # type: ignore[attr-defined]
    assert recorder_sink_path == tmp_path / "logs" / "telemetry.jsonl"
    assert audit_sink_path == tmp_path / "logs" / "audit.jsonl"


def test_builder_persistence_disabled_skips_all_observers(
    tmp_path: Path,
) -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"name": "base"},
                "persistence": {"enabled": False},
            }
        ),
        workspace=tmp_path,
    )
    assert not any(isinstance(c, BusRecorder) for c in robot.components)
    assert not any(isinstance(c, AuditNoteSink) for c in robot.components)


def test_builder_observer_disabled_keeps_other_observer(
    tmp_path: Path,
) -> None:
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"name": "base"},
                "telemetry": {"enabled": False},
            }
        ),
        workspace=tmp_path,
    )
    assert not any(isinstance(c, BusRecorder) for c in robot.components)
    assert any(isinstance(c, AuditNoteSink) for c in robot.components)


def test_builder_uses_explicit_channel_names(tmp_path: Path) -> None:
    """An overridden channel routes the sink to a custom path."""
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"name": "base"},
                "telemetry": {"channel": "raw-events"},
                "audit": {"channel": "narrative"},
            }
        ),
        workspace=tmp_path,
    )
    recorder = next(c for c in robot.components if isinstance(c, BusRecorder))
    audit = next(c for c in robot.components if isinstance(c, AuditNoteSink))
    assert recorder._sink._path == tmp_path / "logs" / "raw-events.jsonl"  # type: ignore[attr-defined]
    assert audit._sink._path == tmp_path / "logs" / "narrative.jsonl"  # type: ignore[attr-defined]


def test_builder_persistence_absolute_path_wins(tmp_path: Path) -> None:
    """An absolute persistence path is used as-is, regardless of workspace."""
    abs_root = tmp_path / "elsewhere"
    robot = build_robot_from_config(
        _cfg(
            {
                "kernel": {"name": "base"},
                "persistence": {"path": str(abs_root)},
            }
        ),
        workspace=tmp_path / "ws",
    )
    recorder = next(c for c in robot.components if isinstance(c, BusRecorder))
    assert recorder._sink._path == abs_root / "telemetry.jsonl"  # type: ignore[attr-defined]


def test_builder_rejects_unknown_persistence_type(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="persistence backend"):
        build_robot_from_config(
            _cfg(
                {
                    "kernel": {"name": "base"},
                    "persistence": {"name": "redis"},
                }
            ),
            workspace=tmp_path,
        )


def test_builder_control_plane_config_overrides() -> None:
    robot = build_robot_from_config(
        {
            "kernel": {"name": "base"},
            "actor": {"name": "echo"},
            "control_plane": {
                "enabled": False,
                "host": "127.0.0.1",
                "port": 12345,
                "port_range": [12345, 12399],
                "path": "/admin",
            },
        }
    )
    cfg = robot._control_plane_config  # type: ignore[attr-defined]
    assert cfg.enabled is False
    assert cfg.port == 12345
    assert cfg.port_range == (12345, 12399)
    assert cfg.path == "/admin"


def test_builder_rejects_bad_port_range() -> None:
    with pytest.raises(ConfigError, match="port_range"):
        build_robot_from_config(
            {
                "kernel": {"name": "base"},
                "actor": {"name": "echo"},
                "control_plane": {"port_range": [9999, 1000]},
            }
        )


# ---------------------------------------------------------------------------
# LLM stack: utility + actor with catalog injection
# ---------------------------------------------------------------------------


def test_builder_assembles_utility_list() -> None:
    """``utility:`` builds UTILITY-tier components into the robot."""
    from src.utility.model_catalog import ModelCatalog

    robot = build_robot_from_config(
        _cfg(
            {
                "utility": [{"name": "model-catalog"}],
                "actor": {"name": "echo"},
                "kernel": {"name": "base"},
            }
        )
    )
    catalogs = [c for c in robot.components if isinstance(c, ModelCatalog)]
    assert len(catalogs) == 1


def test_builder_rejects_utility_section_with_wrong_category() -> None:
    """A non-UTILITY component listed under ``utility:`` must error."""
    with pytest.raises(ConfigError, match="expected utility"):
        build_robot_from_config(
            _cfg(
                {
                    "utility": [{"name": "echo"}],
                    "kernel": {"name": "base"},
                }
            )
        )


def test_builder_injects_catalog_into_mock_llm_actor() -> None:
    """When a ModelCatalog utility is present, MockLLMActor gets it."""
    from src.actor.llm.mock_actor import MockLLMActor
    from src.utility.model_catalog import ModelCatalog

    robot = build_robot_from_config(
        _cfg(
            {
                "utility": [{"name": "model-catalog"}],
                "actor": {
                    "name": "llm.mock",
                    "model_id": "mock-echo",
                    "provider": "mock",
                },
                "kernel": {"name": "base"},
            }
        )
    )
    actor = next(c for c in robot.components if isinstance(c, MockLLMActor))
    catalog = next(c for c in robot.components if isinstance(c, ModelCatalog))
    assert actor._catalog is catalog  # type: ignore[attr-defined]


def test_builder_skips_catalog_injection_for_actors_that_dont_take_it() -> None:
    """An EchoActor has no ``catalog`` kwarg; injection must be silent."""
    from src.actor.echo import EchoActor

    robot = build_robot_from_config(
        _cfg(
            {
                "utility": [{"name": "model-catalog"}],
                "actor": {"name": "echo"},
                "kernel": {"name": "base"},
            }
        )
    )
    # No crash + actor still constructed.
    assert any(isinstance(c, EchoActor) for c in robot.components)


def test_builder_explicit_actor_catalog_kwarg_wins_over_default_injection() -> None:
    """A user-provided ``catalog: null`` opts out of injection.

    Achievable today only by setting ``catalog: null`` -- proves the
    builder leaves explicit values alone.
    """
    from src.actor.llm.mock_actor import MockLLMActor

    robot = build_robot_from_config(
        _cfg(
            {
                "utility": [{"name": "model-catalog"}],
                "actor": {
                    "name": "llm.mock",
                    "catalog": None,  # opt out
                },
                "kernel": {"name": "base"},
            }
        )
    )
    actor = next(c for c in robot.components if isinstance(c, MockLLMActor))
    assert actor._catalog is None  # type: ignore[attr-defined]


def test_builder_utility_boots_before_actor() -> None:
    """Robot.components is sorted by BOOT_PRIORITY; utility before actor."""
    from src.actor.llm.mock_actor import MockLLMActor
    from src.utility.model_catalog import ModelCatalog

    robot = build_robot_from_config(
        _cfg(
            {
                "utility": [{"name": "model-catalog"}],
                "actor": {"name": "llm.mock"},
                "kernel": {"name": "base"},
            }
        )
    )
    by_index = {type(c).__name__: i for i, c in enumerate(robot.components)}
    assert by_index[ModelCatalog.__name__] < by_index[MockLLMActor.__name__]


def test_builder_mock_llm_actor_works_without_catalog() -> None:
    """No utility section -> actor still constructs; catalog is None."""
    from src.actor.llm.mock_actor import MockLLMActor

    robot = build_robot_from_config(
        _cfg(
            {
                "actor": {"name": "llm.mock"},
                "kernel": {"name": "base"},
            }
        )
    )
    actor = next(c for c in robot.components if isinstance(c, MockLLMActor))
    assert actor._catalog is None  # type: ignore[attr-defined]


def test_builder_bus_utility_section_empty_today_but_accepted() -> None:
    """The ``bus_utility`` section is documented and parsed.

    No built-in BUS_UTILITY component ships today, so an empty list
    is the only valid value. Verifies the section is recognised and
    the schema is forward-compatible.
    """
    robot = build_robot_from_config(
        _cfg(
            {
                "bus_utility": [],
                "actor": {"name": "echo"},
                "kernel": {"name": "base"},
            }
        )
    )
    assert isinstance(robot, Robot)


# ---------------------------------------------------------------------------
# Credential subsystem: substitution, fail-fast, provider injection
# ---------------------------------------------------------------------------


class TestBuilderCredentials:
    """Substitution, default store chain, and CredentialProvider injection."""

    def test_default_chain_includes_credential_provider(
        self, tmp_path: Path
    ) -> None:
        """Even without a ``credentials:`` section, a provider is built."""
        from src.credentials.provider import CredentialProvider

        robot = build_robot_from_config(
            _cfg({"kernel": {"name": "base"}}),
            workspace=tmp_path,
        )
        providers = [
            c for c in robot.components if isinstance(c, CredentialProvider)
        ]
        assert len(providers) == 1

    def test_substitutes_secret_in_actor_block(self, tmp_path: Path) -> None:
        """A ``${KEY}`` reference in actor.api_key is resolved before construction."""
        (tmp_path / ".env").write_text(
            "OPENAI_KEY=sk-substituted\n", encoding="utf-8"
        )
        from src.actor.llm.openai_actor import LLMActorOpenAI

        robot = build_robot_from_config(
            _cfg(
                {
                    "actor": {
                        "name": "llm.openai",
                        "model_id": "gpt-4o-mini",
                        "api_key": "${OPENAI_KEY}",
                    },
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        actor = next(
            c for c in robot.components if isinstance(c, LLMActorOpenAI)
        )
        assert actor._api_key == "sk-substituted"  # type: ignore[attr-defined]

    def test_missing_secret_raises_and_aborts_build(
        self, tmp_path: Path
    ) -> None:
        """A reference that no store can resolve aborts with CredentialNotFound."""
        from src.credentials.exceptions import CredentialNotFound

        with pytest.raises(CredentialNotFound) as excinfo:
            build_robot_from_config(
                _cfg(
                    {
                        "actor": {
                            "name": "llm.openai",
                            "model_id": "gpt-4o-mini",
                            "api_key": "${MISSING_KEY}",
                        },
                        "kernel": {"name": "base"},
                    }
                ),
                workspace=tmp_path,
            )
        assert excinfo.value.key == "MISSING_KEY"
        assert excinfo.value.requester == "builder"

    def test_substitutes_in_nested_structures(self, tmp_path: Path) -> None:
        """Substitution walks lists and nested dicts."""
        (tmp_path / ".env").write_text(
            "OPENAI_KEY=sk-deep\nHOST=api.openai.com\n", encoding="utf-8"
        )
        from src.actor.llm.openai_actor import LLMActorOpenAI

        robot = build_robot_from_config(
            _cfg(
                {
                    "actor": {
                        "name": "llm.openai",
                        "model_id": "gpt-4o-mini",
                        "api_key": "${OPENAI_KEY}",
                        "base_url": "https://${HOST}/v1",
                    },
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        actor = next(
            c for c in robot.components if isinstance(c, LLMActorOpenAI)
        )
        assert actor._api_key == "sk-deep"  # type: ignore[attr-defined]
        assert actor._base_url == "https://api.openai.com/v1"  # type: ignore[attr-defined]

    def test_explicit_credentials_section_replaces_default_chain(
        self, tmp_path: Path
    ) -> None:
        """``credentials.stores`` overrides the default chain."""
        store_file = tmp_path / "custom.env"
        store_file.write_text("OPENAI_KEY=sk-custom\n", encoding="utf-8")
        # Default chain would also work because tmp_path contains no .env;
        # the test is about *replacement*. A bot-local .env that holds a
        # different value would shadow the explicit store; verify it does
        # NOT, because the explicit list replaces the default chain.
        (tmp_path / ".env").write_text(
            "OPENAI_KEY=should-not-win\n", encoding="utf-8"
        )
        from src.actor.llm.openai_actor import LLMActorOpenAI

        robot = build_robot_from_config(
            _cfg(
                {
                    "credentials": {
                        "stores": [
                            {
                                "type": "env",
                                "path": str(store_file),
                                "name": "explicit",
                            }
                        ]
                    },
                    "actor": {
                        "name": "llm.openai",
                        "model_id": "gpt-4o-mini",
                        "api_key": "${OPENAI_KEY}",
                    },
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        actor = next(
            c for c in robot.components if isinstance(c, LLMActorOpenAI)
        )
        assert actor._api_key == "sk-custom"  # type: ignore[attr-defined]

    def test_first_store_wins(self, tmp_path: Path) -> None:
        """Resolution order: first store with the key wins."""
        first = tmp_path / "first.env"
        first.write_text("OPENAI_KEY=from-first\n", encoding="utf-8")
        second = tmp_path / "second.env"
        second.write_text("OPENAI_KEY=from-second\n", encoding="utf-8")
        from src.actor.llm.openai_actor import LLMActorOpenAI

        robot = build_robot_from_config(
            _cfg(
                {
                    "credentials": {
                        "stores": [
                            {"type": "env", "path": str(first)},
                            {"type": "env", "path": str(second)},
                        ]
                    },
                    "actor": {
                        "name": "llm.openai",
                        "model_id": "gpt-4o-mini",
                        "api_key": "${OPENAI_KEY}",
                    },
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        actor = next(
            c for c in robot.components if isinstance(c, LLMActorOpenAI)
        )
        assert actor._api_key == "from-first"  # type: ignore[attr-defined]

    def test_dollar_dollar_escape_produces_literal(
        self, tmp_path: Path
    ) -> None:
        """``$$`` collapses to a literal ``$`` and avoids substitution."""
        from src.actor.echo import EchoActor

        robot = build_robot_from_config(
            _cfg(
                {
                    "actor": {
                        "name": "echo",
                        "prefix": "$${OPENAI_KEY}: ",
                    },
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        actor = next(c for c in robot.components if isinstance(c, EchoActor))
        assert actor._prefix == "${OPENAI_KEY}: "  # type: ignore[attr-defined]

    def test_lowercase_keys_are_not_substituted(
        self, tmp_path: Path
    ) -> None:
        """``${log.level}``-style strings are not credential references."""
        from src.actor.echo import EchoActor

        robot = build_robot_from_config(
            _cfg(
                {
                    "actor": {
                        "name": "echo",
                        "prefix": "${log.level}",
                    },
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        actor = next(c for c in robot.components if isinstance(c, EchoActor))
        assert actor._prefix == "${log.level}"  # type: ignore[attr-defined]

    def test_credential_provider_is_injected_into_compatible_actor(
        self, tmp_path: Path
    ) -> None:
        """An actor whose constructor takes ``credentials`` gets the provider."""
        from src.credentials.provider import CredentialProvider

        # Custom actor class to verify the injection convention without
        # needing a real LLM. Lives only in this test.
        from src.actor.ports import ActorPort
        from src.actor.types import ActorResponse
        from src.components import ComponentCategory
        from src.registry import register

        class CredentialProbingActor(ActorPort):
            component_name = "test.credential-probe"
            component_category = ComponentCategory.ACTOR
            component_description = "test-only"

            def __init__(self, *, credentials: CredentialProvider) -> None:
                self.credentials = credentials

            async def run(self, actor_context):  # type: ignore[override]
                return ActorResponse(message=None, status="ok")

        try:
            register(CredentialProbingActor)
        except Exception:  # noqa: BLE001 -- already registered
            pass

        robot = build_robot_from_config(
            _cfg(
                {
                    "actor": {"name": "test.credential-probe"},
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        probe = next(
            c for c in robot.components if isinstance(c, CredentialProbingActor)
        )
        provider = next(
            c for c in robot.components if isinstance(c, CredentialProvider)
        )
        assert probe.credentials is provider

    def test_rejects_unknown_store_type(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="unknown type"):
            build_robot_from_config(
                _cfg(
                    {
                        "credentials": {
                            "stores": [{"type": "vault"}]
                        },
                        "actor": {"name": "echo"},
                        "kernel": {"name": "base"},
                    }
                ),
                workspace=tmp_path,
            )

    def test_rejects_env_store_without_path(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="path"):
            build_robot_from_config(
                _cfg(
                    {
                        "credentials": {
                            "stores": [{"type": "env"}]
                        },
                        "actor": {"name": "echo"},
                        "kernel": {"name": "base"},
                    }
                ),
                workspace=tmp_path,
            )

    def test_credentials_section_is_not_substituted(
        self, tmp_path: Path
    ) -> None:
        """The ``credentials:`` block itself is consumed before substitution.

        Otherwise we'd hit a chicken-and-egg: substituting ``${X}``
        inside the very block that defines how to look up ``X``.
        Verifies the builder pops ``credentials:`` *before* the
        substitution pass.
        """
        from src.actor.echo import EchoActor

        # The ``credentials.stores[0].path`` value contains a literal
        # ${...}-looking string. If substitution ran on the
        # credentials block, it would explode here. Path resolution
        # uses it verbatim; the file doesn't need to exist.
        robot = build_robot_from_config(
            _cfg(
                {
                    "credentials": {
                        "stores": [
                            {
                                "type": "env",
                                "path": str(tmp_path / "nope-${UNRESOLVED}.env"),
                            }
                        ]
                    },
                    "actor": {"name": "echo"},
                    "kernel": {"name": "base"},
                }
            ),
            workspace=tmp_path,
        )
        # Build succeeded; the literal ${UNRESOLVED} was tolerated as
        # a path component (the file is missing, which is fine).
        assert any(isinstance(c, EchoActor) for c in robot.components)
