from __future__ import annotations

from pathlib import Path

from src.bus import SemanticBus
from src.configuration import copy_secret, global_env_path, has_secret, load_global_secret_candidates, onboard_robot_instance, resolve_robot_instance
from src.control import InMemoryPairingRegistry, RobotControlPlane
from src.context import DefaultContextAssembler, FirmwareHeartbeat, MarkdownFirmwareStore, MarkdownMemoryDocumentStore
from src.domain import ExecutionContext, MessageRecord, ReplyTarget, RobotEvent
from src.gateways import ChannelHub, TelegramChannel, WebSocketChannel
from src.governance.composite import CompositeToolExecutionGuard
from src.memory import InMemoryMemoryStore
from src.planners import LLMPlanner
from src.robot import DigitalRobot
from src.runtime import RuntimeEventLoop
from src.service import RobotService
from src.telemetry import EventLog, FanoutEventSink, Telemetry
from src.tools.executor import GovernedToolExecutor
from src.tools.models import ToolDefinition, ToolParameter
from src.tools.registry import InMemoryToolRegistry
from src.utils import new_id
from typing import Any


class _InlineCatalog:
    """Minimal in-memory catalog for demo purposes."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._defs = {d.name: d for d in definitions}

    def list_available(self, *, tags: list[str] | None = None) -> list[ToolDefinition]:
        return list(self._defs.values())

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        return self._defs.get(tool_name)


_DEMO_MESSAGES = [
    MessageRecord(
        message_id="m1",
        sender="Anna Becker <anna@example.com>",
        subject="Termin am Dienstag bestaetigt",
        body="Hallo, der Termin am Dienstag um 10 Uhr passt. Bitte bring die letzten Unterlagen mit.",
        received_at="2026-03-08T08:10:00Z",
        unread=True,
    ),
    MessageRecord(
        message_id="m2",
        sender="Buchhaltung <finance@example.com>",
        subject="Offene Rechnung 2026-041",
        body="Die Rechnung 2026-041 ist noch offen. Bitte pruefe den Zahlungseingang bis Ende der Woche.",
        received_at="2026-03-08T09:05:00Z",
        unread=True,
    ),
    MessageRecord(
        message_id="m3",
        sender="Newsletter <news@example.com>",
        subject="Neue Angebote im Maerz",
        body="Diesen Monat gibt es neue Angebote. Ausserdem wurden neue Kategorien ergaenzt.",
        received_at="2026-03-08T09:30:00Z",
        unread=True,
    ),
]


def _mail_list_handler(ctx: ExecutionContext, arguments: dict[str, Any]) -> list[MessageRecord]:
    limit = int(arguments.get("limit", 10))
    return [m for m in _DEMO_MESSAGES if m.unread][:limit]


def _build_demo_tool_executor() -> GovernedToolExecutor:
    catalog = _InlineCatalog([
        ToolDefinition(
            name="mail.list_new_messages",
            description="List new/unread messages from the inbox",
            parameters=[
                ToolParameter(name="limit", type="integer", description="Max messages to return", required=False),
            ],
        ),
    ])
    registry = InMemoryToolRegistry(catalog)
    registry.mount("mail.list_new_messages")
    guard = CompositeToolExecutionGuard()
    executor = GovernedToolExecutor(registry=registry, guard=guard)
    executor.register_handler("mail.list_new_messages", _mail_list_handler)
    return executor


def build_demo_robot(event_log_path: str | Path = "robot_events.jsonl") -> DigitalRobot:
    event_log = EventLog(str(event_log_path))
    telemetry = Telemetry(event_log)
    bus = SemanticBus()
    telegram_channel = TelegramChannel()
    channel_hub = ChannelHub(ingress_ports=[telegram_channel], egress_ports={"telegram": telegram_channel})
    firmware = MarkdownFirmwareStore(Path("robot") / "firmware")
    memory_store = InMemoryMemoryStore()
    memory_documents = MarkdownMemoryDocumentStore(Path("robot") / "memory")
    default_output_target = ReplyTarget(
        channel="telegram",
        recipient_id="user-telegram-42",
        conversation_id="tg-conv-001",
        mode="notify",
    )
    tool_executor = _build_demo_tool_executor()
    context_assembler = DefaultContextAssembler(
        firmware=firmware,
        memory_documents=memory_documents,
        memory_store=memory_store,
    )
    heartbeat = FirmwareHeartbeat(firmware=firmware, default_output_target=default_output_target)
    pairing_registry = InMemoryPairingRegistry()

    return DigitalRobot(
        robot_id="digital-robot-001",
        robot_name="Digital Robot 001",
        default_output_target=default_output_target,
        message_delivery=channel_hub,
        tool_executor=tool_executor,
        context_assembler=context_assembler,
        planner=LLMPlanner(),
        memory=memory_store,
        telemetry=telemetry,
        bus=bus,
        firmware=firmware,
        pairings=pairing_registry,
        registered_channels_provider=lambda: sorted(channel_hub.egress_ports.keys()),
        memory_backend_name="in-memory+markdown",
        tool_execution_backend_name="governed-tool-executor",
        event_source=channel_hub,
        heartbeat=heartbeat,
        channels=[telegram_channel],
    )


def build_demo_runtime(event_log_path: str | Path = "robot_events.jsonl") -> tuple[RuntimeEventLoop, SemanticBus]:
    robot = build_demo_robot(event_log_path)
    return robot.runtime, robot.bus


def build_websocket_service(
    *,
    robot_id: str = "main",
    robot_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    event_log_path: str | Path = "robot_events.jsonl",
    access_token: str = "",
    admin_token: str = "",
    auto_approve_loopback: bool = True,
    home_dir: str | Path | None = None,
) -> RobotService:
    instance = resolve_robot_instance(
        robot_id=robot_id,
        robot_name=robot_name,
        home_override=home_dir,
        bind_override=host,
        port_override=port,
        access_token_override=access_token if access_token != "" else None,
        admin_token_override=admin_token if admin_token != "" else None,
        auto_approve_loopback_override=auto_approve_loopback,
    )
    log_path = Path(event_log_path)
    if not log_path.is_absolute():
        log_path = instance.paths.logs_dir / log_path
    event_log = EventLog(str(log_path))
    bus = SemanticBus()
    firmware = MarkdownFirmwareStore(instance.paths.firmware_dir)
    memory_store = InMemoryMemoryStore()
    memory_documents = MarkdownMemoryDocumentStore(instance.paths.memory_dir)
    default_output_target = None
    tool_executor = _build_demo_tool_executor()
    context_assembler = DefaultContextAssembler(
        firmware=firmware,
        memory_documents=memory_documents,
        memory_store=memory_store,
    )
    heartbeat = FirmwareHeartbeat(firmware=firmware, default_output_target=default_output_target)
    pairing_registry = InMemoryPairingRegistry()
    ws_channel = WebSocketChannel(
        bind=instance.bind,
        port=instance.port,
        access_token=instance.access_token,
        admin_token=instance.admin_token,
        auto_approve_loopback=instance.auto_approve_loopback,
        pairings=pairing_registry,
    )
    telemetry = Telemetry(FanoutEventSink([event_log, ws_channel]))
    channel_hub = ChannelHub(ingress_ports=[ws_channel], egress_ports={"ws": ws_channel})
    robot = DigitalRobot(
        robot_id=instance.robot_id,
        robot_name=instance.robot_name,
        default_output_target=default_output_target,
        message_delivery=channel_hub,
        tool_executor=tool_executor,
        context_assembler=context_assembler,
        planner=LLMPlanner(),
        memory=memory_store,
        telemetry=telemetry,
        bus=bus,
        firmware=firmware,
        pairings=pairing_registry,
        registered_channels_provider=lambda: sorted(channel_hub.egress_ports.keys()),
        memory_backend_name="in-memory+markdown",
        tool_execution_backend_name="governed-tool-executor",
        event_source=channel_hub,
        heartbeat=heartbeat,
        channels=[ws_channel],
        _control_plane_factory=lambda **kwargs: RobotControlPlane(
            **kwargs,
            home_path=str(instance.paths.home_dir),
            global_env_path=str(instance.paths.global_env_path),
            instance_env_path=str(instance.paths.instance_env_path),
            home_config_path=str(instance.paths.home_config_path),
            robot_config_path=str(instance.paths.robot_config_path),
            workspace_path=str(instance.paths.workspace_dir),
            logs_path=str(instance.paths.logs_dir),
            sessions_path=str(instance.paths.sessions_dir),
            bind=instance.bind,
            port=instance.port,
        ),
    )
    robot.set_onboarded(instance.onboarded)
    robot.set_onboarding_status_provider(
        lambda: {
            "access_token_env": instance.access_token_env,
            "admin_token_env": instance.admin_token_env,
            "global_secret_candidates": load_global_secret_candidates(
                home_dir,
                instance.access_token_env,
                instance.admin_token_env,
            ),
        }
    )

    def _onboard(payload: dict[str, object]) -> dict[str, object]:
        copy_global_access_token = bool(payload.get("copy_global_access_token"))
        copy_global_admin_token = bool(payload.get("copy_global_admin_token"))
        requested_access_token = str(payload.get("access_token") or "")
        requested_admin_token = str(payload.get("admin_token") or "")
        applied = onboard_robot_instance(
            robot_id=robot.robot_id,
            robot_name=str(payload.get("robot_name") or robot.robot_name),
            home_override=home_dir,
            bind_override=instance.bind,
            port_override=ws_channel.bound_port or instance.port,
            respect_port_override=True,
            access_token=requested_access_token,
            admin_token=requested_admin_token,
            auto_approve_loopback=instance.auto_approve_loopback,
            poll_interval_seconds=instance.poll_interval_seconds,
        )
        if copy_global_access_token and not requested_access_token:
            copy_secret(
                applied.access_token_env,
                source=global_env_path(home_dir),
                target=applied.paths.instance_env_path,
            )
            applied = resolve_robot_instance(
                robot_id=applied.robot_id,
                robot_name=applied.robot_name,
                home_override=home_dir,
                bind_override=instance.bind,
                port_override=ws_channel.bound_port or instance.port,
                admin_token_override=requested_admin_token if requested_admin_token else None,
                auto_approve_loopback_override=instance.auto_approve_loopback,
                poll_interval_override=instance.poll_interval_seconds,
            )
        if copy_global_admin_token and not requested_admin_token:
            copy_secret(
                applied.admin_token_env,
                source=global_env_path(home_dir),
                target=applied.paths.instance_env_path,
            )
            applied = resolve_robot_instance(
                robot_id=applied.robot_id,
                robot_name=applied.robot_name,
                home_override=home_dir,
                bind_override=instance.bind,
                port_override=ws_channel.bound_port or instance.port,
                access_token_override=applied.access_token if applied.access_token else None,
                auto_approve_loopback_override=instance.auto_approve_loopback,
                poll_interval_override=instance.poll_interval_seconds,
            )
        ws_channel.update_auth_config(
            access_token=applied.access_token,
            admin_token=applied.admin_token,
            auto_approve_loopback=applied.auto_approve_loopback,
        )
        robot.robot_name = applied.robot_name
        robot.control_plane.robot_name = applied.robot_name
        robot.set_onboarded(applied.onboarded)
        return {
            "onboarded": applied.onboarded,
            "robot_id": applied.robot_id,
            "robot_name": applied.robot_name,
            "home_config_path": str(applied.paths.home_config_path),
            "robot_config_path": str(applied.paths.robot_config_path),
            "global_env_path": str(applied.paths.global_env_path),
            "instance_env_path": str(applied.paths.instance_env_path),
            "workspace_path": str(applied.paths.workspace_dir),
            "access_token_env": applied.access_token_env,
            "admin_token_env": applied.admin_token_env,
            "adopted_from_global": {
                "access_token": copy_global_access_token and has_secret(applied.access_token_env, applied.paths.instance_env_path),
                "admin_token": copy_global_admin_token and has_secret(applied.admin_token_env, applied.paths.instance_env_path),
            },
        }

    robot.set_onboarding_handler(_onboard)
    return RobotService(robot=robot, poll_interval_seconds=instance.poll_interval_seconds)


def main() -> None:
    event_log_path = Path("robot_events.jsonl")
    if event_log_path.exists():
        event_log_path.unlink()

    runtime, bus = build_demo_runtime(event_log_path)

    scheduled_event = RobotEvent(
        event_id=new_id("evt"),
        event_type="cron.fired",
        source_channel="cron",
        sender_id="user-42",
        sender_name="Danny",
        conversation_id="ops-inbox-summary",
        text="Pruefe den Postkorb und antworte per Telegram.",
        payload={"job": "check_inbox"},
    )

    runtime.push_event(scheduled_event)
    runtime.run_once()

    print("Bus messages:")
    for message in bus.messages:
        print(f"- {message.msg_type}: {message.name} -> {message.payload}")

    print("\nEvents stored in robot_events.jsonl")
