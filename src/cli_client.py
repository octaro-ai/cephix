"""Interactive Rich-based WebSocket client for a running cephix robot.

Run with::

    python -m src.cli_client                     # connects to ws://127.0.0.1:8765/ws
    python -m src.cli_client --url ws://host:port/ws

The client is intentionally separate from the robot process: the robot
is a long-running daemon, this is one of many possible client tools.

UI properties:

- The next ``You:`` prompt is shown only after the robot has answered,
  so user input and robot output never interleave on the same line.
- Robot replies are rendered as a green-bordered panel.
- The first prompt waits for the server's ``welcome`` frame so the
  session id is visible before the user starts typing.

Command layer (slash commands):

- ``/new``                       start a fresh chat session
- ``/sessions``                  list existing sessions
- ``/open <id>``                 switch to an existing session (loads history)
- ``/rename <id> <title>``       set a session's title
- ``/help``                      list the commands this robot offers

Failsafe: the client only offers a slash command if the robot's
retained ``capabilities`` manifest advertises the matching action. A
command absent from the manifest is reported as unsupported rather than
sent into the void.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)

DEFAULT_URL = "ws://127.0.0.1:8765/ws"


# ---------------------------------------------------------------------------
# Slash-command parsing (pure, unit-testable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedInput:
    """Result of interpreting one line of user input.

    ``kind`` discriminates the five cases:

    - ``"empty"``   -- blank line; the caller re-prompts.
    - ``"message"`` -- a normal chat turn; ``message`` carries the text.
    - ``"command"`` -- a slash command resolved to an ``action`` plus
      ``args``; ``shortcut`` is the typed alias (for messages).
    - ``"help"``    -- the local ``/help`` request.
    - ``"error"``   -- a malformed / unknown slash command; ``error``
      carries the user-facing reason.
    """

    kind: str
    message: str = ""
    action: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    shortcut: str = ""
    error: str = ""


# Static catalog: typed alias -> (action, usage hint). Kept here so both
# the parser and the ``/help`` renderer agree on the surface.
_SLASH_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("/new", "chat.session.new", "start a new chat"),
    ("/sessions", "chat.session.list", "list sessions"),
    ("/open <id>", "chat.session.open", "open a session and load its history"),
    ("/rename <id> <title>", "chat.session.rename", "set a session's title"),
)


def _parse_input(line: str) -> ParsedInput:
    """Interpret one raw input line into a :class:`ParsedInput`.

    Pure function: no I/O, no manifest awareness. Availability against
    the robot's advertised commands is enforced by the caller so this
    stays trivially testable.
    """
    text = line.strip()
    if not text:
        return ParsedInput(kind="empty")
    if not text.startswith("/"):
        return ParsedInput(kind="message", message=text)

    parts = text.split()
    shortcut = parts[0].lower()
    rest = parts[1:]

    if shortcut in ("/help", "/?"):
        return ParsedInput(kind="help", shortcut="/help")
    if shortcut == "/new":
        return ParsedInput(
            kind="command", action="chat.session.new", shortcut=shortcut
        )
    if shortcut in ("/sessions", "/list"):
        return ParsedInput(
            kind="command", action="chat.session.list", shortcut=shortcut
        )
    if shortcut == "/open":
        if not rest:
            return ParsedInput(
                kind="error", shortcut=shortcut, error="usage: /open <session_id>"
            )
        return ParsedInput(
            kind="command",
            action="chat.session.open",
            args={"session_id": rest[0]},
            shortcut=shortcut,
        )
    if shortcut == "/rename":
        if len(rest) < 2:
            return ParsedInput(
                kind="error",
                shortcut=shortcut,
                error="usage: /rename <session_id> <title>",
            )
        return ParsedInput(
            kind="command",
            action="chat.session.rename",
            args={"session_id": rest[0], "title": " ".join(rest[1:])},
            shortcut=shortcut,
        )
    return ParsedInput(
        kind="error", shortcut=shortcut, error=f"unknown command {shortcut}"
    )


# ---------------------------------------------------------------------------
# Client state
# ---------------------------------------------------------------------------


class _RobotIdentity:
    """Mutable holder of the robot identity learned from the welcome frame."""

    def __init__(self) -> None:
        self.id: str | None = None
        self.name: str | None = None

    @property
    def label(self) -> str:
        if self.name and self.id:
            return f"{self.name} ({self.id})"
        if self.name:
            return self.name
        if self.id:
            return self.id
        return "Robot"


class _ClientState:
    """Per-connection mutable state shared by the print and input loops."""

    def __init__(self) -> None:
        self.identity = _RobotIdentity()
        # The chat session the next message belongs to. ``None`` means
        # "let the server use its per-connection default".
        self.active_session_id: str | None = None
        # Actions the robot advertises -> failsafe gate for slash commands.
        self.available_actions: set[str] = set()
        # correlation_id -> action, so a command_response is rendered
        # against the command that produced it.
        self.pending: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_command_response(
    console: Console, frame: dict[str, Any], state: _ClientState
) -> None:
    """Render a ``command_response`` frame keyed by the action."""
    action = frame.get("action", "")
    status = frame.get("status", "ok")
    payload = frame.get("payload") or {}

    if status != "ok":
        error = frame.get("error") or {}
        code = error.get("code", "error")
        message = error.get("message", "")
        console.print(f"[red]command {action} failed[/] [dim]({code})[/] {message}")
        return

    if action == "chat.session.new":
        sid = payload.get("session_id", "?")
        state.active_session_id = sid
        console.print(f"[green]started new session[/] [bold]{sid}[/]")
    elif action == "chat.session.list":
        _render_session_list(console, payload.get("sessions") or [], state)
    elif action == "chat.session.open":
        sid = payload.get("session_id", "?")
        state.active_session_id = sid
        messages = payload.get("messages") or []
        console.print(
            f"[green]opened session[/] [bold]{sid}[/] "
            f"[dim]· {len(messages)} message(s)[/]"
        )
        _render_history(console, messages, state)
    elif action == "chat.session.rename":
        sid = payload.get("session_id", "?")
        title = payload.get("title", "")
        console.print(f"[green]renamed[/] [bold]{sid}[/] [dim]->[/] {title!r}")
    else:
        console.print(f"[dim]{action}: {payload}[/]")


def _render_session_list(
    console: Console, sessions: list[dict[str, Any]], state: _ClientState
) -> None:
    if not sessions:
        console.print("[dim]no sessions yet[/]")
        return
    console.print("[bold]sessions:[/]")
    for summary in sessions:
        sid = summary.get("session_id", "?")
        title = summary.get("title") or "[dim](untitled)[/]"
        count = summary.get("message_count", 0)
        marker = "[green]*[/]" if sid == state.active_session_id else " "
        console.print(f"  {marker} [bold]{sid}[/] · {title} [dim]({count} msg)[/]")


def _render_history(
    console: Console, messages: list[dict[str, Any]], state: _ClientState
) -> None:
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if role == "user":
            console.print(f"[cyan]You:[/] {content}")
        else:
            console.print(f"[green]{state.identity.label}:[/] {content}")


def _render_help(console: Console, state: _ClientState) -> None:
    available = [
        (alias, hint)
        for alias, action, hint in _SLASH_CATALOG
        if action in state.available_actions
    ]
    if not available:
        console.print("[dim]this robot offers no commands[/]")
        return
    console.print("[bold]commands:[/]")
    for alias, hint in available:
        console.print(f"  [bold]{alias}[/] [dim]· {hint}[/]")
    console.print("  [bold]/help[/] [dim]· this list[/]")


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------


async def _print_loop(
    ws: aiohttp.ClientWebSocketResponse,
    console: Console,
    response_done: asyncio.Event,
    state: _ClientState,
) -> None:
    """Render server frames; release ``response_done`` after each reply."""
    identity = state.identity
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data: dict[str, Any] = json.loads(msg.data)
                except json.JSONDecodeError:
                    console.print(f"[dim]{msg.data}[/]")
                    continue

                kind = data.get("type")
                if kind == "welcome":
                    robot_block = data.get("robot") or {}
                    if isinstance(robot_block, dict):
                        identity.id = robot_block.get("id") or None
                        identity.name = robot_block.get("name") or None
                    sid = data.get("session_id", "?")
                    if identity.id or identity.name:
                        console.print(
                            f"[dim]connected to[/] [bold green]{identity.label}[/] "
                            f"[dim]· session [bold]{sid}[/][/]"
                        )
                    else:
                        console.print(
                            f"[dim]connected · session [bold]{sid}[/][/]"
                        )
                    response_done.set()
                elif kind == "capabilities":
                    commands = data.get("commands") or []
                    state.available_actions = {
                        c.get("action")
                        for c in commands
                        if isinstance(c, dict) and c.get("action")
                    }
                    if state.available_actions:
                        aliases = [
                            alias
                            for alias, action, _ in _SLASH_CATALOG
                            if action in state.available_actions
                        ]
                        if aliases:
                            console.print(
                                "[dim]commands: "
                                + ", ".join(aliases)
                                + " · /help[/]"
                            )
                elif kind == "command_response":
                    corr = data.get("correlation_id") or ""
                    state.pending.pop(corr, None)
                    _render_command_response(console, data, state)
                    response_done.set()
                elif kind == "output":
                    # Accept ``message`` (canonical) and ``text``
                    # (legacy) so the client tolerates older servers.
                    text = data.get("message")
                    if not isinstance(text, str):
                        text = data.get("text", "") or ""
                    source = data.get("source") or ""
                    status = data.get("status", "ok")
                    error_block = data.get("error") if isinstance(data.get("error"), dict) else None
                    is_error = status == "error" or error_block is not None
                    border_style = "red" if is_error else "green"
                    title_color = "red" if is_error else "green"
                    title = f"[{title_color}]{identity.label}[/{title_color}]"
                    subtitle_parts: list[str] = []
                    if source:
                        subtitle_parts.append(source)
                    if error_block:
                        code = error_block.get("code")
                        if isinstance(code, str) and code:
                            subtitle_parts.append(f"error:{code}")
                    subtitle = (
                        f"[dim]{' · '.join(subtitle_parts)}[/dim]"
                        if subtitle_parts
                        else None
                    )
                    console.print(
                        Panel(
                            text,
                            title=title,
                            subtitle=subtitle,
                            border_style=border_style,
                            padding=(0, 1),
                        )
                    )
                    response_done.set()
                elif kind == "shutdown":
                    note = data.get("message") or "robot is shutting down"
                    console.print(f"[yellow]{note}[/]")
                    response_done.set()
                else:
                    console.print(f"[dim]{data}[/]")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                console.print(f"[red]websocket error:[/] {ws.exception()}")
                break
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                break
    finally:
        response_done.set()


async def _input_loop(
    ws: aiohttp.ClientWebSocketResponse,
    console: Console,
    response_done: asyncio.Event,
    state: _ClientState,
) -> None:
    """Sequential prompt -> send -> await response loop."""
    loop = asyncio.get_running_loop()

    await response_done.wait()

    while not ws.closed:
        try:
            user_input = await loop.run_in_executor(
                None,
                lambda: console.input("\n[bold cyan]You:[/] "),
            )
        except EOFError:
            break

        parsed = _parse_input(user_input)

        if parsed.kind == "empty":
            continue
        if parsed.kind == "help":
            _render_help(console, state)
            continue
        if parsed.kind == "error":
            console.print(f"[red]{parsed.error}[/]")
            continue

        if ws.closed:
            console.print("[red]connection closed[/]")
            break

        if parsed.kind == "command":
            if parsed.action not in state.available_actions:
                console.print(
                    f"[red]{parsed.shortcut}[/] is not supported by this robot"
                )
                continue
            if not await _send_command(ws, console, response_done, state, parsed):
                break
            continue

        # Normal chat turn.
        if not await _send_message(ws, console, response_done, state, parsed.message):
            break


async def _send_message(
    ws: aiohttp.ClientWebSocketResponse,
    console: Console,
    response_done: asyncio.Event,
    state: _ClientState,
    text: str,
) -> bool:
    response_done.clear()
    frame: dict[str, Any] = {"type": "input", "message": text}
    if state.active_session_id:
        frame["payload"] = {"session_id": state.active_session_id}
    try:
        await ws.send_json(frame)
    except (aiohttp.ClientConnectionError, ConnectionResetError) as exc:
        console.print(f"[red]connection lost:[/] {exc}")
        return False
    await response_done.wait()
    return True


async def _send_command(
    ws: aiohttp.ClientWebSocketResponse,
    console: Console,
    response_done: asyncio.Event,
    state: _ClientState,
    parsed: ParsedInput,
) -> bool:
    correlation_id = f"cli-{uuid.uuid4().hex[:12]}"
    state.pending[correlation_id] = parsed.action
    response_done.clear()
    frame = {
        "type": "command",
        "action": parsed.action,
        "correlation_id": correlation_id,
        "payload": parsed.args,
    }
    try:
        await ws.send_json(frame)
    except (aiohttp.ClientConnectionError, ConnectionResetError) as exc:
        console.print(f"[red]connection lost:[/] {exc}")
        return False
    await response_done.wait()
    return True


async def _run(url: str) -> None:
    console = Console()
    console.print(
        "[bold]cephix client[/] · type message and hit Enter, /help for commands, "
        "Ctrl-D / Ctrl-Z to exit"
    )
    console.print(f"[dim]connecting to {url} ...[/]")

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.ws_connect(url) as ws:
                response_done = asyncio.Event()
                state = _ClientState()
                printer = asyncio.create_task(
                    _print_loop(ws, console, response_done, state),
                    name="ws.printer",
                )
                try:
                    await _input_loop(ws, console, response_done, state)
                finally:
                    if not ws.closed:
                        await ws.close()
                    printer.cancel()
                    try:
                        await printer
                    except asyncio.CancelledError:
                        pass
        except aiohttp.ClientConnectorError as exc:
            console.print(f"[red]could not connect to {url}:[/] {exc}")
            sys.exit(1)

    console.print("[dim]bye.[/]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cephix CLI client (WebSocket).")
    parser.add_argument("--url", default=DEFAULT_URL, help="WebSocket URL of the robot")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_run(args.url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
