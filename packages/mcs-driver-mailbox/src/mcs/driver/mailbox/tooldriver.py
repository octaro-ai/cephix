"""``MailboxToolDriver`` -- in-process MCS ToolDriver for mailbox tools.

The MCS bottom-up workflow (port -> adapter -> tooldriver) only
earns its keep when a transport layer is involved (IMAP, JMAP,
HTTP, SMB, ...). This driver stays *entirely in-process*: it
returns dummy messages from a constant table. No adapter is
needed because there is no backend to talk to; the ToolDriver
itself implements ``execute_tool`` directly.

The day we wire a real mailbox, that work becomes "ship an
``mcs-adapter-imap`` (or similar) that satisfies a freshly
introduced ``MailboxAdapterPort`` in this package, and switch
this ToolDriver to delegate to it." Not before.
"""

from __future__ import annotations

from typing import Any

from mcs.driver.core import (
    DriverBinding,
    DriverMeta,
    MCSToolDriver,
    Tool,
    ToolParameter,
)


_TOOL_FETCH_UNREAD = "mailbox.fetch_unread"


class MailboxToolDriver(MCSToolDriver):
    """ToolDriver exposing dummy mailbox operations.

    No constructor arguments: there is no transport to configure
    yet. ``list_tools`` advertises ``mailbox.fetch_unread`` and
    ``execute_tool`` returns a deterministic batch of fake messages
    so the bus -> tool -> response round-trip can be exercised
    end-to-end without a mail server.
    """

    meta = DriverMeta(
        id="mcs.driver.mailbox.v1",
        name="Mailbox ToolDriver",
        version="0.1.0",
        bindings=(
            DriverBinding(
                capability="mailbox",
                adapter="*",
                spec_format="Custom",
            ),
        ),
        supported_llms=None,
        capabilities=(),
    )

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=_TOOL_FETCH_UNREAD,
                title="Fetch unread mails",
                description=(
                    "Return the most recent unread messages from a "
                    "mailbox, newest first, up to ``limit`` entries. "
                    "Each message is a mapping with ``id``, ``from``, "
                    "``subject`` and ``snippet``."
                ),
                parameters=[
                    ToolParameter(
                        name="mailbox_id",
                        description=(
                            "Identifier of the mailbox to read. "
                            "Any non-empty string in the in-process "
                            "stub."
                        ),
                        required=False,
                        schema={"type": "string"},
                    ),
                    ToolParameter(
                        name="limit",
                        description=(
                            "Maximum number of messages to return. "
                            "Clamped into 1-50."
                        ),
                        required=False,
                        schema={
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 5,
                        },
                    ),
                ],
            ),
        ]

    def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        if tool_name != _TOOL_FETCH_UNREAD:
            raise ValueError(
                f"MailboxToolDriver: unknown tool {tool_name!r}; "
                f"available: {[t.name for t in self.list_tools()]}"
            )
        mailbox_id = str(arguments.get("mailbox_id", "stub-mailbox"))
        raw_limit = arguments.get("limit")
        if raw_limit is None:
            raw_limit = 5
        limit = max(1, min(int(raw_limit), 50))
        return {
            "mailbox_id": mailbox_id,
            "messages": [
                {
                    "id": f"stub-msg-{i}",
                    "mailbox_id": mailbox_id,
                    "from": f"sender{i}@example.com",
                    "subject": f"Stub message {i}",
                    "snippet": f"This is dummy mail body number {i}.",
                }
                for i in range(1, limit + 1)
            ],
        }
