from __future__ import annotations

from pathlib import Path

from src.domain import AutonomyLevel, PlanningContext, ReplyTarget, RobotEvent
from src.ports import ContextAssemblerPort, FirmwarePort, HeartbeatPort, MemoryDocumentPort, MemoryPort
from src.skills.ports import SkillResolverPort
from src.sop.ports import SOPResolverPort
from src.tools.ports import ToolCatalogPort, ToolRegistryPort
from src.utils import new_id


class MarkdownFirmwareStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def get_base_guidance(self) -> dict[str, str]:
        return self._read_guidance(["AGENTS.md", "POLICY.md", "CONSTITUTION.md"])

    def get_event_instruction(self, event_type: str) -> str:
        if event_type == "heartbeat.tick":
            return self._read_guidance_text("HEARTBEAT.md")
        return ""

    def _read_guidance_text(self, name: str) -> str:
        path = self.root / name
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _read_guidance(self, names: list[str]) -> dict[str, str]:
        guidance: dict[str, str] = {}
        for name in names:
            content = self._read_guidance_text(name)
            if content:
                guidance[name] = content
        return guidance


class MarkdownMemoryDocumentStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def get_documents(self, event: RobotEvent, user_id: str) -> dict[str, str]:
        names = ["BOOTSTRAP.md", "IDENTITY.md", "TOOLS.md", "DIRECTORY.md", "CORE_MEMORIES.md", "MEMORY.md"]
        if event.event_type != "heartbeat.tick" and user_id != "system":
            names.append("USER.md")

        documents: dict[str, str] = {}
        for name in names:
            path = self.root / name
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if content:
                    documents[name] = content
        return documents


class DefaultContextAssembler:
    def __init__(
        self,
        *,
        firmware: FirmwarePort,
        memory_documents: MemoryDocumentPort,
        memory_store: MemoryPort,
        sop_resolver: SOPResolverPort | None = None,
        skill_resolver: SkillResolverPort | None = None,
        tool_registry: ToolRegistryPort | None = None,
        tool_catalog: ToolCatalogPort | None = None,
        system_tool_definitions: list[object] | None = None,
        autonomy_level: AutonomyLevel = AutonomyLevel.CREATIVE,
    ) -> None:
        self.firmware = firmware
        self.memory_documents = memory_documents
        self.memory_store = memory_store
        self.sop_resolver = sop_resolver
        self.skill_resolver = skill_resolver
        self.tool_registry = tool_registry
        self.tool_catalog = tool_catalog
        self.system_tool_definitions = system_tool_definitions or []
        self.autonomy_level = autonomy_level

    def assemble(self, event: RobotEvent, user_id: str) -> PlanningContext:
        firmware_documents = dict(self.firmware.get_base_guidance())
        event_instruction = self.firmware.get_event_instruction(event.event_type)
        if event_instruction.strip():
            firmware_documents[f"{event.event_type}.instruction"] = event_instruction

        active_sops = []
        if self.sop_resolver is not None:
            active_sops = self.sop_resolver.resolve(event, user_id)

        all_required_tools: set[str] = set()
        all_required_skills: set[str] = set()
        for sop in active_sops:
            all_required_tools.update(sop.required_tools)
            all_required_skills.update(sop.required_skills)

        active_skills = []
        if self.skill_resolver is not None:
            active_skills = self.skill_resolver.resolve(event, user_id)
        for skill in active_skills:
            all_required_tools.update(skill.required_tools)

        tool_schemas: list[dict] = []
        if self.tool_registry is not None:
            self.tool_registry.unmount_all()
            self._mount_tools(active_sops, all_required_tools)
            tool_schemas = self.tool_registry.get_schemas()

        return PlanningContext(
            firmware_documents=firmware_documents,
            memory_documents=self.memory_documents.get_documents(event, user_id),
            memory_context=self.memory_store.build_context(user_id, event.conversation_id),
            tool_schemas=tool_schemas,
            active_skills=active_skills,
            active_sops=active_sops,
        )

    def _mount_tools(self, active_sops: list, all_required_tools: set[str]) -> None:
        """Mount tools according to the autonomy level.

        SCRIPTED   -- Only SOP-required tools. No system tools.
        GUIDED     -- SOP-required tools + system tools (memory, procedure).
        AUTONOMOUS -- If SOP matches: SOP tools. Otherwise: full catalog.
                      System tools always included.
        CREATIVE   -- Like AUTONOMOUS, plus procedure.propose.
                      This is the default -- the robot can learn.

        In AUTONOMOUS and CREATIVE, when no SOP matches (General Mode),
        the full catalog is mounted so the LLM can reason freely.
        """
        assert self.tool_registry is not None
        level = self.autonomy_level

        if level == AutonomyLevel.SCRIPTED:
            # Only what the SOP prescribes -- nothing else
            for tool_name in all_required_tools:
                self.tool_registry.mount(tool_name)
            return

        # GUIDED, AUTONOMOUS, CREATIVE: mount SOP tools or full catalog
        system_names = {s.name for s in self.system_tool_definitions}  # type: ignore[union-attr]

        if active_sops or all_required_tools:
            for tool_name in all_required_tools:
                self.tool_registry.mount(tool_name)
        elif level in (AutonomyLevel.AUTONOMOUS, AutonomyLevel.CREATIVE) and self.tool_catalog is not None:
            # General Mode: mount domain tools from catalog (system tools handled below)
            for tool_def in self.tool_catalog.list_available():
                if tool_def.name not in system_names:
                    self.tool_registry.mount(tool_def.name)

        # System tools: mount based on level
        for sys_tool in self.system_tool_definitions:
            name = sys_tool.name  # type: ignore[union-attr]
            if name == "procedure.propose" and level not in (AutonomyLevel.CREATIVE,):
                continue
            self.tool_registry.mount(name)


class FirmwareHeartbeat:
    def __init__(
        self,
        *,
        firmware: FirmwarePort,
        default_output_target: ReplyTarget | None = None,
        interval_seconds: float = 300.0,
    ) -> None:
        self.firmware = firmware
        self.default_output_target = default_output_target
        self._interval = interval_seconds
        self._last_tick: float = 0.0

    def build_idle_event(self) -> RobotEvent | None:
        import time
        now = time.monotonic()
        if now - self._last_tick < self._interval:
            return None

        instruction = self.firmware.get_event_instruction("heartbeat.tick")
        if not instruction.strip():
            return None

        self._last_tick = now
        return RobotEvent(
            event_id=new_id("evt"),
            event_type="heartbeat.tick",
            source_channel="heartbeat",
            conversation_id="heartbeat",
            text=instruction.strip(),
            reply_target=self.default_output_target,
        )
