"""CapabilityCollector: builds the retained capability manifest.

A :class:`~src.components.ComponentCategory.BUS_UTILITY` that watches
the retained :class:`~src.bus.messages.RobotLifecycle` and republishes
the robot's available commands as a retained
:class:`~src.bus.messages.HarnessCapabilities` on
:data:`~src.bus.messages.HARNESS_CAPABILITIES_TOPIC`. UIs read that one
retained slot to learn what they may render.
"""

from src.utility.capability_collector.collector import CapabilityCollector

__all__ = ["CapabilityCollector"]
