"""Kernel port.

A kernel is the bus component that turns observations into decisions:
it subscribes to inputs, curates context, and -- in real iterations --
delegates to actors via :class:`ComponentRequest` / :class:`ComponentResponse`.
The robot holds exactly one kernel.

The port is a marker base class on top of :class:`BusComponent`. The
actual run-loop, phase methods, telemetry events and error handling
live in :class:`src.kernel.base.BaseKernel`. Specializing kernels
inherit from ``BaseKernel`` (or another concrete kernel) so they get
the run loop "for free" and only override the phases they need.

Future kernel-only surface (state introspection, capability
registration) will live here so it stays out of the generic
:class:`BusComponent` contract.
"""

from __future__ import annotations

from src.components import BusComponent


class KernelPort(BusComponent):
    """Marker base class for kernel implementations.

    Kernels differ from generic bus components only by role and
    privilege, not by surface. The role is documented; the
    privileges are enforced by governance middleware on the bus,
    not by this type.
    """
