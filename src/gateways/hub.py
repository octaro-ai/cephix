from __future__ import annotations

from src.domain import OutboundMessage, ReplyTarget, RobotEvent
from src.ports import ChannelEgressPort, ChannelIngressPort


class ChannelHub:
    def __init__(
        self,
        *,
        ingress_ports: list[ChannelIngressPort] | None = None,
        egress_ports: dict[str, ChannelEgressPort] | None = None,
    ) -> None:
        self.ingress_ports = ingress_ports or []
        self.egress_ports = egress_ports or {}

    def register_ingress(self, port: ChannelIngressPort) -> None:
        self.ingress_ports.append(port)

    def register_egress(self, channel: str, port: ChannelEgressPort) -> None:
        self.egress_ports[channel] = port

    def collect_new_events(self) -> list[RobotEvent]:
        events: list[RobotEvent] = []
        for port in self.ingress_ports:
            events.extend(port.drain_events())
        return events

    def send(self, target: ReplyTarget, message: OutboundMessage) -> None:
        port = self.egress_ports.get(target.channel)
        if port is None:
            raise RuntimeError(f"No ChannelEgressPort registered for channel: {target.channel}")
        port.send(target, message)
