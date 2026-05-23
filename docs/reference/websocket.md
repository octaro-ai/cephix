# WebSocket & Control Plane

Cephix's long-lived service exposes an aiohttp WebSocket channel. The runtime
does not use FastAPI or Uvicorn.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | JSON health/status summary for the WebSocket channel. |
| `GET /ws` | WebSocket endpoint for chat, telemetry, approval decisions, and admin control messages. |

The default URL is:

```text
ws://127.0.0.1:8765/ws
```

The preferred bind/port comes from `robot.yaml`, but the service may bind a
different free port. While running, `cephix start` writes the actual value to
`~/.cephix/robots/<robot_id>/runtime.json`.

## Authentication handshake

The server first sends:

```json
{
  "type": "auth_required",
  "client_id": "...",
  "channel": "ws",
  "server": {
    "robot_id": "myrobot",
    "robot_name": "MyRobot",
    "control_plane": true,
    "onboarding_required": false
  },
  "required": ["auth.hello"]
}
```

Clients respond with `auth.hello`:

```json
{
  "type": "auth.hello",
  "device_id": "my-client",
  "requested_scopes": ["chat"],
  "token": "..."
}
```

For admin:

```json
{
  "type": "auth.hello",
  "device_id": "admin-client",
  "requested_scopes": ["admin"],
  "admin_token": "..."
}
```

Successful auth returns `auth.ok` with `granted_scopes`.

## Scopes

| Scope | Grants |
|---|---|
| `chat` | Send `message`, `approval.decision`, `session.new`, and `session.list` requests. |
| `telemetry` | Subscribe to runtime telemetry via `subscribe_telemetry`. |
| `admin` | Send `admin.*` control messages. Admin auth also grants chat and telemetry. |

When `websocket.auto_approve_loopback` is `true`, loopback clients can get
`chat` scope without a token. Telemetry still requires either an access token
or admin scope.

## Pairing

For non-loopback clients, a valid access token proves the client may request
access. If the requested non-admin scopes have not yet been approved for the
client's `device_id`, the server returns:

```json
{
  "type": "auth.pairing_required",
  "device_id": "my-client",
  "pairing_id": "pair_...",
  "pairing_code": "...",
  "requested_scopes": ["chat", "telemetry"]
}
```

An admin can inspect and approve pending devices:

```bash
uv run python -m src admin pairings --admin-token <admin-token>
uv run python -m src admin approve my-client --admin-token <admin-token>
```

## Chat messages

After authentication with `chat` scope:

```json
{
  "type": "message",
  "content": "Hello",
  "sender_id": "owner",
  "conversation_id": "optional-conversation-id"
}
```

The server acknowledges with `message_queued` and later streams or sends a
response:

```json
{"type": "response_chunk", "content": "..."}
{"type": "response", "content": "Done", "metadata": {"channel": "ws"}}
```

Tool approvals are sent as `approval_prompt`; client button clicks come back
as `approval.decision` events and are handled by the kernel without an LLM
call.

## Admin control messages

Admin messages are converted into `ControlRequest` objects and handled by
`RobotService` / `RobotControlPlane`.

| Message type | Response type |
|---|---|
| `admin.status` | `admin.status` |
| `admin.tools.list` | `admin.tools.list` |
| `admin.onboarding.status` | `admin.onboarding.status` |
| `admin.onboarding.apply` | `admin.onboarding.apply` |
| `admin.pairing.list` | `admin.pairing.list` |
| `admin.pairing.approve` | `admin.pairing.approve` |
| `session.list` | `session.list` |

The CLI wraps these messages in `cephix admin ...` and the chat slash-command
admin mode.
