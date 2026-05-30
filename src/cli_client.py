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

    ``kind`` discriminates the six cases:

    - ``"empty"``   -- blank line; the caller re-prompts.
    - ``"message"`` -- a normal chat turn; ``message`` carries the text.
    - ``"command"`` -- a slash command resolved to an ``action`` plus
      ``args``; ``shortcut`` is the typed alias (for messages).
    - ``"help"``    -- the local ``/help`` request.
    - ``"exit"``    -- the local ``/exit`` / ``/quit`` request; the
      input loop tears down the connection cleanly (Ctrl-D
      equivalent in keystroke form).
    - ``"error"``   -- a malformed / unknown slash command; ``error``
      carries the user-facing reason.
    """

    kind: str
    message: str = ""
    action: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    shortcut: str = ""
    error: str = ""


# Static catalog: typed alias -> (action, manifest slot, usage hint).
# Kept here so the parser, the ``/help`` renderer, and the
# capabilities-line renderer all agree on the surface.
#
# The ``slot`` field tells the gate which side of the manifest to
# check before offering the alias:
#
# - ``"commands"`` -- the action must appear in
#   ``HarnessCapabilities.commands`` (slash-callable UI ops the
#   chat kernel announces).
# - ``"tools"`` -- the action must appear in
#   ``HarnessCapabilities.tools`` (MCS tools the tool-execution
#   layer exposes; the layer subscribes
#   ``command.request.<action>`` for each so the same wire frame
#   the CLI sends for any other command reaches them).
_SLASH_CATALOG: tuple[tuple[str, str, str, str], ...] = (
    ("/new", "chat.session.new", "commands", "start a new chat"),
    ("/sessions", "chat.session.list", "commands", "list sessions"),
    ("/open <id>", "chat.session.open", "commands", "open a session and load its history"),
    ("/rename <id> <title>", "chat.session.rename", "commands", "set a session's title"),
    ("/time", "current_time", "tools", "current wall-clock time (UTC + optional zone)"),
    ("/calc <expr>", "calculate", "tools", "evaluate a math expression"),
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
    if shortcut in ("/exit", "/quit"):
        return ParsedInput(kind="exit", shortcut="/exit")
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
    if shortcut == "/time":
        return ParsedInput(
            kind="command", action="current_time", shortcut=shortcut
        )
    if shortcut == "/calc":
        if not rest:
            return ParsedInput(
                kind="error",
                shortcut=shortcut,
                error="usage: /calc <expression>  e.g. /calc sqrt(9)+3*(5+6)/4",
            )
        return ParsedInput(
            kind="command",
            action="calculate",
            args={"expression": " ".join(rest)},
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
        # Slash-command gate: two sets, one per manifest slot. A slash
        # alias is only offered when its target action appears in the
        # matching slot.
        self.command_actions: set[str] = set()
        self.tool_actions: set[str] = set()
        # Component-name -> active model id (e.g. ``"chat" -> "gpt-5.5"``)
        # for rendering ``kernel.chat (gpt-5.5)`` panel subtitles.
        self.models_by_component: dict[str, str] = {}
        # Memoized last-rendered capability line so the print loop only
        # re-renders when something actually changed.
        self.last_capability_signature: tuple = ()
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
        for alias, action, slot, hint in _SLASH_CATALOG
        if action in _actions_for_slot(state, slot)
    ]
    console.print("[bold]commands:[/]")
    if available:
        for alias, hint in available:
            console.print(f"  [bold]{alias}[/] [dim]· {hint}[/]")
    else:
        console.print("  [dim]this robot offers no remote commands[/]")
    # Local meta commands - always available, never gated by manifest.
    console.print("  [bold]/help[/] [dim]· this list[/]")
    console.print("  [bold]/exit[/] [dim]· close the connection and quit (also: /quit)[/]")


def _actions_for_slot(state: _ClientState, slot: str) -> set[str]:
    """Return the live action set for a manifest slot."""
    if slot == "commands":
        return state.command_actions
    if slot == "tools":
        return state.tool_actions
    return set()


def _styled_alias(alias: str, color: str, *, available: bool = True) -> str:
    """Render an alias with the command head in ``color`` and any
    argument tail dimmed in the same colour.

    ``"/rename <id> <title>"`` -> ``"/rename"`` rendered in full
    ``color`` plus ``"<id> <title>"`` in ``[dim <color>]``. The
    eye lands on the verb first; the placeholder text stays
    legible-but-recessed so it reads as a usage hint, not as
    something to focus on.

    Aliases without arguments (``"/new"``, ``"/help"``) just get
    the single-colour span; the split is a no-op for them.

    When ``available`` is ``False`` (catalog entry the current
    robot does not advertise) the whole alias renders with
    ``strike dim <color>``. Strikethrough is the universal "not
    available" cue; keeping the slot colour preserves the visual
    grouping so an operator can tell at a glance which subsystem
    is currently offline (e.g. ``/time`` and ``/calc`` struck
    through in blue means the tool-execution layer is not up).
    """
    parts = alias.split(maxsplit=1)
    if not available:
        style = f"strike dim {color}"
        return f"[{style}]{alias}[/{style}]"
    if len(parts) == 1:
        return f"[{color}]{alias}[/{color}]"
    head, tail = parts
    return f"[{color}]{head}[/{color}] [dim {color}]{tail}[/dim {color}]"


def _action_supported(action: str, state: _ClientState) -> bool:
    """Check whether ``action`` is currently offered by the robot.

    Walks the catalog to learn which manifest slot the action belongs
    to, then asks the matching live set. Returns ``False`` for an
    action the catalog does not know (a future client tried to send
    a command we have no entry for).
    """
    for _alias, catalog_action, slot, _hint in _SLASH_CATALOG:
        if catalog_action == action:
            return action in _actions_for_slot(state, slot)
    return False


def _absorb_capabilities(
    data: dict[str, Any], state: _ClientState
) -> None:
    """Latch the relevant slots of a ``capabilities`` frame into state.

    Mutates the state in place; does not render anything. The
    print loop calls :func:`_render_capability_lines` separately,
    which uses :attr:`_ClientState.last_capability_signature` to
    skip work when nothing actually changed (the server republishes
    on every ComponentLifecycle, even those that left the manifest
    untouched).
    """
    commands = data.get("commands") or []
    tools = data.get("tools") or []
    models = data.get("models") or []
    state.command_actions = {
        c.get("action")
        for c in commands
        if isinstance(c, dict) and c.get("action")
    }
    state.tool_actions = {
        t.get("action")
        for t in tools
        if isinstance(t, dict) and t.get("action")
    }
    state.models_by_component = {
        m.get("owner_component", ""): m.get("model_id", "")
        for m in models
        if isinstance(m, dict) and m.get("model_id")
    }


def _render_capability_lines(
    console: Console, state: _ClientState
) -> None:
    """Render the ``commands:`` and ``tools:`` lines if changed.

    Two-line layout (user-requested):

        commands: /new, /sessions, /open, /rename · /time, /calc · /help /exit
        tools:    current_time, calculate, list_directory, ...

    The ``commands`` line groups slash aliases by origin, each
    group in its own colour so the eye can spot "what kind of
    command is this":

    - **Chat ops** (``slot=commands`` in the catalog, advertised
      by the kernel) -- rendered ``dim`` because they're the
      default workflow surface.
    - **Tools** (``slot=tools``, advertised by the tool-execution
      layer) -- ``bright_cyan`` so they stand out as "this calls
      something remote that does real work".
    - **Local meta** (``/help``, ``/exit``) -- ``bright_magenta``
      because they're always-on, never gated by the manifest, and
      visually distinct from anything the robot offers.

    The three groups are separated by `` · `` so the structure
    "chat · tools · meta" is unambiguous regardless of which
    groups happen to be empty.

    The ``tools`` line below lists the raw tool action names the
    robot has mounted -- informational. The slash aliases above
    are how the user actually invokes them.

    Always renders the meta group even when the robot offers no
    remote commands, so the user always has a reminder of the
    local exit path.
    """
    # Walk the full catalog this time, not just available actions:
    # missing entries are rendered struck-through so the user sees
    # what the CLI knows about but the robot is not currently
    # offering. Empty groups (no catalog entries at all for that
    # slot) still get suppressed.
    chat_entries = [
        (alias, action in state.command_actions)
        for alias, action, slot, _ in _SLASH_CATALOG
        if slot == "commands"
    ]
    tool_entries = [
        (alias, action in state.tool_actions)
        for alias, action, slot, _ in _SLASH_CATALOG
        if slot == "tools"
    ]
    tools = sorted(state.tool_actions)
    # The signature carries both the alias AND its current
    # availability so a toggle (layer drops, layer re-mounts)
    # re-renders even though the alias set itself is constant.
    signature = (tuple(chat_entries), tuple(tool_entries), tuple(tools))
    if signature == state.last_capability_signature:
        return
    state.last_capability_signature = signature

    groups: list[str] = []
    if chat_entries:
        # Bright cyan for chat ops -- the primary interaction
        # surface. Sits front-and-centre against the cooler blue
        # tool colour.
        groups.append(
            ", ".join(
                _styled_alias(a, "bright_cyan", available=avail)
                for a, avail in chat_entries
            )
        )
    if tool_entries:
        # Catppuccin Mocha "Blue" (#89B4FA) for tools. Picked over
        # plain ANSI bright_blue (too dark on most dark terminals)
        # and over Rich's cornflower_blue because the Catppuccin
        # palette is explicitly tuned to coexist with its Sky
        # (cyan) and Mauve (magenta) -- exactly the chat / tools
        # / meta triad this CLI renders.
        groups.append(
            ", ".join(
                _styled_alias(a, "#89B4FA", available=avail)
                for a, avail in tool_entries
            )
        )
    # Local meta commands always shown -- they're never gated by
    # the manifest. Rendered in a distinct colour so they read as
    # "client-side, not robot-side". No arguments on these, so a
    # single colour span is enough.
    groups.append("[bright_magenta]/help /exit[/bright_magenta]")

    # The dots and the ``commands:`` label stay dim so the eye
    # tracks the coloured groups, not the connective tissue.
    console.print(
        "[dim]commands:[/dim] " + "[dim] · [/dim]".join(groups)
    )
    if tools:
        console.print("[dim]tools:    " + ", ".join(tools) + "[/dim]")


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
                    _absorb_capabilities(data, state)
                    _render_capability_lines(console, state)
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
                        # Enrich e.g. ``kernel.chat`` with the active
                        # model when known. ``source`` carries the
                        # component prefix (``kernel.<name>``); the
                        # state model map is keyed by component name
                        # (``"chat"``), so strip the ``kernel.``
                        # prefix to look it up.
                        component_name = source.split(".", 1)[1] if source.startswith("kernel.") else source
                        model = state.models_by_component.get(component_name)
                        subtitle_parts.append(
                            f"{source} ({model})" if model else source
                        )
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
        if parsed.kind == "exit":
            # Symmetric with the EOF (Ctrl-D / Ctrl-Z) exit path:
            # leave the input loop cleanly so the outer ``_run``
            # closes the websocket and prints the goodbye banner.
            break
        if parsed.kind == "error":
            console.print(f"[red]{parsed.error}[/]")
            continue

        if ws.closed:
            console.print("[red]connection closed[/]")
            break

        if parsed.kind == "command":
            if not _action_supported(parsed.action, state):
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
