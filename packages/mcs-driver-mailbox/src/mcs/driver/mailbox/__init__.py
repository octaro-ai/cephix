"""``mcs.driver.mailbox`` -- mailbox tool driver.

Single tool today (``mailbox.fetch_unread``), in-process. No
transport layer; the ToolDriver is fully self-contained. When a
real mailbox backend arrives, this package will introduce a
``MailboxAdapterPort`` here and the actual backend ships as a
separate ``mcs-adapter-*`` package the driver delegates to.
"""

from mcs.driver.mailbox.tooldriver import MailboxToolDriver

__all__ = ["MailboxToolDriver"]
