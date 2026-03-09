from __future__ import annotations

from pathlib import Path

from src.domain import PlanningContext, ReplyTarget, RobotEvent
from src.ports import ContextAssemblerPort, FirmwarePort, HeartbeatPort, MemoryDocumentPort, MemoryPort
from src.skills.ports import SkillResolverPort
from src.sop.ports import SOPResolverPort
from src.tools.ports import ToolRegistryPort
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
        names = ["IDENTITY.md", "TOOLS.md", "DIRECTORY.md", "CORE_MEMORIES.md", "MEMORY.md"]
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
    ) -> None:
        self.firmware = firmware
        self.memory_documents = memory_documents
        self.memory_store = memory_store
        self.sop_resolver = sop_resolver
        self.skill_resolver = skill_resolver
        self.tool_registry = tool_registry

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
            for tool_name in all_required_tools:
                self.tool_registry.mount(tool_name)
            tool_schemas = self.tool_registry.get_schemas()

        return PlanningContext(
            firmware_documents=firmware_documents,
            memory_documents=self.memory_documents.get_documents(event, user_id),
            memory_context=self.memory_store.build_context(user_id, event.conversation_id),
            tool_schemas=tool_schemas,
            active_skills=active_skills,
            active_sops=active_sops,
        )


class FirmwareHeartbeat:
    def __init__(
        self,
        *,
        firmware: FirmwarePort,
        default_output_target: ReplyTarget | None = None,
    ) -> None:
        self.firmware = firmware
        self.default_output_target = default_output_target

    def build_idle_event(self) -> RobotEvent | None:
        instruction = self.firmware.get_event_instruction("heartbeat.tick")
        if not instruction.strip():
            return None

        return RobotEvent(
            event_id=new_id("evt"),
            event_type="heartbeat.tick",
            source_channel="heartbeat",
            conversation_id="heartbeat",
            text=instruction.strip(),
            reply_target=self.default_output_target,
        )
