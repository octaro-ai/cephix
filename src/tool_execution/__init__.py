"""Tool execution layer: the IO surface for LLM-driven and direct tool calls.

Public surface:

- :class:`ToolExecutionLayerPort` -- the abstraction every layer
  implementation satisfies.
- :class:`ToolDescriptor` -- catalog entry.
- :class:`ToolInvocationResult` -- normalised invocation outcome.
- :class:`MCSToolExecutionLayer` -- the BUS_PROVIDER component that
  will eventually back the layer with MCS (currently a stub).
"""

from src.tool_execution.mcs_layer import MCSToolExecutionLayer
from src.tool_execution.ports import (
    ToolDescriptor,
    ToolExecutionLayerPort,
    ToolInvocationResult,
)

__all__ = [
    "MCSToolExecutionLayer",
    "ToolDescriptor",
    "ToolExecutionLayerPort",
    "ToolInvocationResult",
]
