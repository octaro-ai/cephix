# TODO

- Add an optional delivery-override tool so the LLM can request a channel switch without coupling the kernel to concrete channels.
- Decide whether delivery override should be injected through robot construction or dynamically through the configured tool execution layer.
- Keep the current default simple:
  - use `event.reply_target` for reply-capable inputs
  - otherwise fall back to the robot's configured `default_output_target`
- Revisit dynamic tool availability once a real MCS/MCP-backed `ToolExecutionPort` is integrated.
- Mid term:
  - wire the persistent event, episode, profile, and procedure stores into the runtime path
  - add a memory manager that assembles planning context from firmware plus selected memory layers
  - add policy-controlled memory updates so the robot can suggest firmware changes without mutating human-owned files directly
- Long term:
  - define a formal robot brain package for cloning, transfer, and tenant-safe distribution
  - support backend adapters for alternative memory implementations without changing robot internals
  - add memory classification for cloneable, tenant-bound, and sensitive knowledge
