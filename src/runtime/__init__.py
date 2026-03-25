from src.runtime.event_loop import RuntimeEventLoop
from src.runtime.kernel import DigitalRobotKernel

__all__ = ["DigitalRobotKernel", "RuntimeEventLoop"]

# KernelPort is intentionally defined in src.ports (not here) so that
# consumers can depend on the protocol without importing the runtime package.
