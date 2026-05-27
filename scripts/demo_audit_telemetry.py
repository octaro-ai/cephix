"""End-to-end demo for the telemetry + audit pipeline.

Boots a minimal cephix robot in a temporary workspace, talks to it
over its WebSocket channel for a few rounds, also drops one
hand-written :class:`RobotAuditNote`, then shuts down cleanly and
prints the resulting ``logs/telemetry.jsonl`` and
``logs/audit.jsonl`` files.

Run with::

    python scripts/demo_audit_telemetry.py

No external service is contacted; the demo binds to ``127.0.0.1``
on an OS-assigned port and runs entirely in the same process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

import aiohttp

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.builder import build_robot_from_config  # noqa: E402
from src.bus.messages import AUDIT_TOPIC, RobotAuditNote  # noqa: E402
from src.channels.websocket import WebsocketChannel  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("demo")


ROBOT_YAML = {
    "id": "demo",
    "name": "Demo Bot",
    "kernel": {"type": "base", "actor_timeout": 5.0},
    "actor": {"type": "echo", "prefix": "echo: "},
    "channels": [
        {"type": "websocket", "host": "127.0.0.1", "port": 0, "path": "/ws"}
    ],
    "control_plane": {"enabled": False},
}


async def _talk(port: int, messages: list[str]) -> list[dict]:
    received: list[dict] = []
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(f"ws://127.0.0.1:{port}/ws") as ws:
            welcome = await ws.receive_json()
            logger.info("welcome: %s", welcome)
            received.append(welcome)
            for text in messages:
                await ws.send_json({"type": "input", "text": text})
                reply = await ws.receive_json()
                logger.info("-> %s", reply.get("text"))
                received.append(reply)
    return received


def _print_jsonl(label: str, path: Path) -> None:
    print()
    print(f"=== {label} ({path}) ===")
    if not path.exists():
        print("  <file not created>")
        return
    raw = path.read_text(encoding="utf-8").splitlines()
    if not raw:
        print("  <empty>")
        return
    for line in raw:
        if not line.strip():
            continue
        record = json.loads(line)
        topic = record.get("topic", "?")
        event_type = record.get("event_type", "?")
        text = record.get("text") or record.get("action") or ""
        print(f"  {event_type:<18} topic={topic:<22} {text}")


async def _run_demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        logger.info("workspace = %s", workspace)

        robot = build_robot_from_config(dict(ROBOT_YAML), workspace=workspace)
        async with robot:
            ws_channel = next(
                c for c in robot.components if isinstance(c, WebsocketChannel)
            )
            port = ws_channel.actual_port
            assert port is not None
            logger.info("websocket bound to ws://127.0.0.1:%s/ws", port)

            await _talk(
                port,
                [
                    "Hello robot",
                    "How are you?",
                    "Goodbye",
                ],
            )

            assert robot.bus is not None
            await robot.bus.publish(
                RobotAuditNote(
                    topic=AUDIT_TOPIC,
                    principal="demo:script",
                    source="demo",
                    run_id="demo-run-1",
                    actor="demo-script",
                    action="smoke.test",
                    details={"why": "verifying audit pipeline"},
                )
            )
            await asyncio.sleep(0.05)

        logs_dir = workspace / "logs"
        _print_jsonl("telemetry.jsonl", logs_dir / "telemetry.jsonl")
        _print_jsonl("audit.jsonl", logs_dir / "audit.jsonl")


def main() -> int:
    asyncio.run(_run_demo())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
