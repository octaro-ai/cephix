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
        self.robot_name: str = "Robot"

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
        self._console.print(self._panel_cls(text, title=self.robot_name, border_style="green"))

    def print_stream_start(self) -> None:
        """Print the opening frame for a streamed response."""
        self._console.print(f"\n[green]── {self.robot_name} ──[/green]")

    def print_token(self, token: str) -> None:
        """Print a streaming token without newline."""
        self._console.print(token, end="", highlight=False)

    def print_stream_end(self) -> None:
        """Print the closing frame after a streamed response."""
        self._console.print("\n[green]──────────[/green]")

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
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="cephix")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run the local demo flow.")

    init_parser = subparsers.add_parser("init", help="Initialise a new robot instance.")
    init_parser.add_argument("robot_id", help="Unique robot identifier (will be slugified).")
    init_parser.add_argument("--name", default="", help="Human-readable display name (defaults to robot_id).")
    init_parser.add_argument("--home", default="")
    init_parser.add_argument("--host", default="")
    init_parser.add_argument("--port", type=int, default=None)
    init_parser.add_argument("--token", default="", help="Access token for chat connections.")
    init_parser.add_argument("--admin-token", default="", help="Admin token for management.")

    list_parser = subparsers.add_parser("list", help="List all initialised robots.")
    list_parser.add_argument("--home", default="")

    start_parser = subparsers.add_parser("start", help="Start (power on) a robot.")
    start_parser.add_argument("robot_id", nargs="?", default="main", help="Robot to start (default: main).")
    start_parser.add_argument("--home", default="")
    start_parser.add_argument("--host", default="")
    start_parser.add_argument("--port", type=int, default=None)
    start_parser.add_argument("--event-log", default="robot_events.jsonl")
    start_parser.add_argument("--token", default="")
    start_parser.add_argument("--admin-token", default="")
    start_parser.add_argument("--no-loopback-auto-approve", action="store_true")

    chat_parser = subparsers.add_parser("chat", help="Connect to a running robot over WebSocket.")
    chat_parser.add_argument("robot_id", nargs="?", default="", help="Robot to connect to (resolves URL from config).")
    chat_parser.add_argument("--url", default="")
    chat_parser.add_argument("--home", default="")
    chat_parser.add_argument("--sender", default="owner")
    chat_parser.add_argument("--conversation", default="")
    chat_parser.add_argument("--debug", action="store_true")
    chat_parser.add_argument("--token", default="", help="Access token (required).")
    chat_parser.add_argument("--admin-token", default="", help="Admin token (grants admin scope).")
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

    admin_config = admin_subparsers.add_parser("config", help="Reconfigure a running robot.")
    admin_config.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    admin_config.add_argument("--admin-token", default="")
    admin_config.add_argument("--device-id", default=_default_device_id("admin"))

    admin_approve = admin_subparsers.add_parser("approve", help="Approve a device pairing.")
    admin_approve.add_argument("target_device_id")
    admin_approve.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    admin_approve.add_argument("--admin-token", default="")
    admin_approve.add_argument("--device-id", default=_default_device_id("admin"))

    args = parser.parse_args(argv)

    if args.command in (None, "demo"):
        run_demo()
        return

    if args.command == "init":
        _run_init(
            robot_id=args.robot_id,
            robot_name=args.name or None,
            home_dir=args.home or None,
            bind=args.host or None,
            port=args.port,
            access_token=args.token,
            admin_token=args.admin_token,
        )
        return

    if args.command == "list":
        _run_list(home_dir=args.home or None)
        return

    if args.command == "start":
        from src.configuration import slugify_robot_id
        asyncio.run(
            _run_robot(
                host=args.host,
                port=args.port,
                robot_id=slugify_robot_id(args.robot_id),
                robot_name=None,
                home_dir=args.home or None,
                event_log=args.event_log,
                access_token=args.token,
                admin_token=args.admin_token,
                auto_approve_loopback=not args.no_loopback_auto_approve,
            )
        )
        return

    if args.command == "chat":
        chat_url = args.url
        if args.robot_id and not chat_url:
            from src.configuration import resolve_robot_instance, slugify_robot_id
            slug = slugify_robot_id(args.robot_id)
            inst = resolve_robot_instance(robot_id=slug, home_override=args.home or None)
            # Prefer runtime.json (actual bound port) over config (preferred port).
            runtime_file = inst.paths.workspace_dir / "runtime.json"
            if runtime_file.exists():
                from pathlib import Path
                rt = json.loads(runtime_file.read_text(encoding="utf-8"))
                chat_url = f"ws://{rt['bind']}:{rt['port']}/ws"
            else:
                chat_url = f"ws://{inst.bind}:{inst.port}/ws"
        chat_url = chat_url or "ws://127.0.0.1:8765/ws"
        asyncio.run(
            _run_chat(
                url=chat_url,
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


def _run_init(
    *,
    robot_id: str,
    robot_name: str | None,
    home_dir: str | None,
    bind: str | None,
    port: int | None,
    access_token: str,
    admin_token: str,
) -> None:
    from src.configuration import (
        _KNOWN_API_KEY_VARS,
        init_robot_instance,
        read_secret,
        save_robot_config,
        save_secret,
        seed_global_env,
        slugify_robot_id,
        _load_yaml,
    )

    ui = _build_cli_ui()
    slug = slugify_robot_id(robot_id)
    name = robot_name or robot_id

    ui.print_info(f"\nInitialising robot [bold]{name}[/bold] (id: {slug})\n")

    # Seed global .env from CWD .env before init (so API keys are available).
    seeded = seed_global_env(home_override=home_dir)
    for key in seeded:
        ui.print_info(f"Seeded {key} into global .env")

    try:
        instance = init_robot_instance(
            robot_id=slug,
            robot_name=name,
            home_override=home_dir,
            bind=bind,
            port=port,
        )
    except RuntimeError as exc:
        ui.print_error(str(exc))
        sys.exit(1)

    ui.print_success("Workspace created.")

    # -- Tokens ----------------------------------------------------------------
    effective_access = access_token or secrets.token_urlsafe(24)
    effective_admin = admin_token or secrets.token_urlsafe(24)
    save_secret(instance.access_token_env, effective_access, instance.paths.instance_env_path)
    save_secret(instance.admin_token_env, effective_admin, instance.paths.instance_env_path)

    # -- LLM provider (interactive) -------------------------------------------
    # Show which API keys are already available.
    llm_keys_available: dict[str, str] = {}
    for key_var in _KNOWN_API_KEY_VARS:
        value = read_secret(key_var, instance.paths.instance_env_path, global_fallback=instance.paths.global_env_path)
        if value:
            masked = value[:4] + "..." + value[-4:] if len(value) > 12 else "****"
            llm_keys_available[key_var] = masked

    llm_config = _collect_llm_config(ui, llm_keys_available=llm_keys_available)

    if llm_config:
        robot_cfg = _load_yaml(instance.paths.robot_config_path)
        robot_cfg["llm"] = {
            "provider": llm_config["provider"],
            "model": llm_config["model"],
            "api_key_env": llm_config.get("api_key_env", ""),
        }
        save_robot_config(robot_cfg, instance.paths.robot_config_path)

        # Persist API key if the user entered one directly.
        api_key_value = llm_config.get("api_key_value", "").strip()
        api_key_env = llm_config.get("api_key_env", "")
        if api_key_value and api_key_env:
            save_secret(api_key_env, api_key_value, instance.paths.instance_env_path)

    # -- Summary ---------------------------------------------------------------
    ui.print_info("")
    ui.print_success(f"Robot '{instance.robot_name}' is ready.")
    ui.print_info(f"  ID:           {instance.robot_id}")
    ui.print_info(f"  Workspace:    {instance.paths.workspace_dir}")
    ui.print_info(f"  Access token: {effective_access}")
    ui.print_info(f"  Admin token:  {effective_admin}")
    if llm_config:
        ui.print_info(f"  LLM:          {llm_config['provider']} / {llm_config['model']}")
    else:
        ui.print_warning("  No LLM configured — robot will use keyword fallback.")
        ui.print_info("  Reconfigure later via: cephix chat {slug} --admin-token ... → /admin → onboard")
    ui.print_info(f"\nStart with:  cephix start {instance.robot_id}")


def _run_list(*, home_dir: str | None) -> None:
    from src.configuration import list_robot_instances

    ui = _build_cli_ui()
    instances = list_robot_instances(home_override=home_dir)

    if not instances:
        ui.print_warning("No robots initialised yet. Run: cephix init <name>")
        return

    ui.print_info(f"{'ID':<20} {'Name':<25} {'Onboarded':<12} {'Workspace'}")
    ui.print_info("-" * 80)
    for inst in instances:
        onboarded = "yes" if inst.onboarded else "no"
        ui.print_info(f"{inst.robot_id:<20} {inst.robot_name:<25} {onboarded:<12} {inst.paths.workspace_dir}")


async def _run_robot(
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

    # Verify robot has been initialised and onboarded.
    from src.configuration import resolve_robot_instance, is_robot_workspace_initialized
    try:
        inst = resolve_robot_instance(robot_id=robot_id, home_override=home_dir)
    except Exception:
        inst = None
    if inst is None or not is_robot_workspace_initialized(inst.paths.workspace_dir):
        ui.print_error(f"Robot '{robot_id}' has not been initialised yet.")
        ui.print_info(f"Run first:  cephix init {robot_id}")
        sys.exit(1)
    if not inst.onboarded:
        ui.print_error(f"Robot '{robot_id}' is not fully onboarded.")
        ui.print_info(f"Run:  cephix init {robot_id}  (or reconfigure via /admin)")
        sys.exit(1)

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

    # Write runtime info so `cephix chat <robot>` can find the actual port.
    runtime_file = inst.paths.workspace_dir / "runtime.json" if inst else None
    if runtime_file:
        runtime_file.write_text(
            json.dumps({"bind": actual_host, "port": actual_port, "pid": os.getpid()}),
            encoding="utf-8",
        )

    ui.print_success(f"Cephix robot '{service.robot.robot_id}' listening on ws://{actual_host}:{actual_port}/ws")
    try:
        await stop_event.wait()
    finally:
        if runtime_file and runtime_file.exists():
            runtime_file.unlink(missing_ok=True)
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
            ui.robot_name = server_info.get("robot_name") or server_info.get("robot_id") or "Robot"
            ui.print_success(f"Connected to {ui.robot_name} at {url}")
            if is_admin:
                ui.print_info("Admin session active. Use /admin and /chat to switch modes.")
            if server_info.get("onboarding_required"):
                ui.print_error("Robot is not initialised. Run: cephix init <robot-id>")
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

            if command == "config":
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
                    await _run_config_menu(ws, ui=ui, control_queue=control_queue)
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
            ui.print_info("Admin: /status, /config, /pairings, /approve <device_id>")
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

    if lowered in {"status", "config", "pairings"} or lowered.startswith("approve "):
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
    # Accept commands with or without leading /.
    cleaned = command_text.lstrip("/").strip()
    lowered = cleaned.lower()
    if lowered == "status":
        await ws.send_json({"type": "admin.status"})
        response = await _wait_for_control_message(control_queue, accepted_types={"admin.status", "error"})
        ui.print_json(response.get("status", {}), title="Status")
        return

    if lowered == "config":
        await _run_config_menu(ws, ui=ui, control_queue=control_queue)
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

    ui.print_error(f"[error] unknown command: {cleaned}. Try /help")


def _pick_provider(ui: CliUI) -> ProviderInfo | None:
    """Numbered provider picker backed by the model catalog."""
    from src.llm.catalog import ModelCatalog, ProviderInfo

    ui.print_info("  Loading model catalog...")
    catalog = ModelCatalog()
    providers = catalog.list_providers(preferred_only=True)

    if not providers:
        ui.print_warning("  Could not load model catalog.")
        pid = ui.prompt("  Provider name").strip().lower()
        key_env = ui.prompt("  API key env var", default="").strip()
        return ProviderInfo(id=pid, label=pid, api_key_env=key_env)

    ui.print_info("")
    for i, p in enumerate(providers, 1):
        model_count = f"[dim]({len(p.models)} models)[/dim]" if p.models else ""
        ui.print_info(f"  [cyan bold]{i}[/]  {p.label}  {model_count}")
    more_idx = len(providers) + 1
    custom_idx = len(providers) + 2
    ui.print_info(f"  [cyan bold]{more_idx}[/]  [italic]More providers...[/italic]")
    ui.print_info(f"  [cyan bold]{custom_idx}[/]  [italic]Custom...[/italic]")
    ui.print_info("")

    raw = ui.prompt("Provider", default="1").strip()

    try:
        idx = int(raw)
    except ValueError:
        for p in providers:
            if raw.lower() == p.id:
                ui.print_success(f"  > {p.label}")
                return p
        ui.print_warning(f"Unknown provider '{raw}'.")
        return None

    if 1 <= idx <= len(providers):
        chosen = providers[idx - 1]
        ui.print_success(f"  > {chosen.label}")
        return chosen

    if idx == more_idx:
        all_providers = catalog.list_providers(preferred_only=False)
        ui.print_info("")
        for i, p in enumerate(all_providers, 1):
            ui.print_info(f"  [cyan bold]{i}[/]  {p.label}")
        ui.print_info("")
        raw2 = ui.prompt("Provider", default="1").strip()
        try:
            idx2 = int(raw2)
            if 1 <= idx2 <= len(all_providers):
                chosen = all_providers[idx2 - 1]
                ui.print_success(f"  > {chosen.label}")
                return chosen
        except ValueError:
            for p in all_providers:
                if raw2.lower() == p.id:
                    ui.print_success(f"  > {p.label}")
                    return p
        ui.print_warning("Invalid choice.")
        return None

    if idx == custom_idx:
        pid = ui.prompt("  Provider name").strip().lower()
        key_env = ui.prompt("  API key env var", default="").strip()
        return ProviderInfo(id=pid, label=pid, api_key_env=key_env)

    ui.print_warning("Invalid choice.")
    return None


def _pick_model(ui: CliUI, provider: ProviderInfo, *, page_size: int = 15) -> str:
    """Paginated model picker with navigation."""
    from src.llm.catalog import ProviderInfo

    models = provider.models
    if not models:
        return ui.prompt("  Model ID").strip()

    total = len(models)
    offset = 0

    while True:
        page = models[offset:offset + page_size]
        page_num = offset // page_size + 1
        total_pages = (total + page_size - 1) // page_size

        ui.print_info("")
        for i, m in enumerate(page, 1):
            ctx = m.context_label
            cost = m.cost_label
            tools = "[green]tools[/green]" if m.supports_tools else ""
            ui.print_info(
                f"  [cyan bold]{i:>2}[/]  {m.id:<40} "
                f"[dim]{ctx:>6}  {cost:>12}[/dim]  {tools}"
            )

        has_prev = offset > 0
        has_next = offset + page_size < total

        nav = [f"[cyan bold]1-{len(page)}[/] select"]
        if has_next:
            nav.append("[cyan bold]n[/] next")
        if has_prev:
            nav.append("[cyan bold]p[/] prev")
        nav.append("[cyan bold]c[/] custom")

        ui.print_info(
            f"\n  Page {page_num}/{total_pages} ({total} models)  "
            + "  ".join(nav)
        )

        raw = ui.prompt("  >", default="1").strip()
        cmd = raw.lower()

        if cmd == "n" and has_next:
            offset += page_size
            continue
        if cmd == "p" and has_prev:
            offset -= page_size
            continue
        if cmd == "c":
            return ui.prompt("  Model ID").strip()

        try:
            idx = int(cmd)
            if 1 <= idx <= len(page):
                chosen = page[idx - 1]
                ui.print_success(f"  > {chosen.id}")
                return chosen.id
        except ValueError:
            # User typed a model ID directly.
            return raw

        ui.print_warning("  Invalid choice.")


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

    provider = _pick_provider(ui)
    if provider is None:
        return {}

    model = _pick_model(ui, provider)
    if not model:
        return {}

    api_key_env = provider.api_key_env

    api_key_value = ""
    if api_key_env:
        masked = available.get(api_key_env, "")
        if masked:
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

    config: dict[str, str] = {"provider": provider.id, "model": model}
    if api_key_env:
        config["api_key_env"] = api_key_env
    if api_key_value:
        config["api_key_value"] = api_key_value
    return config


async def _run_config_menu(
    ws: aiohttp.ClientWebSocketResponse,
    *,
    ui: CliUI,
    control_queue: asyncio.Queue[dict[str, Any]],
) -> None:
    """Interactive config menu for a running robot."""
    ui.print_info("\n  [bold]Configuration[/bold]\n")
    ui.print_info("  [cyan bold]1[/]  LLM provider & model")
    ui.print_info("  [cyan bold]q[/]  Cancel")
    ui.print_info("")

    choice = ui.prompt("  >", default="1").strip().lower()
    if choice in {"q", ""}:
        return

    if choice == "1":
        # Fetch current status to show available keys.
        await ws.send_json({"type": "admin.onboarding.status"})
        response = await _wait_for_control_message(
            control_queue, accepted_types={"admin.onboarding.status", "error"},
        )
        if response.get("type") == "error":
            ui.print_error(f"[error] {response.get('content', 'Unable to load status')}")
            return

        status = dict(response.get("status", {}))
        llm_keys_available = dict(status.get("llm_keys_available") or {})
        llm_config = _collect_llm_config(ui, llm_keys_available=llm_keys_available)

        if not llm_config:
            ui.print_info("  No changes.")
            return

        apply_payload: dict[str, Any] = {
            "type": "admin.onboarding.apply",
            "llm": llm_config,
        }
        await ws.send_json(apply_payload)
        result = await _wait_for_control_message(
            control_queue, accepted_types={"admin.onboarding.apply", "error"},
        )
        if result.get("type") == "error":
            ui.print_error(f"[error] {result.get('content', 'Config update failed')}")
            return

        ui.print_success(f"  LLM updated: {llm_config['provider']} / {llm_config['model']}")
        return

    ui.print_warning("  Invalid choice.")


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
    streaming = False  # True while we're receiving response_chunk messages.
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        data = json.loads(msg.data)
        msg_type = data.get("type", "")
        if msg_type == "ack":
            if data.get("content") != "message_queued":
                await control_queue.put(data)
            continue
        if msg_type == "response_chunk_clear":
            if streaming:
                if debug_state["enabled"]:
                    ui.print_info("[dim](thinking...)[/dim]\n")
                streaming = False
            continue
        if msg_type == "response_chunk":
            if not streaming:
                streaming = True
                ui.print_stream_start()
            ui.print_token(data.get("content", ""))
            continue
        if msg_type == "response":
            if streaming:
                ui.print_stream_end()
                streaming = False
            else:
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
