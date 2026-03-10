# HEARTBEAT

## On Each Tick

1. Check memory for pending follow-ups or unresolved items.
2. If action items exist, evaluate whether they can be handled now.
3. If nothing requires attention, report HEARTBEAT_OK silently.

## Constraints

- Do not send messages to the user unless there is something actionable.
- Use memory tools to check state before deciding.
- Prefer silent completion over unnecessary notifications.
