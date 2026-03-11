from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import signal
import sys
from dataclasses import dataclass
from typing import Any

import aiohttp

from src.app import build_websocket_service, main as run_demo


@dataclass
class _RichSupport:
    console_cls: type[Any]
    panel_cls: type[Any]
    pretty_cls: type[Any]
    prompt_cls: type[Any]
    confirm_cls: type[Any]


class CliUI:
    def __init__(self, support: _RichSupport) -> None:
        self._console = support.console_cls()
        self._stderr = support.console_cls(stderr=True)
        self._panel_cls = support.panel_cls
        self._pretty_cls = support.pretty_cls
        self._prompt_cls = support.prompt_cls
        self._confirm_cls = support.confirm_cls

    def input(self, mode: str) -> str:
        label = "Admin" if mode == "admin" else "You"
        style = "bold yellow" if mode == "admin" else "bold cyan"
        return self._console.input(f"\n[{style}]{label}:[/] ")

    def print_info(self, text: str) -> None:
        self._console.print(text)

    def print_success(self, text: str) -> None:
        self._console.print(f"[bold green]{text}[/]")

    def print_warning(self, text: str) -> None:
        self._stderr.print(f"[bold yellow]{text}[/]")

    def print_error(self, text: str) -> None:
        self._stderr.print(f"[bold red]{text}[/]")

    def print_response(self, text: str) -> None:
        self._console.print(self._panel_cls(text, title="Robot", border_style="green"))

    def print_telemetry(self, event: dict[str, Any]) -> None:
        header = (
            f"[bold magenta]{event.get('event_type', 'telemetry')}[/] "
            f"actor=[cyan]{event.get('actor', 'unknown')}[/]"
        )
        self._console.print(header)
        self._console.print(self._pretty_cls(event.get("payload", {})))

    def print_json(self, payload: Any, *, title: str) -> None:
        self._console.print(self._panel_cls(self._pretty_cls(payload), title=title, border_style="blue"))

    def prompt(self, text: str, *, default: str = "", password: bool = False) -> str:
        return self._prompt_cls.ask(text, default=default, password=password)

    def confirm(self, text: str, *, default: bool = True) -> bool:
        return bool(self._confirm_cls.ask(text, default=default))


def _load_rich_support() -> _RichSupport:
    try:
        from rich.console import Console
        from rich.prompt import Confirm, Prompt
        from rich.panel import Panel
        from rich.pretty import Pretty
    except ImportError as exc:
        raise RuntimeError(
            "The Cephix CLI requires the optional 'cli' extra. Install it with 'pip install .[cli]'."
        ) from exc
    return _RichSupport(
        console_cls=Console,
        panel_cls=Panel,
        pretty_cls=Pretty,
        prompt_cls=Prompt,
        confirm_cls=Confirm,
    )


