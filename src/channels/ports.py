"""Channel port.

A channel bridges the bus to an outside-world boundary -- a terminal,
HTTP, WebSocket, Telegram, ... -- and is fully encapsulated behind the
:class:`BusComponent` lifecycle. The robot can be composed with any
number of channels in parallel.

Inside the bus, channels typically:

- publish :class:`RobotInput` events on an input topic when data
  arrives from outside;
- subscribe to an output topic and forward :class:`RobotOutput` events
  back to the outside world.

Routing semantics (how to match an output to the originating session,
multi-tenant separation, auth) are an implementation detail of each
channel and not part of this protocol.
"""

from __future__ import annotations

from src.components import BusComponent


class ChannelPort(BusComponent):
    """Marker base class for channel implementations."""
