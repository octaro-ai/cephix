"""Cephix-side MCS adapters.

Each module here is a class that satisfies one of the MCS adapter
ports (``mcs.driver.<capability>.FilesystemPort``, future
``DatabasePort``, ...) by routing the protocol through Cephix's own
infrastructure (FilesystemConnection, database providers, ...). The
``mcs_adapters`` prefix mirrors :mod:`src.tool_execution.mcs_layer`
and exists to keep the MCS-side terminology from being confused with
Cephix-side adapters like ``LocalFSAdapter`` (which is a BACKEND-tier
component implementing Cephix's own byte-level ``FilesystemPort``).
"""

from src.tool_execution.mcs_adapters.filesystem import MCSFilesystemAdapter

__all__ = ["MCSFilesystemAdapter"]
