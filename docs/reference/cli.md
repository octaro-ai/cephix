# CLI Reference

The CLI entry point is `src.cli:main`. In development you can run commands as:

```bash
uv run python -m src <command>
```

When the package script is installed, the equivalent command is:

```bash
cephix <command>
```

## Commands

| Command | Purpose |
|---|---|
| `demo` | Run the local demo flow (`cephix-drp.py` uses the same demo wiring). |
| `init <robot_id>` | Create a robot home and register it in the host config. |
| `list` | List robot instances registered in `~/.cephix/cephix.yaml`. |
| `start [robot_id]` | Start a robot as a long-lived WebSocket service. Defaults to `main`. |
| `chat [robot_id]` | Connect to a running robot over WebSocket. |
| `admin status` | Query status from a running robot. |
| `admin pairings` | List pending device pairings. |
| `admin tools` | List mounted tools exposed by the runtime. |
| `admin config` | Run the interactive runtime configuration menu. |
| `admin approve <device_id>` | Approve a pending device pairing. |

## `init <robot_id>`

```bash
uv run python -m src init myrobot --name "MyRobot"
```

Useful flags:

| Flag | Purpose |
|---|---|
| `--name` | Human-readable display name. Defaults to `robot_id`. |
| `--home` | Override `CEPHIX_HOME` / `~/.cephix`. |
| `--host` | Preferred WebSocket bind address written to `robot.yaml`. |
| `--port` | Preferred WebSocket port written to `robot.yaml`. |
| `--token` | Initial chat access token. If omitted, one is generated. |
| `--admin-token` | Initial admin token. If omitted, one is generated. |

`init` creates `~/.cephix/cephix.yaml` if needed, registers the robot, writes
`~/.cephix/robots/<robot_id>/robot.yaml`, creates the robot home directories
(including the `workspace/` file sandbox), and runs an interactive LLM
provider picker.

## `start [robot_id]`

```bash
uv run python -m src start myrobot
```

Useful flags:

| Flag | Purpose |
|---|---|
| `--home` | Override `CEPHIX_HOME` / `~/.cephix`. |
| `--host` | Override `websocket.bind` for this process. |
| `--port` | Override the preferred port for this process. |
| `--event-log` | Event log filename/path. Relative paths are written under `logs/`. |
| `--token` | Override the resolved access token for this process. |
| `--admin-token` | Override the resolved admin token for this process. |
| `--no-loopback-auto-approve` | Require token/pairing even for loopback chat clients. |

When the server binds, `start` writes `runtime.json` into the robot home
with the actual bind/port and process ID. This is removed again on shutdown.

## `chat [robot_id]`

```bash
uv run python -m src chat myrobot
```

Useful flags:

| Flag | Purpose |
|---|---|
| `--url` | Explicit WebSocket URL. If omitted with `robot_id`, CLI resolves via `runtime.json` or config. |
| `--home` | Override `CEPHIX_HOME` / `~/.cephix`. |
| `--sender` | Sender/actor ID for messages. Defaults to `owner`. |
| `--conversation` | Optional conversation ID. |
| `--debug` | Request telemetry scope and print telemetry events. Requires token unless loopback only chat is enough. |
| `--token` | Chat access token. |
| `--admin-token` | Admin token; grants admin, chat, and telemetry scopes. |
| `--device-id` | Stable client/device ID used for pairing. |

In chat mode, these slash commands are handled locally by the CLI:

| Command | Purpose |
|---|---|
| `/help` | Show chat/admin commands. |
| `/debug on` / `/debug off` | Toggle telemetry subscription. |
| `/admin` / `/chat` | Switch between chat and admin input modes. |
| `/status` | Admin status. |
| `/tools` | List tools. |
| `/config` | Interactive config menu. |
| `/pairings` | List pending pairings. |
| `/approve <device_id>` | Approve a pending device. |

## `admin ...`

Admin subcommands connect directly to a WebSocket URL and require an admin
token:

```bash
uv run python -m src admin status --url ws://127.0.0.1:8765/ws --admin-token <admin-token>
```

Unlike `chat <robot_id>`, the standalone `admin` command does not currently
resolve a robot ID from `runtime.json`; pass `--url` when the service is not
on the default `ws://127.0.0.1:8765/ws`.
