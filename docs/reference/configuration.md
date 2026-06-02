# Configuration

Cephix configuration is layered at host and robot level. The host owns the
central registry and defaults; each robot instance can then override or extend
those values in its own robot home.

## Layering

```
Package defaults      src/defaults.yaml
        |
        v merged into
Host config           ~/.cephix/cephix.yaml
        |
        v selected robot entry + defaults
Robot config          ~/.cephix/robots/<robot_id>/robot.yaml
        |
        v secrets resolved from
Instance .env         ~/.cephix/robots/<robot_id>/.env
Global .env           ~/.cephix/.env
OS environment        ANTHROPIC_API_KEY, OPENAI_API_KEY, ...
        |
        v final runtime override
CLI flags             --host, --port, --token, --admin-token, ...
```

The implementation lives in `src/configuration.py`. `load_home_config()`
deep-merges `src/defaults.yaml` with `~/.cephix/cephix.yaml`.
`resolve_robot_instance()` then combines host defaults, the matching robot
entry, `robot.yaml`, secrets, and CLI overrides.

## Host config — `~/.cephix/cephix.yaml`

The host config is created from `src/defaults.yaml` on first use. It contains
defaults for all robots on this machine and the registry of known robot
instances.

```yaml
defaults:
  websocket:
    bind: 127.0.0.1
    port: 8765
    access_token_env: ""
    admin_token_env: ""
    auto_approve_loopback: true
  runtime:
    poll_interval_seconds: 0.05
robots:
  - id: myrobot
    name: MyRobot
    home: ~/.cephix/robots/myrobot   # legacy key `workspace:` is still read
    config_path: ~/.cephix/robots/myrobot/robot.yaml
    enabled: true
    autostart: false
```

A `robots[]` entry is only needed when a robot's home lives outside the
convention (`~/.cephix/robots/<id>/`). Bots inside the convention are
auto-discovered. The path is stored under `home:`; entries written before
the rename used `workspace:` and continue to resolve.

The `robots` entry may also carry per-robot defaults such as `websocket` or
`runtime`, but normal onboarding writes runtime values into the robot's
`robot.yaml`.

## Robot config — `robot.yaml`

`cephix init <robot_id>` creates the robot home and writes
`~/.cephix/robots/<robot_id>/robot.yaml`.

```yaml
id: myrobot
name: MyRobot
enabled: true
autostart: false
websocket:
  bind: 127.0.0.1
  port: 8765
  access_token_env: CEPHIX_MYROBOT_WS_ACCESS_TOKEN
  admin_token_env: CEPHIX_MYROBOT_WS_ADMIN_TOKEN
  auto_approve_loopback: true
runtime:
  poll_interval_seconds: 0.05
llm:
  provider: anthropic
  model: claude-sonnet-4-20250514
  api_key_env: ANTHROPIC_API_KEY
```

Important keys:

| Key | Purpose |
|---|---|
| `websocket.bind` | Interface for the aiohttp WebSocket server. Use `0.0.0.0` only when exposing intentionally. |
| `websocket.port` | Preferred port. At start time Cephix may choose a free port if the preferred port is occupied. |
| `websocket.access_token_env` | Name of the env var containing the chat token. |
| `websocket.admin_token_env` | Name of the env var containing the admin token. |
| `websocket.auto_approve_loopback` | Grants local loopback clients chat scope without pairing. |
| `runtime.poll_interval_seconds` | Idle poll interval for `RobotService`. |
| `llm.provider` | `anthropic`, `openai`, `litellm`, or `stub`. Empty/missing falls back to keyword mode. |
| `llm.model` | Provider-specific model ID. |
| `llm.api_key_env` | Env var name used by the layered secret resolver. |
| `llm.max_tokens` | Optional Anthropic max-token override. |
| `llm.thinking_budget_tokens` | Optional Anthropic extended-thinking budget. |
| `llm.base_url` | Optional OpenAI-compatible or LiteLLM endpoint. |
| `governance.principal_ids` | Actor IDs treated as principals by `ConfigBasedActorResolver`. |
| `governance.delegate_ids` | Actor IDs treated as delegates. |
| `governance.operator_ids` | Actor IDs treated as operators. |
| `mail` | Optional mail-driver config consumed by `src/tools/mail_driver_factory.py`. |
| `workstation` | Optional Docker workstation config consumed by `src/app.py`. |

