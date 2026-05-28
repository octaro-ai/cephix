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
# LLM stack: governance + actor + provider injection
# ---------------------------------------------------------------------------


def test_builder_assembles_llm_stack() -> None:
    """End-to-end assembly: governance + LLMActor + mock provider."""
    from src.llm.actor import LLMActor
    from src.llm.metadata_service import ModelMetadataService
    from src.llm.providers.mock import MockLLMProvider

    robot = build_robot_from_config(
        _cfg(
            {
                "governance": {"model_metadata": {"enabled": True}},
                "actor": {
                    "name": "llm",
                    "default_system_prompt": "You are helpful.",
                    "provider": {
                        "name": "mock",
                        "model_id": "echo",
                        "provider": "mock",
                    },
                },
                "kernel": {"name": "base"},
            }
        )
    )
    actor = next(c for c in robot.components if isinstance(c, LLMActor))
    metadata = next(
        c for c in robot.components if isinstance(c, ModelMetadataService)
    )
    assert metadata is not None
    assert isinstance(actor._provider, MockLLMProvider)  # type: ignore[attr-defined]
    assert actor._default_system_prompt == "You are helpful."  # type: ignore[attr-defined]
    # Catalog was injected via governance.
    assert actor._catalog is not None  # type: ignore[attr-defined]


def test_builder_llm_actor_requires_provider_spec() -> None:
    with pytest.raises(ConfigError, match="provider"):
        build_robot_from_config(
            _cfg(
                {
                    "governance": {"model_metadata": {"enabled": True}},
                    "actor": {"name": "llm"},
                    "kernel": {"name": "base"},
                }
            )
        )


def test_builder_rejects_unknown_llm_provider_name() -> None:
    with pytest.raises(ConfigError, match="unknown llm provider"):
        build_robot_from_config(
            _cfg(
                {
                    "governance": {"model_metadata": {"enabled": True}},
                    "actor": {
                        "name": "llm",
                        "provider": {"name": "made-up-name"},
                    },
                    "kernel": {"name": "base"},
                }
            )
        )


def test_builder_governance_disabled_omits_metadata_service() -> None:
    """Without governance, the metadata service is absent.

    The mock provider needs a catalog, so this configuration relies on
    a different provider (here: dotted-path for documentation). The
    builder should not error because of governance absence -- the
    error must come from the provider's own validation.
    """
    from src.llm.metadata_service import ModelMetadataService

    with pytest.raises(ConfigError, match="catalog"):
        build_robot_from_config(
            _cfg(
                {
                    "governance": {"model_metadata": {"enabled": False}},
                    "actor": {
                        "name": "llm",
                        "provider": {
                            "name": "mock",
                            "model_id": "echo",
                            "provider": "mock",
                        },
                    },
                    "kernel": {"name": "base"},
                }
            )
        )

    # And confirm the *absence* path itself: with a provider that
    # accepts catalog=None, governance off must produce a robot
    # without a ModelMetadataService.
    robot = build_robot_from_config(
        _cfg(
            {
                "actor": {"name": "echo"},
                "kernel": {"name": "base"},
            }
        )
    )
    assert not any(
        isinstance(c, ModelMetadataService) for c in robot.components
    )


def test_builder_provider_class_path_works() -> None:
    """A dotted-path provider works alongside the registered names."""
    from src.llm.actor import LLMActor
    from src.llm.providers.mock import MockLLMProvider

    robot = build_robot_from_config(
        _cfg(
            {
                "governance": {"model_metadata": {"enabled": True}},
                "actor": {
                    "name": "llm",
                    "provider": {
                        "class": "src.llm.providers.mock.MockLLMProvider",
                        "model_id": "echo",
                        "provider": "mock",
                    },
                },
                "kernel": {"name": "base"},
            }
        )
    )
    actor = next(c for c in robot.components if isinstance(c, LLMActor))
    assert isinstance(actor._provider, MockLLMProvider)  # type: ignore[attr-defined]


def test_builder_governance_metadata_service_boots_before_actor() -> None:
    """Robot.components is sorted by BOOT_PRIORITY; metadata before actor."""
    from src.llm.actor import LLMActor
    from src.llm.metadata_service import ModelMetadataService

    robot = build_robot_from_config(
        _cfg(
            {
                "governance": {"model_metadata": {"enabled": True}},
                "actor": {
                    "name": "llm",
                    "provider": {
                        "name": "mock",
                        "model_id": "echo",
                        "provider": "mock",
                    },
                },
                "kernel": {"name": "base"},
            }
        )
    )
    # Find indices
    by_index = {type(c).__name__: i for i, c in enumerate(robot.components)}
    assert (
        by_index[ModelMetadataService.__name__]
        < by_index[LLMActor.__name__]
    )
