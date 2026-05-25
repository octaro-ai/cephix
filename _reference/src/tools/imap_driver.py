"""IMAP-based mail tool driver with secret isolation.

Credentials are resolved once at initialization via a ``secret_resolver``
callable and are never exposed in tool definitions or schemas.
The LLM only sees tool names and parameter descriptions -- no passwords.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any, Callable

from src.domain import ExecutionContext
from src.tools.models import ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)


def _decode_header(raw: str) -> str:
    parts = email.header.decode_header(raw)
    decoded: list[str] = []
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            decoded.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(fragment)
    return " ".join(decoded)


def _body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


_TOOL_DEFS: list[ToolDefinition] = [
    ToolDefinition(
        name="mail.list",
        description="List messages in a mailbox folder",
        parameters=[
            ToolParameter(name="folder", type="string", description="IMAP folder name", required=False),
            ToolParameter(name="limit", type="integer", description="Max messages to return", required=False),
            ToolParameter(name="unread_only", type="boolean", description="Only unread messages", required=False),
        ],
        metadata={"risk_class": "read_only"},
    ),
    ToolDefinition(
        name="mail.read",
        description="Read a single message by UID",
        parameters=[
            ToolParameter(name="uid", type="string", description="Message UID"),
            ToolParameter(name="folder", type="string", description="IMAP folder name", required=False),
        ],
        metadata={"risk_class": "read_only"},
    ),
    ToolDefinition(
        name="mail.move",
        description="Move a message to a different folder",
        parameters=[
            ToolParameter(name="uid", type="string", description="Message UID"),
            ToolParameter(name="target", type="string", description="Target folder name"),
            ToolParameter(name="source", type="string", description="Source folder name", required=False),
        ],
        metadata={"risk_class": "low_risk_mutation"},
    ),
    ToolDefinition(
        name="mail.flag",
        description="Add or remove a flag on a message",
        parameters=[
            ToolParameter(name="uid", type="string", description="Message UID"),
            ToolParameter(name="flag", type="string", description="Flag name (e.g. \\Seen, \\Flagged)"),
            ToolParameter(name="action", type="string", description="'add' or 'remove'", required=False),
        ],
        metadata={"risk_class": "low_risk_mutation"},
    ),
    ToolDefinition(
        name="mail.archive",
        description="Move a message to the Archive folder",
        parameters=[
            ToolParameter(name="uid", type="string", description="Message UID"),
            ToolParameter(name="source", type="string", description="Source folder name", required=False),
        ],
        metadata={"risk_class": "low_risk_mutation"},
    ),
    ToolDefinition(
        name="mail.delete",
        description="Delete a message (move to Trash)",
        parameters=[
            ToolParameter(name="uid", type="string", description="Message UID"),
            ToolParameter(name="source", type="string", description="Source folder name", required=False),
        ],
        metadata={"risk_class": "high_risk_mutation"},
    ),
    ToolDefinition(
        name="mail.send",
        description="Send an email message",
        parameters=[
            ToolParameter(name="to", type="string", description="Recipient email address"),
            ToolParameter(name="subject", type="string", description="Email subject"),
            ToolParameter(name="body", type="string", description="Email body text"),
        ],
        metadata={"risk_class": "high_risk_mutation"},
    ),
]


class IMAPToolDriver:
    """Tool driver that speaks IMAP/SMTP with secret isolation.

    The ``secret_resolver`` is a callable ``(key: str) -> str`` that reads
    secrets from the robot's env files.  Credentials never appear in tool
    schemas.
    """

    def __init__(
        self,
        *,
        secret_resolver: Callable[[str], str],
        imap_host_key: str = "IMAP_HOST",
        imap_port_key: str = "IMAP_PORT",
        imap_user_key: str = "IMAP_USER",
        imap_pass_key: str = "IMAP_PASSWORD",
        smtp_host_key: str = "SMTP_HOST",
        smtp_port_key: str = "SMTP_PORT",
        smtp_user_key: str = "SMTP_USER",
        smtp_pass_key: str = "SMTP_PASSWORD",
        smtp_from_key: str = "SMTP_FROM",
        archive_folder: str = "Archive",
        trash_folder: str = "Trash",
    ) -> None:
        self._resolve = secret_resolver
        self._imap_host_key = imap_host_key
        self._imap_port_key = imap_port_key
        self._imap_user_key = imap_user_key
        self._imap_pass_key = imap_pass_key
        self._smtp_host_key = smtp_host_key
        self._smtp_port_key = smtp_port_key
        self._smtp_user_key = smtp_user_key
        self._smtp_pass_key = smtp_pass_key
        self._smtp_from_key = smtp_from_key
        self._archive_folder = archive_folder
        self._trash_folder = trash_folder

    def list_tools(self) -> list[ToolDefinition]:
        return list(_TOOL_DEFS)

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        dispatch = {
            "mail.list": self._mail_list,
            "mail.read": self._mail_read,
            "mail.move": self._mail_move,
            "mail.flag": self._mail_flag,
            "mail.archive": self._mail_archive,
            "mail.delete": self._mail_delete,
            "mail.send": self._mail_send,
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            raise RuntimeError(f"IMAPToolDriver has no handler for: {tool_name!r}")
        return handler(arguments)

    def _connect_imap(self) -> imaplib.IMAP4_SSL:
        host = self._resolve(self._imap_host_key)
        port = int(self._resolve(self._imap_port_key) or "993")
        user = self._resolve(self._imap_user_key)
        password = self._resolve(self._imap_pass_key)
        if not host or not user:
            raise RuntimeError("IMAP credentials not configured")
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(user, password)
        return conn

    def _mail_list(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        folder = args.get("folder", "INBOX")
        limit = int(args.get("limit", 20))
        unread_only = args.get("unread_only", False)

        conn = self._connect_imap()
        try:
            conn.select(folder, readonly=True)
            criteria = "(UNSEEN)" if unread_only else "ALL"
            _, data = conn.uid("search", None, criteria)
            uids = (data[0] or b"").split()
            uids = uids[-limit:] if limit else uids

            results: list[dict[str, Any]] = []
            for uid in uids:
                _, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] FLAGS)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                msg = email.message_from_bytes(raw if isinstance(raw, bytes) else raw.encode())
                flags_raw = msg_data[0][0] if isinstance(msg_data[0], tuple) else b""
                results.append({
                    "uid": uid.decode(),
                    "from": _decode_header(msg.get("From", "")),
                    "subject": _decode_header(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
                    "unread": b"\\Seen" not in flags_raw,
                })
            return results
        finally:
            conn.close()
            conn.logout()

    def _mail_read(self, args: dict[str, Any]) -> dict[str, Any]:
        uid = str(args["uid"])
        folder = args.get("folder", "INBOX")

        conn = self._connect_imap()
        try:
            conn.select(folder, readonly=True)
            _, msg_data = conn.uid("fetch", uid.encode(), "(RFC822)")
            if not msg_data or not msg_data[0]:
                return {"error": f"Message {uid} not found"}
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
            msg = email.message_from_bytes(raw if isinstance(raw, bytes) else raw.encode())
            return {
                "uid": uid,
                "from": _decode_header(msg.get("From", "")),
                "to": _decode_header(msg.get("To", "")),
                "subject": _decode_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": _body_text(msg)[:5000],
            }
        finally:
            conn.close()
            conn.logout()

    def _mail_move(self, args: dict[str, Any]) -> dict[str, str]:
        uid = str(args["uid"])
        target = str(args["target"])
        source = args.get("source", "INBOX")

        conn = self._connect_imap()
        try:
            conn.select(source)
            conn.uid("copy", uid.encode(), target)
            conn.uid("store", uid.encode(), "+FLAGS", "(\\Deleted)")
            conn.expunge()
            return {"status": "moved", "uid": uid, "target": target}
        finally:
            conn.close()
            conn.logout()

    def _mail_flag(self, args: dict[str, Any]) -> dict[str, str]:
        uid = str(args["uid"])
        flag = str(args["flag"])
        action = args.get("action", "add")
        folder = args.get("folder", "INBOX")

        conn = self._connect_imap()
        try:
            conn.select(folder)
            op = "+FLAGS" if action == "add" else "-FLAGS"
            conn.uid("store", uid.encode(), op, f"({flag})")
            return {"status": "flagged", "uid": uid, "flag": flag, "action": action}
        finally:
            conn.close()
            conn.logout()

    def _mail_archive(self, args: dict[str, Any]) -> dict[str, str]:
        return self._mail_move({
            "uid": args["uid"],
            "target": self._archive_folder,
            "source": args.get("source", "INBOX"),
        })

    def _mail_delete(self, args: dict[str, Any]) -> dict[str, str]:
        return self._mail_move({
            "uid": args["uid"],
            "target": self._trash_folder,
            "source": args.get("source", "INBOX"),
        })

    def _mail_send(self, args: dict[str, Any]) -> dict[str, str]:
        to_addr = str(args["to"])
        subject = str(args["subject"])
        body = str(args["body"])

        smtp_host = self._resolve(self._smtp_host_key)
        smtp_port = int(self._resolve(self._smtp_port_key) or "587")
        smtp_user = self._resolve(self._smtp_user_key)
        smtp_pass = self._resolve(self._smtp_pass_key)
        from_addr = self._resolve(self._smtp_from_key) or smtp_user

        if not smtp_host or not smtp_user:
            raise RuntimeError("SMTP credentials not configured")

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = subject

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        return {"status": "sent", "to": to_addr, "subject": subject}
