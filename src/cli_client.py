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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

import aiohttp
from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)

DEFAULT_URL = "ws://127.0.0.1:8765/ws"


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


async def _print_loop(
    ws: aiohttp.ClientWebSocketResponse,
    console: Console,
    response_done: asyncio.Event,
    identity: _RobotIdentity,
) -> None:
    """Render server frames; release ``response_done`` after each output."""
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

        text = user_input.strip()
        if not text:
            continue

        if ws.closed:
            console.print("[red]connection closed[/]")
            break

        response_done.clear()
        try:
            await ws.send_json({"type": "input", "message": text})
        except (aiohttp.ClientConnectionError, ConnectionResetError) as exc:
            console.print(f"[red]connection lost:[/] {exc}")
            break

        await response_done.wait()


async def _run(url: str) -> None:
    console = Console()
    console.print(
        "[bold]cephix client[/] · type message and hit Enter, Ctrl-D / Ctrl-Z to exit"
    )
    console.print(f"[dim]connecting to {url} ...[/]")

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.ws_connect(url) as ws:
                response_done = asyncio.Event()
                identity = _RobotIdentity()
                printer = asyncio.create_task(
                    _print_loop(ws, console, response_done, identity),
                    name="ws.printer",
                )
                try:
                    await _input_loop(ws, console, response_done)
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
