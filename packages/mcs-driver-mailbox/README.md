# mcs-driver-mailbox

`MailboxToolDriver` -- an MCS ToolDriver that exposes mailbox operations as LLM-callable tools.

Today's surface: one tool, `mailbox.fetch_unread`, that returns a fixed batch of dummy messages. No backend, no transport -- pure in-process. Useful for exercising the bus -> tool -> response path before a real mail backend exists.

When a real mailbox backend lands (IMAP, JMAP, Microsoft Graph, ...), the right move is to introduce a `MailboxAdapterPort` in this package and ship the backend as a separate `mcs-adapter-*` package the ToolDriver delegates to. Until then the ToolDriver implements `execute_tool` directly.

## Usage

```python
from mcs.driver.mailbox import MailboxToolDriver

driver = MailboxToolDriver()
print([t.name for t in driver.list_tools()])
# ['mailbox.fetch_unread']

result = driver.execute_tool("mailbox.fetch_unread", {"mailbox_id": "team-sales", "limit": 3})
# {'mailbox_id': 'team-sales', 'messages': [{'id': 'stub-msg-1', ...}, ...]}
```

The driver follows the MCS convention: install the package, import from `mcs.driver.mailbox`, hand the driver instance to any orchestrator or directly to the MCS tool-execution layer.