Robot config overrides host defaults only for that robot. This is the intended
model: a central host config for all robots, plus robot-local override and
extension.

## Secrets

Secrets are not stored in YAML. They are resolved by name:

1. `~/.cephix/robots/<robot_id>/.env`
2. `~/.cephix/.env`
3. OS environment

This allows a shared host-level API key in `~/.cephix/.env`, while a specific
robot can override it in its own `.env`.

Common variables:

| Variable | Purpose |
|---|---|
| `CEPHIX_<ROBOT_ID>_WS_ACCESS_TOKEN` | Chat token for one robot. |
| `CEPHIX_<ROBOT_ID>_WS_ADMIN_TOKEN` | Admin token for one robot. |
| `ANTHROPIC_API_KEY` | Anthropic API key when `llm.provider: anthropic`. |
| `OPENAI_API_KEY` | OpenAI API key when `llm.provider: openai`. |
| `CEPHIX_HOME` | Override the default `~/.cephix` host directory. |

On startup and init, Cephix also attempts to seed known API keys from a local
project `.env` into the global `~/.cephix/.env` if they are not already set.

## CLI flags

CLI flags take final precedence for that invocation:

```bash
uv run python -m src init --help
uv run python -m src start --help
uv run python -m src chat --help
uv run python -m src admin --help
```

The most commonly used:

| Command | Useful flags |
|---|---|
| `init <robot_id>` | `--name`, `--home`, `--host`, `--port`, `--token`, `--admin-token` |
| `start [robot_id]` | `--home`, `--host`, `--port`, `--event-log`, `--token`, `--admin-token`, `--no-loopback-auto-approve` |
| `chat [robot_id]` | `--url`, `--home`, `--sender`, `--conversation`, `--debug`, `--token`, `--admin-token`, `--device-id` |
| `admin status` | `--url`, `--admin-token`, `--device-id` |
| `admin pairings` | `--url`, `--admin-token`, `--device-id` |
| `admin tools` | `--url`, `--admin-token`, `--device-id` |
| `admin config` | `--url`, `--admin-token`, `--device-id` |
| `admin approve <device_id>` | `--url`, `--admin-token`, `--device-id` |
| `list` | `--home` |

## Runtime files

`~/.cephix/robots/<robot_id>/` is the robot's **home** - its whole on-disk
presence. The directories below are created lazily by the components that
consume them:

```
robot.yaml       Robot-local config
.env             Robot-local secrets
firmware/        AGENTS.md, POLICY.md, CONSTITUTION.md, HEARTBEAT.md
logs/            telemetry.jsonl, audit.jsonl, cephix.log
sessions/        Runtime session data
configs/         User-editable YAML lists (heartbeats.yaml, ...)
workspace/       The robot's file sandbox (see below)
runtime.json     Actual bind/port while the service is running
```

### `workspace/` - the file-tool sandbox

The `workspace/` sub-directory is the **only** part of the robot home the
robot's own filesystem tools can reach. The tool-execution layer roots its
`FilesystemConnection` there, deliberately apart from the bot's machinery
(`.env`, `firmware/`, `logs/`, ...), so an LLM tool call can read and write
the robot's working files but cannot touch its secrets or rewrite its
firmware. Put files you want the robot to operate on under
`~/.cephix/robots/<robot_id>/workspace/`.

`runtime.json` is written by `cephix start` after the WebSocket server has
bound its actual port. `cephix chat <robot_id>` prefers this file over the
preferred port in `robot.yaml`.
