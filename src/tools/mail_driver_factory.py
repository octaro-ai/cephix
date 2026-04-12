"""Factory for building a Mail ToolDriver backed by the MCS mail driver.

Credentials are resolved through Cephix's secret system so they never
appear in prompts or tool schemas.  The MCS ``MailToolDriver`` handles
the actual IMAP/SMTP communication.

Usage::

    driver = build_mail_driver(
        secret_resolver=lambda key: read_secret(key, instance_env, global_fallback=global_env),
        mail_config=robot_cfg.get("mail", {}),
    )
    # driver is a standard ToolDriverPort (MCSToolDriverAdapter)
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from src.tools.mcs_adapter import MCSToolDriverAdapter

logger = logging.getLogger(__name__)

MAIL_RISK_OVERRIDES: dict[str, str] = {
    "list_folders": "read_only",
    "list_messages": "read_only",
    "fetch_message": "read_only",
    "search_messages": "read_only",
    "move_message": "low_risk_mutation",
    "set_flags": "low_risk_mutation",
    "create_folder": "low_risk_mutation",
    "send_message": "high_risk_mutation",
    "send_html_message": "high_risk_mutation",
}

MAIL_CONTEXT_MAPPING: dict[str, dict[str, str]] = {
    "move_message": {"source": "source_folder", "target": "destination_folder"},
    "set_flags": {"target": "uid"},
    "create_folder": {"target": "folder_name"},
    "send_message": {"target": "to"},
    "send_html_message": {"target": "to"},
}


def build_mail_driver(
    *,
    secret_resolver: Callable[[str], str],
    mail_config: dict[str, Any] | None = None,
    namespace: str = "mail",
) -> MCSToolDriverAdapter | None:
    """Build a mail driver from robot.yaml config and secrets.

    Returns ``None`` if the MCS mail driver package is not installed or
    if required configuration is missing.

    Expected ``mail_config`` structure in robot.yaml::

        mail:
          read:
            host: imap.example.com
            user_env: MAIL_READ_USER        # resolved via secret_resolver
            password_env: MAIL_READ_PASSWORD
            port: 993
            ssl: true
          send:
            host: smtp.example.com
            user_env: MAIL_SEND_USER
            password_env: MAIL_SEND_PASSWORD
            port: 587
            starttls: true
    """
    try:
        from mcs.driver.mail import MailToolDriver
    except ImportError:
        logger.info("mcs-driver-mail not installed -- mail tools unavailable")
        return None

    cfg = mail_config or {}
    read_cfg = cfg.get("read", {})
    send_cfg = cfg.get("send", {})

    if not read_cfg.get("host"):
        logger.info("No mail.read.host configured -- mail tools unavailable")
        return None

    read_kwargs = _resolve_connection(read_cfg, secret_resolver, prefix="read")
    send_kwargs = _resolve_connection(send_cfg, secret_resolver, prefix="send")

    read_adapter = read_cfg.get("adapter", "imap")
    send_adapter = send_cfg.get("adapter", "smtp")

    mcs_driver = MailToolDriver(
        read_adapter=read_adapter,
        send_adapter=send_adapter,
        read_kwargs=read_kwargs,
        send_kwargs=send_kwargs,
    )

    return MCSToolDriverAdapter(
        driver=mcs_driver,
        namespace=namespace,
        risk_overrides=MAIL_RISK_OVERRIDES,
        context_mappings=MAIL_CONTEXT_MAPPING,
    )


def _resolve_connection(
    cfg: dict[str, Any],
    secret_resolver: Callable[[str], str],
    prefix: str,
) -> dict[str, Any]:
    """Build kwargs for a single connection (read or send)."""
    kwargs: dict[str, Any] = {}

    if cfg.get("host"):
        kwargs["host"] = cfg["host"]

    user_env = cfg.get("user_env", f"MAIL_{prefix.upper()}_USER")
    password_env = cfg.get("password_env", f"MAIL_{prefix.upper()}_PASSWORD")

    user = secret_resolver(user_env)
    password = secret_resolver(password_env)

    if user:
        kwargs["user"] = user
    if password:
        kwargs["password"] = password

    if cfg.get("port"):
        kwargs["port"] = int(cfg["port"])
    if "ssl" in cfg:
        kwargs["ssl"] = bool(cfg["ssl"])
    if "starttls" in cfg:
        kwargs["starttls"] = bool(cfg["starttls"])
    if cfg.get("sender"):
        kwargs["sender"] = cfg["sender"]

    return kwargs
