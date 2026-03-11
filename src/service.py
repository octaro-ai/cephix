from __future__ import annotations

import asyncio

from src.domain import ControlRequest
from src.ports import ChannelControlPort, ChannelInfoPort, ChannelLifecyclePort
from src.robot import DigitalRobot


class RobotService:
    def __init__(
        self,
        *,
        robot: DigitalRobot,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self.robot = robot
        self.runtime = robot.runtime
        self.channels = list(robot.channels)
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._refresh_public_info()
        for channel in self.channels:
            if isinstance(channel, ChannelLifecyclePort):
                await channel.start()

    async def stop(self) -> None:
        self._stop_event.set()
        for channel in self.channels:
            if isinstance(channel, ChannelLifecyclePort):
                await channel.stop()

    async def run_forever(self) -> None:
        await self.start()
        try:
            while not self._stop_event.is_set():
                did_control_work = self._dispatch_control_requests()
                did_work = self.runtime.run_once() or did_control_work
                # Always yield to the event loop so async WS sends can progress.
                await asyncio.sleep(0 if did_work else self.poll_interval_seconds)
        finally:
            await self.stop()

    def _dispatch_control_requests(self) -> bool:
        did_work = False
        for channel in self.channels:
            if not isinstance(channel, ChannelControlPort):
                continue
            requests = channel.drain_control_requests()
            if not requests:
                continue
            did_work = True
            for request in requests:
                response = self._handle_control_request(request)
                channel.send_control_payload(request.recipient_id, response)
        return did_work

    def _handle_control_request(self, request: ControlRequest) -> dict[str, object]:
        if request.request_type == "admin.status":
            return {"type": "admin.status", "status": self.robot.control_plane.get_status()}
        if request.request_type == "admin.onboarding.status":
            return {"type": "admin.onboarding.status", "status": self.robot.control_plane.get_onboarding_status()}
        if request.request_type == "admin.onboarding.apply":
            result = self.robot.control_plane.onboard(request.payload)
            self._refresh_public_info()
            return {"type": "admin.onboarding.apply", **result}
        if request.request_type == "admin.pairing.list":
            return {"type": "admin.pairing.list", "pairings": self.robot.control_plane.list_pairings()}
        if request.request_type == "admin.pairing.approve":
            device_id = str(request.payload.get("device_id") or "").strip()
            if not device_id:
                return {"type": "error", "content": "device_id is required."}
            return {"type": "admin.pairing.approve", **self.robot.control_plane.approve_pairing(device_id)}
        if request.request_type == "session.list":
            conversations = self.robot.kernel.memory.list_conversations()
            return {"type": "session.list", "conversations": conversations}
        return {"type": "error", "content": f"Unknown control request type: {request.request_type}"}

    def _refresh_public_info(self) -> None:
        public_info = self.robot.control_plane.get_public_info()
        for channel in self.channels:
            if isinstance(channel, ChannelInfoPort):
                channel.set_public_info(public_info)
