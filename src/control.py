from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from src.domain import ReplyTarget
from src.ports import ControlPlanePort, FirmwarePort, PairingRegistryPort
from src.utils import new_id


@dataclass
class PendingPairing:
    pairing_id: str
    device_id: str
    remote_addr: str
    requested_scopes: frozenset[str]
    pairing_code: str


class InMemoryPairingRegistry:
    def __init__(self) -> None:
        self._approved_devices: dict[str, frozenset[str]] = {}
        self._pending_pairings: dict[str, PendingPairing] = {}

    def get_approved_scopes(self, device_id: str) -> frozenset[str]:
        return self._approved_devices.get(device_id, frozenset())

    def queue_pairing(
        self,
        *,
        device_id: str,
        remote_addr: str,
        requested_scopes: set[str],
    ) -> PendingPairing:
        target_scopes = frozenset(requested_scopes)
        for pending in self._pending_pairings.values():
            if pending.device_id == device_id and pending.requested_scopes == target_scopes:
                return pending

        pending = PendingPairing(
            pairing_id=new_id("pair"),
            device_id=device_id,
            remote_addr=remote_addr,
            requested_scopes=target_scopes,
            pairing_code=uuid4().hex[:8].upper(),
        )
        self._pending_pairings[pending.pairing_id] = pending
        return pending

    def list_pairings(self) -> list[dict[str, Any]]:
        return [self._pairing_to_payload(pairing) for pairing in self._pending_pairings.values()]

    def approve_pairing(self, device_id: str) -> dict[str, Any]:
        approved_any = False
        for pairing_id, pending in list(self._pending_pairings.items()):
            if pending.device_id != device_id:
                continue
            self._approved_devices[device_id] = frozenset(
                set(self._approved_devices.get(device_id, frozenset())) | set(pending.requested_scopes)
            )
            self._pending_pairings.pop(pairing_id, None)
            approved_any = True

        return {
            "device_id": device_id,
            "approved": approved_any,
            "granted_scopes": sorted(self._approved_devices.get(device_id, frozenset())),
        }

    @staticmethod
    def _pairing_to_payload(pairing: PendingPairing) -> dict[str, Any]:
        return {
            "pairing_id": pairing.pairing_id,
            "device_id": pairing.device_id,
            "remote_addr": pairing.remote_addr,
            "requested_scopes": sorted(pairing.requested_scopes),
            "pairing_code": pairing.pairing_code,
        }


class RobotControlPlane(ControlPlanePort):
    def __init__(
        self,
        *,
        robot_id: str,
        robot_name: str,
        firmware: FirmwarePort,
        pairings: PairingRegistryPort,
        registered_channels_provider: Callable[[], list[str]],
        default_output_target_provider: Callable[[], ReplyTarget | None],
        onboarded_provider: Callable[[], bool],
        onboarding_status_provider: Callable[[], dict[str, Any]],
        onboarding_handler: Callable[[dict[str, Any]], dict[str, Any]],
        memory_backend_name: str,
        tool_execution_backend_name: str,
        home_path: str | None = None,
        global_env_path: str | None = None,
        instance_env_path: str | None = None,
        home_config_path: str | None = None,
        robot_config_path: str | None = None,
        workspace_path: str | None = None,
        logs_path: str | None = None,
        sessions_path: str | None = None,
        bind: str | None = None,
        port: int | None = None,
    ) -> None:
        self.robot_id = robot_id
        self.robot_name = robot_name
        self.firmware = firmware
        self.pairings = pairings
        self.registered_channels_provider = registered_channels_provider
        self.default_output_target_provider = default_output_target_provider
        self.onboarded_provider = onboarded_provider
        self.onboarding_status_provider = onboarding_status_provider
        self.onboarding_handler = onboarding_handler
        self.memory_backend_name = memory_backend_name
        self.tool_execution_backend_name = tool_execution_backend_name
        self.home_path = home_path
        self.global_env_path = global_env_path
        self.instance_env_path = instance_env_path
        self.home_config_path = home_config_path
        self.robot_config_path = robot_config_path
        self.workspace_path = workspace_path
        self.logs_path = logs_path
        self.sessions_path = sessions_path
        self.bind = bind
        self.port = port

    def get_public_info(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "robot_name": self.robot_name,
            "control_plane": True,
            "onboarding_required": not self.onboarded_provider(),
        }

    def get_status(self) -> dict[str, Any]:
        default_output = self.default_output_target_provider()
        return {
            "robot_id": self.robot_id,
            "robot_name": self.robot_name,
            "onboarded": self.onboarded_provider(),
            "loaded_firmware": sorted(self.firmware.get_base_guidance().keys()),
            "memory_backend": self.memory_backend_name,
            "tool_execution_backend": self.tool_execution_backend_name,
            "registered_channels": self.registered_channels_provider(),
            "default_output_channel": default_output.channel if default_output is not None else None,
            "home_path": self.home_path,
            "global_env_path": self.global_env_path,
            "instance_env_path": self.instance_env_path,
            "home_config_path": self.home_config_path,
            "robot_config_path": self.robot_config_path,
            "workspace_path": self.workspace_path,
            "logs_path": self.logs_path,
            "sessions_path": self.sessions_path,
            "bind": self.bind,
            "port": self.port,
        }

    def get_onboarding_status(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "robot_name": self.robot_name,
            "onboarded": self.onboarded_provider(),
            "home_path": self.home_path,
            "global_env_path": self.global_env_path,
            "instance_env_path": self.instance_env_path,
            "home_config_path": self.home_config_path,
            "robot_config_path": self.robot_config_path,
            "workspace_path": self.workspace_path,
            "bind": self.bind,
            "port": self.port,
            **self.onboarding_status_provider(),
        }

    def onboard(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.onboarding_handler(payload)

    def list_pairings(self) -> list[dict[str, Any]]:
        return self.pairings.list_pairings()

    def approve_pairing(self, device_id: str) -> dict[str, Any]:
        return self.pairings.approve_pairing(device_id)
