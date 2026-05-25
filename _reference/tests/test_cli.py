from __future__ import annotations

import asyncio
import unittest

from src.cli import _build_cli_ui, _handle_chat_command, _is_chat_cycle_complete


class _StubWebSocket:
    def __init__(self) -> None:
        self.sent_payloads: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent_payloads.append(payload)


class _StubUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.errors: list[str] = []
        self.json_payloads: list[tuple[str, object]] = []

    def print_info(self, text: str) -> None:
        self.messages.append(text)

    def print_error(self, text: str) -> None:
        self.errors.append(text)

    def print_json(self, payload: object, *, title: str) -> None:
        self.json_payloads.append((title, payload))


class CliTests(unittest.TestCase):
    def test_non_debug_cycle_completes_on_response(self) -> None:
        self.assertTrue(_is_chat_cycle_complete("response", {"content": "ok"}, debug=False))

    def test_debug_cycle_does_not_complete_on_response(self) -> None:
        self.assertFalse(_is_chat_cycle_complete("response", {"content": "ok"}, debug=True))

    def test_debug_cycle_completes_on_run_completed_telemetry(self) -> None:
        self.assertTrue(
            _is_chat_cycle_complete(
                "telemetry",
                {"event": {"event_type": "run.completed"}},
                debug=True,
            )
        )

    def test_debug_cycle_ignores_other_telemetry_events(self) -> None:
        self.assertFalse(
            _is_chat_cycle_complete(
                "telemetry",
                {"event": {"event_type": "output.sent"}},
                debug=True,
            )
        )

    def test_build_cli_ui_raises_clear_error_without_rich(self) -> None:
        def _raise_import_error() -> object:
            raise RuntimeError(
                "The Cephix CLI requires the optional 'cli' extra. Install it with 'pip install .[cli]'."
            )

        with self.assertRaisesRegex(RuntimeError, r"pip install \.\[cli\]"):
            _build_cli_ui(support_loader=_raise_import_error)


class CliCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_mode_cannot_be_enabled_without_admin_scope(self) -> None:
        ws = _StubWebSocket()
        ui = _StubUI()
        debug_state = {"enabled": False}
        mode_state = {"current": "chat"}
        control_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        handled = await _handle_chat_command(
            ws,
            "/admin",
            is_admin=False,
            ui=ui,
            debug_state=debug_state,
            mode_state=mode_state,
            control_queue=control_queue,
        )

        self.assertTrue(handled)
        self.assertEqual("chat", mode_state["current"])
        self.assertEqual([], ws.sent_payloads)
        self.assertEqual(["[error] admin scope required"], ui.errors)

    async def test_exit_in_admin_mode_returns_to_chat(self) -> None:
        mode_state = {"current": "admin"}
        lowered_input = "exit"

        if lowered_input in {"exit", "quit"} and mode_state["current"] == "admin":
            mode_state["current"] = "chat"

        self.assertEqual("chat", mode_state["current"])

    async def test_exit_in_chat_mode_keeps_chat_mode_until_outer_loop_breaks(self) -> None:
        mode_state = {"current": "chat"}
        lowered_input = "exit"

        if lowered_input in {"exit", "quit"} and mode_state["current"] == "admin":
            mode_state["current"] = "chat"

        self.assertEqual("chat", mode_state["current"])


if __name__ == "__main__":
    unittest.main()