def _build_cli_ui(
    *,
    support: _RichSupport | None = None,
    support_loader: Any | None = None,
) -> CliUI:
    if support is not None:
        return CliUI(support)
    loader = support_loader or _load_rich_support
    return CliUI(loader())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cephix")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run the local demo flow.")

    serve_parser = subparsers.add_parser("serve", help="Start the robot runtime and channels.")
    serve_parser.add_argument("--robot", default="main")
    serve_parser.add_argument("--name", default="")
    serve_parser.add_argument("--home", default="")
    serve_parser.add_argument("--host", default="")
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--event-log", default="robot_events.jsonl")
    serve_parser.add_argument("--token", default="")
    serve_parser.add_argument("--admin-token", default="")
    serve_parser.add_argument("--no-loopback-auto-approve", action="store_true")

    chat_parser = subparsers.add_parser("chat", help="Connect to a running robot over WebSocket.")
    chat_parser.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    chat_parser.add_argument("--sender", default="owner")
    chat_parser.add_argument("--conversation", default="")
    chat_parser.add_argument("--debug", action="store_true")
    chat_parser.add_argument("--token", default="")
    chat_parser.add_argument("--admin-token", default="")
    chat_parser.add_argument("--device-id", default=_default_device_id("chat"))

    admin_parser = subparsers.add_parser("admin", help="Administrative commands for a running robot.")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command", required=True)

    admin_status = admin_subparsers.add_parser("status", help="Show robot and channel status.")
    admin_status.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    admin_status.add_argument("--admin-token", default="")
    admin_status.add_argument("--device-id", default=_default_device_id("admin"))

    admin_pairings = admin_subparsers.add_parser("pairings", help="List pending pairing requests.")
    admin_pairings.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    admin_pairings.add_argument("--admin-token", default="")
    admin_pairings.add_argument("--device-id", default=_default_device_id("admin"))

    admin_onboard = admin_subparsers.add_parser("onboard", help="Run onboarding for a robot.")
    admin_onboard.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    admin_onboard.add_argument("--admin-token", default="")
    admin_onboard.add_argument("--device-id", default=_default_device_id("admin"))

    admin_approve = admin_subparsers.add_parser("approve", help="Approve a device pairing.")
    admin_approve.add_argument("target_device_id")
    admin_approve.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    admin_approve.add_argument("--admin-token", default="")
    admin_approve.add_argument("--device-id", default=_default_device_id("admin"))

    args = parser.parse_args(argv)

    if args.command in (None, "demo"):
        run_demo()
        return

    if args.command == "serve":
        asyncio.run(
            _run_server(
                host=args.host,
                port=args.port,
                robot_id=args.robot,
                robot_name=args.name or None,
                home_dir=args.home or None,
                event_log=args.event_log,
                access_token=args.token,
                admin_token=args.admin_token,
                auto_approve_loopback=not args.no_loopback_auto_approve,
            )
        )
        return

    if args.command == "chat":
        asyncio.run(
            _run_chat(
                url=args.url,
                sender_id=args.sender,
                conversation_id=args.conversation,
                debug=args.debug,
                access_token=args.token,
                admin_token=args.admin_token,
                device_id=args.device_id,
            )
        )
        return

    if args.command == "admin":
        asyncio.run(
            _run_admin(
                url=args.url,
                admin_token=args.admin_token,
                device_id=args.device_id,
                command=args.admin_command,
                target_device_id=getattr(args, "target_device_id", None),
            )
        )
        return

    parser.print_help()


def _default_device_id(kind: str) -> str:
    hostname = platform.node().lower() or "local"
    return f"cephix-{kind}-{hostname}"


