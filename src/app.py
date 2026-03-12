from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from src.bus import SemanticBus
from src.configuration import load_global_secret_candidates, onboard_robot_instance, resolve_robot_instance
from src.control import InMemoryPairingRegistry, RobotControlPlane
from src.context import DefaultContextAssembler, FirmwareHeartbeat, MarkdownFirmwareStore, MarkdownMemoryDocumentStore
from src.domain import ExecutionContext, MessageRecord, ReplyTarget, RobotEvent
from src.gateways import ChannelHub, TelegramChannel, WebSocketChannel
from src.governance.composite import CompositeToolExecutionGuard
from src.memory import InMemoryMemoryStore, PersistentMemoryStore
from src.llm import StubLLMProvider, create_llm_provider
from src.llm.ports import LLMPort
from src.planners import LLMPlanner
from src.robot import DigitalRobot
from src.runtime import RuntimeEventLoop
from src.service import RobotService
from src.telemetry import EventLog, FanoutEventSink, Telemetry
from src.tools.collector import ToolCollector
from src.tools.executor import GovernedToolExecutor
from src.tools.models import ToolDefinition, ToolParameter
from src.tools.registry import InMemoryToolRegistry
from src.tools.system_tools import ALL_SYSTEM_TOOLS, SystemToolDriver
from src.utils import new_id
from typing import Any


class _NullEgress:
    """No-op egress port that discards all messages. Used in headless kernels."""

    def send(self, target: ReplyTarget, message: Any) -> None:
        pass

    def send_chunk(self, target: ReplyTarget, token: str) -> None:
        pass

    def send_chunk_clear(self, target: ReplyTarget) -> None:
        pass


