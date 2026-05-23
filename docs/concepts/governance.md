# Governance & Approvals

> Governance is the **deterministic layer** that decides whether an action
> may happen, *before* the LLM gets to act. The LLM proposes; governance
> disposes.

## The two questions

Every governance check answers two questions:

1. **Is this action allowed at all?** — based on risk class, system-tool
   status, and prior approval rules.
2. **If not allowed automatically — should we ask the user?** — and if the
   user says "always", remember that for next time.

The result is one of three outcomes, encoded as `GuardDecision`:

```
                ┌─► allow         (proceed with the tool call)
GuardDecision   ─┤
                └─► deny          (return error, no execution)
                 ─► require_approval  (ask the user, defer the call)
```

## Risk classes

Every tool carries a `risk_class` in its metadata:

| Class | Meaning | Default policy |
|---|---|---|
| `read_only` | Reads state, no side effects | Always allowed |
| `low_risk_mutation` | Reversible side effects (move mail, write a note) | Asks for approval if no rule exists |
| `high_risk_mutation` | Irreversible or external side effects (send mail, run shell) | Asks for approval if no rule exists |

The `MetadataRiskClassifier` reads this from `ToolDefinition.metadata` at
mount time. Tool authors set the class once; the guard enforces it everywhere.

## System tools — the bypass

Internal tools like `memory.write`, `notebook.work`, `task.update` are
flagged as `system_tool=true`. They bypass the approval flow because:

- They never touch the outside world.
- They are part of how the harness self-organises.
- Requiring approval for `memory.write` would defeat the point.

The check order in `PolicyToolExecutionGuard.check()`:

```
1. risk_class == read_only?      → allow
2. system_tool == true?          → allow
3. ApprovalStore has a rule?     → use the rule (allow / deny)
4. otherwise                      → require_approval
```

## The approval store

Approval rules live as JSONL in `~/.cephix/robots/<robot_id>/approvals`:

```json
{"principal_id": "owner", "action": "mail.move", "source_scope": null, "target_scope": null, "scope": "once", "granted_by": "owner", "granted_at": "2026-05-22T14:00:00Z", "expires_at": null, "sop_name": null}
{"principal_id": "owner", "action": "mail.send", "source_scope": null, "target_scope": null, "scope": "persistent", "granted_by": "owner", "granted_at": "2026-05-22T14:05:00Z", "expires_at": null, "sop_name": null}
{"principal_id": "owner", "action": "shell.exec", "source_scope": null, "target_scope": null, "scope": "deny", "granted_by": "owner", "granted_at": "2026-05-22T14:10:00Z", "expires_at": null, "sop_name": null}
```

| Field | Purpose |
|---|---|
| `principal_id` | Who the rule applies to (resolved by the `ActorResolver`) |
| `action` | Tool name |
| `source_scope` / `target_scope` | Optional narrowing for source/target arguments |
| `scope` | `once`, `session`, `scoped`, `persistent`, or `deny` |
| `granted_by` / `granted_at` | Audit metadata for who created the rule and when |
| `expires_at` / `sop_name` | Optional expiry and SOP association |

## The approval prompt flow

When the guard returns `require_approval`, the kernel does **not** execute
the tool. Instead, it sends an `ApprovalPrompt` to the user with four buttons:

| Button | Resulting rule |
|---|---|
| **Einmal** / Once | Store a `once` allow rule and consume it on use. |
| **Immer so** / Always | Store a `persistent` allow rule. |
| **Nein** / No | Deny this single call without storing a rule. |
| **Nie so** / Never | Store a `deny` rule. |

User clicks → channel sends back an event with `event_type = "approval.decision"`.
The kernel **short-circuits** this event:

- No LLM call.
- No re-planning.
- Just: write the rule to the store, send a one-line confirmation, emit
  `approval.granted` or `approval.denied`.

On the next run with the same tool and arguments, the guard finds the rule
and lets the call through (or denies it) without a prompt.

See [Architecture › Diagrams](../architecture/diagrams.md#3-sequence-diagram-approval-flow-tool-is-blocked)
for the full sequence.

## Actors and roles

The `ActorResolver` maps an event to an `ActorContext`:

| Role | Who | Example |
|---|---|---|
| `principal` | The owner / authorised operator | The user the robot was initialised for |
| `delegate` | Someone the principal authorised | A colleague using a shared inbox |
| `counterparty` | An external sender | A customer email arriving via IMAP |

Today only basic roles are resolved (`ConfigBasedActorResolver`). The
approval store keys rules by `principal_id`, so different actors can have
different rule sets — useful when an enterprise robot serves multiple users.

## What is wired vs. what is not

The state-of-the-world for governance is summarised here; full breakdown in
[Project › Status](../project/status.md):

| Item | Status |
|---|---|
| `PolicyToolExecutionGuard` (the main check) | Wired |
| `MetadataRiskClassifier` | Wired |
| `FileApprovalStore` (JSONL) | Wired |
| Approval prompt + button → rule | Wired end-to-end |
| `ConfigBasedActorResolver` | Wired (basic roles only) |
| `InputGuardPort` (pre-LLM check) | **Not wired** — port exists, kernel does not call it |
| `OutputGuardPort` (pre-send check) | **Not wired** |
| SOP `safe_actions` as hard policy | **Docs only** — the docstring claims enforcement, the implementation does not |

## Coming next — approval self-learning

The next planned subsystem builds *on top* of the existing approval flow:

- **Generalisation:** when the user clicks "Always" several times for
  similar arguments, propose a more general rule.
- **Confidence scoring:** rules learned from many positive decisions are
  weighted higher than one-off grants.
- **Conflict detection:** flag when a new rule contradicts an existing one.

See [Project › Roadmap](../project/roadmap.md) for the order of work.

## Related

- [Runtime Lifecycle](runtime-lifecycle.md) — where the guard sits in the run
- [SOPs](sops.md) — the `safe_actions` story
- [Architecture › Diagrams](../architecture/diagrams.md) — visual sequences
