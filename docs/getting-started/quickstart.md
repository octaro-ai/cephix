# Quickstart

This page gets a robot running locally in under a minute.

## Run the demo flow

The fastest way to see the harness in action:

```bash
uv run python cephix-drp.py
```

This runs the local demo flow built by `build_demo_robot` in `src/app.py`.
It sends a synthetic event through the kernel, you can watch the lifecycle
in stdout, and the run finishes when the kernel emits `run.completed`.

## Run as a long-lived service

For the real WebSocket-based service:

### 1. Initialise a robot instance

```bash
uv run python -m src init myrobot --name "MyRobot"
```

This creates a host config at `~/.cephix/cephix.yaml`, registers the robot,
and creates `~/.cephix/robots/myrobot/` with:

- `robot.yaml` — per-robot runtime config
- `.env` — per-robot secrets
- `firmware/` — immutable guardrails
- `memory/` — global memory documents
- `sops/` — standard operating procedures
- `logs/`, `sessions/`, `memory_data/`, `notebooks/`

`init` prints the generated access and admin tokens. Keep them if you plan to
connect from outside loopback or use admin commands.

### 2. Start the robot

```bash
uv run python -m src start myrobot
```

The robot opens an aiohttp WebSocket endpoint (default
`ws://127.0.0.1:8765/ws`) and waits for clients. Startup prints the loaded
context and the actual bound URL. While running, the service also writes
`~/.cephix/robots/myrobot/runtime.json` so clients can find the actual port.

### 3. Connect a chat client

In a second terminal:

```bash
uv run python -m src chat myrobot
```

Type a message and hit Enter. The robot will plan, possibly invoke tools,
and reply.

Local loopback clients can chat without a token when
`websocket.auto_approve_loopback` is `true`. For telemetry/debug or remote
connections, pass the token printed by `init`:

```bash
uv run python -m src chat myrobot --token <access-token> --debug
```

For admin mode:

```bash
uv run python -m src chat myrobot --admin-token <admin-token>
```

Inside the chat client, use `/help`, `/admin`, `/status`, `/tools`,
`/pairings`, `/approve <device_id>`, and `/config`.

## List all robots

```bash
uv run python -m src list
```

Shows every initialised robot registered in `~/.cephix/cephix.yaml`.

## Inspect the service

The service exposes a small health endpoint:

```bash
curl http://127.0.0.1:8765/health
```

For CLI admin status:

```bash
uv run python -m src admin status --admin-token <admin-token>
```

## Where to look when something goes wrong

- **Console output** — the planner streams its tokens, tool calls are logged
  inline, telemetry events are visible.
- **`logs/robot_events.jsonl`** — the WideEvent audit trail. Every phase emits
  a structured event. Open it with `jq` or any JSONL viewer.
- **Approval flow** — if a tool requires approval, you'll see an
  `ApprovalPrompt` in the chat client with four buttons (Once / Always /
  No / Never). Decisions are stored as JSONL in
  `~/.cephix/robots/myrobot/approvals`.
- **Configuration** — host defaults and robot registry live in
  `~/.cephix/cephix.yaml`; robot-specific overrides live in
  `~/.cephix/robots/myrobot/robot.yaml`.

## Next steps

- Read [Concepts › Harness Model](../concepts/harness-model.md) to understand
  the five layers.
- Browse the [Diagrams](../architecture/diagrams.md) for the visual overview.
- Read [Reference › Configuration](../reference/configuration.md) for the
  host/robot config layering.
- Check the [Current State](../state.md) to see what is in flight.
