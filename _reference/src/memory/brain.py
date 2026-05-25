from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from src.memory.firmware import FirmwareLoader
from src.memory.interfaces import ProcedureStorePort, ProfileStorePort
from src.memory.models import RobotBrainSnapshot
from src.utils import utc_now_iso


class RobotBrainExporter:
    def __init__(
        self,
        *,
        robot_id: str,
        firmware_loader: FirmwareLoader,
        profile_store: ProfileStorePort,
        procedure_store: ProcedureStorePort,
    ) -> None:
        self.robot_id = robot_id
        self.firmware_loader = firmware_loader
        self.profile_store = profile_store
        self.procedure_store = procedure_store

    def build_snapshot(self) -> RobotBrainSnapshot:
        return RobotBrainSnapshot(
            robot_id=self.robot_id,
            exported_at=utc_now_iso(),
            firmware_documents=self.firmware_loader.load_documents(),
            profile_facts=self.profile_store.list_facts(),
            procedures=self.procedure_store.list_procedures(),
            notes=[
                "Runtime state is intentionally excluded.",
                "Event streams stay outside the cloneable brain by default.",
            ],
        )

    def export_to_file(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        snapshot = self.build_snapshot()
        destination.write_text(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
        return destination