async def _run_server(
    *,
    host: str,
    port: int | None,
    robot_id: str,
    robot_name: str | None,
    home_dir: str | None,
    event_log: str,
    access_token: str,
    admin_token: str,
    auto_approve_loopback: bool,
) -> None:
    ui = _build_cli_ui()

    # Seed global .env from CWD .env (copies known API keys if not present).
    from src.configuration import seed_global_env
    seeded = seed_global_env(home_override=home_dir)
    for key in seeded:
        ui.print_info(f"Seeded {key} into global .env")

    service = build_websocket_service(
        robot_id=robot_id,
        robot_name=robot_name,
        host=host or None,
        port=port,
        event_log_path=event_log,
        access_token=access_token,
        admin_token=admin_token,
        auto_approve_loopback=auto_approve_loopback,
        home_dir=home_dir,
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    task = asyncio.create_task(service.run_forever())
    channel = service.channels[0]
    for _ in range(100):
        bound_port = getattr(channel, "bound_port", 0)
        if bound_port:
            break
        await asyncio.sleep(0.01)
    actual_host = getattr(channel, "bind", host or "127.0.0.1")
    actual_port = getattr(channel, "bound_port", port or 0)
    ui.print_success(f"Cephix robot '{service.robot.robot_id}' listening on ws://{actual_host}:{actual_port}/ws")
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _run_chat(
    *,
    url: str,
    sender_id: str,
    conversation_id: str,
    debug: bool,
    access_token: str,
    admin_token: str,
    device_id: str,
) -> None:
    ui = _build_cli_ui()
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(url) as ws:
            greeting = await ws.receive_json()
            if greeting.get("type") != "auth_required":
                raise RuntimeError("Unexpected server greeting")

            requested_scopes = ["admin"] if admin_token else (["chat", "telemetry"] if debug else ["chat"])
            auth_payload: dict[str, Any] = {
                "type": "auth.hello",
                "device_id": device_id,
                "requested_scopes": requested_scopes,
            }
            if admin_token:
                auth_payload["admin_token"] = admin_token
            else:
                auth_payload["token"] = access_token
            await ws.send_json(auth_payload)
            auth = await ws.receive_json()
            if auth.get("type") == "auth.pairing_required":
                ui.print_warning(
                    "Device pending approval. "
                    f"device_id={device_id} pairing_code={auth.get('pairing_code', '')}"
                )
                return
            if auth.get("type") != "auth.ok":
                raise RuntimeError(auth.get("content", "Authentication failed"))

            granted_scopes = set(auth.get("granted_scopes", []))
            is_admin = "admin" in granted_scopes
            debug_state = {"enabled": debug}
            mode_state = {"current": "chat"}
            server_info = dict(auth.get("server", {}))
            ui.print_success(f"Connected to {server_info.get('robot_id', 'robot')} at {url}")
            if is_admin:
                ui.print_info("Admin session active. Use /admin and /chat to switch modes.")
            if server_info.get("onboarding_required") and not is_admin:
                ui.print_error("Robot onboarding required. Reconnect with admin scope to initialize this machine.")
                return

            if debug_state["enabled"]:
                await ws.send_json({"type": "subscribe_telemetry", "enabled": True})
                telemetry_ack = await ws.receive_json()
                if telemetry_ack.get("type") == "error":
                    ui.print_warning(f"[debug disabled] {telemetry_ack.get('content', '')}")
                    debug_state["enabled"] = False

            response_done = asyncio.Event()
            control_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            receiver = asyncio.create_task(
                _receive_chat(
                    ws,
                    ui=ui,
                    debug_state=debug_state,
                    response_done=response_done,
                    control_queue=control_queue,
                )
            )
            if server_info.get("onboarding_required") and is_admin:
                mode_state["current"] = "admin"
                ui.print_warning("Robot is not onboarded yet. Starting onboarding in ControlPlane mode.")
                await _run_onboarding_wizard(
                    ws,
                    ui=ui,
                    control_queue=control_queue,
                    robot_id=str(server_info.get("robot_id") or "robot"),
                    robot_name=str(server_info.get("robot_name") or server_info.get("robot_id") or "robot"),
                    current_admin_token=admin_token,
                )
                mode_state["current"] = "chat"
                ui.print_info("[mode] chat")
            loop = asyncio.get_running_loop()
            try:
                while True:
                    user_input = await loop.run_in_executor(None, lambda: ui.input(mode_state["current"]))
                    if not user_input.strip():
                        continue
                    lowered_input = user_input.lower().strip()
                    if lowered_input in {"exit", "quit"} and mode_state["current"] == "admin":
                        mode_state["current"] = "chat"
                        ui.print_info("[mode] chat")
                        continue
                    if lowered_input in {"exit", "quit"}:
                        break
                    command_handled = await _handle_chat_command(
                        ws,
                        user_input.strip(),
                        is_admin=is_admin,
                        ui=ui,
                        debug_state=debug_state,
                        mode_state=mode_state,
                        control_queue=control_queue,
                    )
                    if command_handled:
                        continue
                    if mode_state["current"] == "admin":
                        await _handle_admin_mode_input(
                            ws,
                            user_input.strip(),
                            ui=ui,
                            control_queue=control_queue,
                        )
                        continue
                    payload: dict[str, Any] = {
                        "type": "message",
                        "content": user_input,
                        "sender_id": sender_id,
                    }
                    if conversation_id:
                        payload["conversation_id"] = conversation_id
                    response_done.clear()
                    await ws.send_json(payload)
                    await response_done.wait()
            finally:
                receiver.cancel()
                try:
                    await receiver
                except asyncio.CancelledError:
                    pass


async def _run_admin(
    *,
    url: str,
    admin_token: str,
    device_id: str,
    command: str,
    target_device_id: str | None,
) -> None:
    ui = _build_cli_ui()
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(url) as ws:
            greeting = await ws.receive_json()
            if greeting.get("type") != "auth_required":
                raise RuntimeError("Unexpected server greeting")

            await ws.send_json(
                {
                    "type": "auth.hello",
                    "device_id": device_id,
                    "admin_token": admin_token,
                    "requested_scopes": ["admin"],
                }
            )
            auth = await ws.receive_json()
            if auth.get("type") != "auth.ok":
                raise RuntimeError(auth.get("content", "Authentication failed"))

            if command == "status":
                await ws.send_json({"type": "admin.status"})
                response = await ws.receive_json()
                ui.print_json(response.get("status", {}), title="Status")
                return

            if command == "pairings":
                await ws.send_json({"type": "admin.pairing.list"})
                response = await ws.receive_json()
                ui.print_json(response.get("pairings", []), title="Pairings")
                return

            if command == "onboard":
                control_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
                receiver = asyncio.create_task(
                    _receive_chat(
                        ws,
                        ui=ui,
                        debug_state={"enabled": False},
                        response_done=asyncio.Event(),
                        control_queue=control_queue,
                    )
                )
                try:
                    await _run_onboarding_wizard(ws, ui=ui, control_queue=control_queue, current_admin_token=admin_token)
                finally:
                    receiver.cancel()
                    try:
                        await receiver
                    except asyncio.CancelledError:
                        pass
                return

            if command == "approve":
                await ws.send_json({"type": "admin.pairing.approve", "device_id": target_device_id})
                response = await ws.receive_json()
                ui.print_json(response, title="Approve")
                return

            raise RuntimeError(f"Unknown admin command: {command}")


async def _handle_chat_command(
    ws: aiohttp.ClientWebSocketResponse,
    command_text: str,
    *,
    is_admin: bool,
    ui: CliUI | None = None,
    debug_state: dict[str, bool],
    mode_state: dict[str, str],
    control_queue: asyncio.Queue[dict[str, Any]],
) -> bool:
    ui = ui or _build_cli_ui()
    if not command_text.startswith("/"):
        return False

    command = command_text[1:].strip()
    lowered = command.lower()

    if lowered == "help":
        ui.print_info("Commands: /help, /debug on, /debug off, /admin, /chat")
        if is_admin:
            ui.print_info("Admin mode commands: status, onboard, pairings, approve <device_id>")
        return True

    if lowered == "admin":
        if not is_admin:
            ui.print_error("[error] admin scope required")
            return True
        mode_state["current"] = "admin"
        ui.print_info("[mode] admin")
        return True

    if lowered == "chat":
        mode_state["current"] = "chat"
        ui.print_info("[mode] chat")
        return True

    if lowered in {"debug", "debug on"}:
        await ws.send_json({"type": "subscribe_telemetry", "enabled": True})
        response = await _wait_for_control_message(control_queue, accepted_types={"ack", "error"})
        if response.get("type") == "ack":
            ui.print_info("[debug] enabled")
            debug_state["enabled"] = True
            return True
        ui.print_error(f"[error] {response.get('content', 'Unable to enable debug')}")
        return True

    if lowered == "debug off":
        await ws.send_json({"type": "subscribe_telemetry", "enabled": False})
        response = await _wait_for_control_message(control_queue, accepted_types={"ack", "error"})
        if response.get("type") == "ack":
            ui.print_info("[debug] disabled")
            debug_state["enabled"] = False
            return True
        ui.print_error(f"[error] {response.get('content', 'Unable to disable debug')}")
        return True

    if not is_admin:
        ui.print_error("[error] admin scope required")
        return True

    if lowered in {"status", "onboard", "pairings"} or lowered.startswith("approve "):
        await _handle_admin_mode_input(ws, command, ui=ui, control_queue=control_queue)
        return True

    ui.print_error(f"[error] unknown command: /{command}")
    return True


async def _handle_admin_mode_input(
    ws: aiohttp.ClientWebSocketResponse,
    command_text: str,
    *,
    ui: CliUI | None = None,
    control_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    ui = ui or _build_cli_ui()
    lowered = command_text.lower()
    if lowered == "status":
        await ws.send_json({"type": "admin.status"})
        response = await _wait_for_control_message(control_queue, accepted_types={"admin.status", "error"})
        ui.print_json(response.get("status", {}), title="Status")
        return

    if lowered == "onboard":
        await _run_onboarding_wizard(ws, ui=ui, control_queue=control_queue)
        return

    if lowered == "pairings":
        await ws.send_json({"type": "admin.pairing.list"})
        response = await _wait_for_control_message(control_queue, accepted_types={"admin.pairing.list", "error"})
        ui.print_json(response.get("pairings", []), title="Pairings")
        return

    if lowered.startswith("approve "):
        target_device_id = command_text.partition(" ")[2].strip()
        if not target_device_id:
            ui.print_error("[error] usage: approve <device_id>")
            return
        await ws.send_json({"type": "admin.pairing.approve", "device_id": target_device_id})
        response = await _wait_for_control_message(control_queue, accepted_types={"admin.pairing.approve", "error"})
        ui.print_json(response, title="Approve")
        return

    ui.print_error("[error] unknown admin command")


_LLM_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {"model": "claude-sonnet-4-20250514", "api_key_env": "ANTHROPIC_API_KEY"},
    "openai": {"model": "gpt-4o", "api_key_env": "OPENAI_API_KEY"},
    "litellm": {"model": "anthropic/claude-sonnet-4-20250514", "api_key_env": ""},
}


def _collect_llm_config(
    ui: CliUI,
    llm_keys_available: dict[str, str] | None = None,
) -> dict[str, str]:
    """Interactively collect LLM provider settings during onboarding.

    *llm_keys_available* maps env-var names to masked values (e.g.
    ``{"ANTHROPIC_API_KEY": "sk-a...xyz3"}``).  Keys that are already
    resolved are shown for confirmation so the user can press Enter.
    """
    if not ui.confirm("Configure an LLM provider?", default=True):
        return {}

    available = llm_keys_available or {}

    providers = list(_LLM_PROVIDER_DEFAULTS.keys())
    ui.print_info(f"Available providers: {', '.join(providers)}")
    provider = ui.prompt("LLM provider", default="anthropic").strip().lower()

    if provider not in _LLM_PROVIDER_DEFAULTS:
        ui.print_warning(f"Unknown provider '{provider}'. Skipping LLM configuration.")
        return {}

    defaults = _LLM_PROVIDER_DEFAULTS[provider]
    model = ui.prompt("Model", default=defaults["model"]).strip()
    api_key_env = ui.prompt("API key env var", default=defaults["api_key_env"]).strip()

    api_key_value = ""
    if api_key_env:
        masked = available.get(api_key_env, "")
        if masked:
            # Key already found — show masked value, Enter = keep.
            ui.print_success(f"  ${api_key_env} ({masked})")
            override = ui.prompt("Enter = keep, or paste a new key", default="").strip()
            if override:
                api_key_value = override
        else:
            ui.print_warning(f"  ${api_key_env} not found")
            api_key_value = ui.prompt(
                "API key (Enter = configure later in ~/.cephix/.env)",
                default="",
                password=True,
            ).strip()

    config: dict[str, str] = {"provider": provider, "model": model}
    if api_key_env:
        config["api_key_env"] = api_key_env
    if api_key_value:
        config["api_key_value"] = api_key_value
    return config


async def _run_onboarding_wizard(
    ws: aiohttp.ClientWebSocketResponse,
    *,
    ui: CliUI,
    control_queue: asyncio.Queue[dict[str, Any]],
    robot_id: str = "robot",
    robot_name: str = "robot",
    current_admin_token: str = "",
) -> None:
    await ws.send_json({"type": "admin.onboarding.status"})
    response = await _wait_for_control_message(control_queue, accepted_types={"admin.onboarding.status", "error"})
    if response.get("type") == "error":
        ui.print_error(f"[error] {response.get('content', 'Unable to load onboarding status')}")
        return

    status = dict(response.get("status", {}))
    ui.print_json(status, title="Onboarding")
    default_name = str(status.get("robot_name") or robot_name or robot_id)
    chosen_name = ui.prompt("Robot name", default=default_name).strip() or default_name

    # -- LLM provider selection ------------------------------------------------
    llm_keys_available = dict(status.get("llm_keys_available") or {})
    llm_config = _collect_llm_config(ui, llm_keys_available=llm_keys_available)

    global_candidates = dict(status.get("global_secret_candidates") or {})
    access_token_env = str(status.get("access_token_env") or "")
    admin_token_env = str(status.get("admin_token_env") or "")

    copy_global_admin_token = False
    if current_admin_token:
        persist_admin = ui.confirm("Persist current admin token to the robot .env?", default=True)
        chosen_admin_token = current_admin_token if persist_admin else ""
    elif admin_token_env and global_candidates.get(admin_token_env):
        copy_global_admin_token = ui.confirm(
            f"Central secret '{admin_token_env}' found. Copy it into this robot instance?",
            default=True,
        )
        chosen_admin_token = ""
    else:
        default_admin_token = secrets.token_urlsafe(18)
        chosen_admin_token = ui.prompt("Admin token", default=default_admin_token, password=True).strip()

    chosen_access_token = ""
    copy_global_access_token = False
    if access_token_env and global_candidates.get(access_token_env):
        copy_global_access_token = ui.confirm(
            f"Central secret '{access_token_env}' found. Copy it into this robot instance?",
            default=False,
        )
    if ui.confirm("Configure a chat access token now?", default=False):
        chosen_access_token = ui.prompt(
            "Chat access token",
            default=secrets.token_urlsafe(18),
            password=True,
        ).strip()

    apply_payload: dict[str, Any] = {
        "type": "admin.onboarding.apply",
        "robot_name": chosen_name,
        "admin_token": chosen_admin_token,
        "access_token": chosen_access_token,
        "copy_global_access_token": copy_global_access_token and not chosen_access_token,
        "copy_global_admin_token": copy_global_admin_token and not chosen_admin_token,
    }
    if llm_config:
        apply_payload["llm"] = llm_config
    await ws.send_json(apply_payload)
    result = await _wait_for_control_message(control_queue, accepted_types={"admin.onboarding.apply", "error"})
    if result.get("type") == "error":
        ui.print_error(f"[error] {result.get('content', 'Onboarding failed')}")
        return

    ui.print_json(result, title="Onboarding Complete")
    ui.print_success("Robot onboarding completed.")


async def _receive_chat(
    ws: aiohttp.ClientWebSocketResponse,
    *,
    ui: CliUI,
    debug_state: dict[str, bool],
    response_done: asyncio.Event,
    control_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        data = json.loads(msg.data)
        msg_type = data.get("type", "")
        if msg_type == "ack":
            if data.get("content") != "message_queued":
                await control_queue.put(data)
            continue
        if msg_type == "response":
            ui.print_response(data.get("content", ""))
            if _is_chat_cycle_complete(msg_type, data, debug=debug_state["enabled"]):
                response_done.set()
            continue
        if msg_type == "telemetry" and debug_state["enabled"]:
            event = data.get("event", {})
            ui.print_telemetry(event)
            if _is_chat_cycle_complete(msg_type, data, debug=debug_state["enabled"]):
                response_done.set()
            continue
        if msg_type == "error":
            await control_queue.put(data)
            ui.print_error(f"[error] {data.get('content', '')}")
            response_done.set()
            continue
        await control_queue.put(data)


def _is_chat_cycle_complete(msg_type: str, data: dict[str, Any], *, debug: bool) -> bool:
    if msg_type == "response" and not debug:
        return True
    if msg_type != "telemetry" or not debug:
        return False
    event = data.get("event", {})
    return event.get("event_type") == "run.completed"


async def _wait_for_control_message(
    control_queue: asyncio.Queue[dict[str, Any]],
    *,
    accepted_types: set[str],
) -> dict[str, Any]:
    while True:
        message = await control_queue.get()
        if message.get("type") in accepted_types:
            return message