class InlineToolDriver:
    """Minimal tool driver for inline/demo tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Any] = {}

    def register(
        self,
        definition: ToolDefinition,
        handler: Any,
    ) -> None:
        self._tools[definition.name] = definition
        self._handlers[definition.name] = handler

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise RuntimeError(f"InlineToolDriver has no handler for: {tool_name!r}")
        return handler(ctx, arguments)


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


def _build_demo_drivers(
    memory: InMemoryMemoryStore | PersistentMemoryStore,
    memory_dir: str | Path | None = None,
) -> list[Any]:
    """Build the list of ToolDriverPort implementations for the demo robot."""
    # System tools (memory, documents, procedures)
    system_driver = SystemToolDriver(memory=memory, memory_dir=memory_dir)

    # Domain tools (demo mail)
    domain_driver = InlineToolDriver()
    domain_driver.register(
        ToolDefinition(
            name="mail.list_new_messages",
            description="List new/unread messages from the inbox",
            parameters=[
                ToolParameter(name="limit", type="integer", description="Max messages to return", required=False),
            ],
        ),
        _mail_list_handler,
    )

    return [system_driver, domain_driver]


def _build_tool_stack(
    drivers: list[Any],
) -> tuple[GovernedToolExecutor, InMemoryToolRegistry, ToolCollector]:
    """Wire up Collector → Registry → GovernedToolExecutor from drivers."""
    collector = ToolCollector(drivers)
    registry = InMemoryToolRegistry(collector)
    guard = CompositeToolExecutionGuard()
    executor = GovernedToolExecutor(registry=registry, guard=guard, collector=collector)
    return executor, registry, collector


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
    drivers = _build_demo_drivers(memory_store)
    tool_executor, registry, collector = _build_tool_stack(drivers)
    context_assembler = DefaultContextAssembler(
        firmware=firmware,
        memory_documents=memory_documents,
        memory_store=memory_store,
        tool_registry=registry,
        tool_catalog=collector,
        system_tool_definitions=ALL_SYSTEM_TOOLS,
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


def build_kernel_for_instance(
    *,
    home_dir: str | Path,
    robot_id: str = "main",
    robot_name: str | None = None,
    event_log_path: str | Path | None = None,
    llm: LLMPort | None = None,
    default_output_target: ReplyTarget | None = None,
) -> tuple["DigitalRobotKernel", SemanticBus, PersistentMemoryStore]:
    """Build a fully wired kernel from an onboarded robot instance.

    This uses the real firmware, memory documents, and tool wiring from
    the instance workspace — no WebSocket or service layer overhead.
    Ideal for integration tests that exercise the full context-assembly
    and planning pipeline.
    """
    from src.runtime.kernel import DigitalRobotKernel

    instance = resolve_robot_instance(
        robot_id=robot_id,
        robot_name=robot_name,
        home_override=home_dir,
    )

    # Auto-resolve LLM from robot.yaml if not explicitly provided
    if llm is None:
        from src.configuration import _load_yaml, read_secret, robot_config_path
        robot_cfg_path = robot_config_path(instance.paths.workspace_dir)
        if robot_cfg_path.exists():
            robot_cfg = _load_yaml(robot_cfg_path)
            llm = create_llm_provider(
                robot_cfg,
                secret_resolver=lambda key: read_secret(
                    key,
                    instance.paths.instance_env_path,
                    global_fallback=instance.paths.global_env_path,
                ),
            )

    log_path = Path(event_log_path) if event_log_path else instance.paths.logs_dir / "events.jsonl"
    event_log = EventLog(str(log_path))
    bus = SemanticBus()
    firmware = MarkdownFirmwareStore(instance.paths.firmware_dir)
    memory_store = PersistentMemoryStore(instance.paths.workspace_dir / "memory_data")
    memory_documents = MarkdownMemoryDocumentStore(instance.paths.memory_dir)
    drivers = _build_demo_drivers(memory_store, memory_dir=instance.paths.memory_dir)
    tool_executor, registry, collector = _build_tool_stack(drivers)
    context_assembler = DefaultContextAssembler(
        firmware=firmware,
        memory_documents=memory_documents,
        memory_store=memory_store,
        tool_registry=registry,
        tool_catalog=collector,
        system_tool_definitions=ALL_SYSTEM_TOOLS,
    )
    # Register a sink for each channel referenced by the default output target.
    egress: dict[str, Any] = {}
    if default_output_target is not None:
        egress[default_output_target.channel] = _NullEgress()
    channel_hub = ChannelHub(ingress_ports=[], egress_ports=egress)

    kernel = DigitalRobotKernel(
        robot_id=instance.robot_id,
        default_output_target=default_output_target,
        message_delivery=channel_hub,
        tool_executor=tool_executor,
        context_assembler=context_assembler,
        planner=LLMPlanner(llm=llm),
        memory=memory_store,
        telemetry=Telemetry(event_log),
        bus=bus,
    )
    return kernel, bus, memory_store


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
    llm: LLMPort | None = None,
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
    memory_store = PersistentMemoryStore(instance.paths.workspace_dir / "memory_data")
    memory_documents = MarkdownMemoryDocumentStore(instance.paths.memory_dir)
    default_output_target = None
    drivers = _build_demo_drivers(memory_store, memory_dir=instance.paths.memory_dir)
    tool_executor, registry, collector = _build_tool_stack(drivers)

    # Resolve LLM provider from robot.yaml if not explicitly provided
    if llm is None:
        from src.configuration import _load_yaml, read_secret, robot_config_path
        robot_cfg_path = robot_config_path(instance.paths.workspace_dir)
        if robot_cfg_path.exists():
            robot_cfg = _load_yaml(robot_cfg_path)
            llm = create_llm_provider(
                robot_cfg,
                secret_resolver=lambda key: read_secret(
                    key,
                    instance.paths.instance_env_path,
                    global_fallback=instance.paths.global_env_path,
                ),
            )
    context_assembler = DefaultContextAssembler(
        firmware=firmware,
        memory_documents=memory_documents,
        memory_store=memory_store,
        tool_registry=registry,
        tool_catalog=collector,
        system_tool_definitions=ALL_SYSTEM_TOOLS,
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
        planner=LLMPlanner(llm=llm),
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
    def _onboarding_status() -> dict[str, object]:
        from src.configuration import read_secret, _KNOWN_API_KEY_VARS
        global_path = instance.paths.global_env_path
        instance_path = instance.paths.instance_env_path

        # Report which LLM API keys are already available (masked).
        llm_keys: dict[str, str] = {}
        for key_var in _KNOWN_API_KEY_VARS:
            value = read_secret(key_var, instance_path, global_fallback=global_path)
            if value:
                masked = value[:4] + "..." + value[-4:] if len(value) > 12 else "****"
                llm_keys[key_var] = masked

        return {
            "access_token_env": instance.access_token_env,
            "admin_token_env": instance.admin_token_env,
            "global_secret_candidates": load_global_secret_candidates(
                home_dir,
                instance.access_token_env,
                instance.admin_token_env,
            ),
            "llm_keys_available": llm_keys,
        }

    robot.set_onboarding_status_provider(_onboarding_status)

    def _onboard(payload: dict[str, object]) -> dict[str, object]:
        # Identity (robot_id, robot_name) is fixed at init time.
        # Onboarding only configures runtime aspects: LLM, tokens, etc.
        requested_access_token = str(payload.get("access_token") or "")
        requested_admin_token = str(payload.get("admin_token") or "")
        llm_payload = dict(payload.get("llm") or {}) if isinstance(payload.get("llm"), dict) else {}
        applied = onboard_robot_instance(
            robot_id=instance.robot_id,
            robot_name=instance.robot_name,
            home_override=home_dir,
            bind_override=instance.bind,
            port_override=ws_channel.bound_port or instance.port,
            respect_port_override=True,
            access_token=requested_access_token,
            admin_token=requested_admin_token,
            auto_approve_loopback=instance.auto_approve_loopback,
            poll_interval_seconds=instance.poll_interval_seconds,
            llm_config=llm_payload or None,
            workspace_override=instance.paths.workspace_dir,
        )
        ws_channel.update_auth_config(
            access_token=applied.access_token,
            admin_token=applied.admin_token,
            auto_approve_loopback=applied.auto_approve_loopback,
        )
        robot.set_onboarded(applied.onboarded)

        # Hot-reload LLM provider from the freshly written robot.yaml.
        from src.configuration import _load_yaml, read_secret, robot_config_path
        fresh_cfg_path = robot_config_path(applied.paths.workspace_dir)
        if fresh_cfg_path.exists():
            fresh_cfg = _load_yaml(fresh_cfg_path)
            new_llm = create_llm_provider(
                fresh_cfg,
                secret_resolver=lambda key: read_secret(
                    key,
                    applied.paths.instance_env_path,
                    global_fallback=applied.paths.global_env_path,
                ),
            )
            robot.kernel.planner._llm = new_llm
            if new_llm is None:
                logger.warning("LLM provider is None after onboarding — keyword fallback active")
            else:
                logger.info("LLM provider hot-reloaded: %s", type(new_llm).__name__)

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
