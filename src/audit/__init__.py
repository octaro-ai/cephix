"""Audit components: persist deliberately recorded :class:`RobotAuditNote`s.

The audit subsystem records the *curated* trail of significant
actions a robot performed (or refused). Components publish notes via
:meth:`RobotComponent.publish_audit`; an :class:`AuditNoteSink`
subscribes to :data:`AUDIT_TOPIC` and persists every note it sees.

This is distinct from the telemetry subsystem
(:mod:`src.telemetry`), which records *all* bus traffic. Telemetry
gives you raw history; audit gives you narrative responsibility.

The off-bus rule: any component that performs work outside the bus
(call a tool, hit an API, write a file, deny an authorization) must
publish an audit note so that the audit log reflects what the robot
actually did. Components that only read/route/transform on-bus
events are already covered by telemetry.
"""

from src.audit.note_sink import AuditNoteSink

__all__ = ["AuditNoteSink"]
