"""Telemetry components: read-only observers of all bus traffic.

Telemetry components subscribe to *every* event flowing over the bus
via :meth:`BusPort.subscribe_all` and record/forward what they see.
They never block, modify, or refuse delivery. Their job is to answer
the question "what is happening inside this robot?".

This is distinct from the audit subsystem
(:mod:`src.audit`), which records only the deliberately curated
:class:`RobotAuditNote` events. Telemetry is the broad raw stream;
audit is the narrative spine.
"""

from src.telemetry.bus_recorder import BusRecorder

__all__ = ["BusRecorder"]
