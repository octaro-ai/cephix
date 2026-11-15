# ADR-0007 - Robot home vs. workspace: a sandboxed file area for tools

- Status: accepted
- Date: 2026-06-02

## Context

A robot's on-disk directory (`~/.cephix/robots/<slug>/`) was called the
**workspace** throughout the code and docs. That single term covered two
genuinely different things:

1. The bot's **whole** presence: config (`robot.yaml`), secrets (`.env`),
   and all internal state (`logs/`, `sessions/`, `firmware/`, `configs/`).
2. A place to put files the robot should be able to **read and write as part
   of its job**.

There was no separation between the two. Concretely, the MCS tool-execution
layer was wired with the **default persistence `FilesystemConnection`**, whose
root is that same directory. So the LLM's filesystem tools were rooted at the
entire robot tree: a tool call could read `.env`, read or rewrite `firmware/`
(the system prompt), and read `logs/`. The machinery of the bot and the files
the bot operates on shared one unguarded root.

The naming also collided conceptually with `robots_root()` (the directory of
*all* robots) and with the OS metaphor the project already uses (firmware,
boot priorities, lifecycle), where a per-robot "home directory" is the natural
term.

## Decision

**Split the concept in two, by name and by connection.**

- The container directory is the **robot home** (`robot_home` in code,
  `RobotInstance.home`, `default_robot_home_for()`). It holds the bot's
  machinery, exactly as before.
- A dedicated **`workspace/`** sub-directory (`<robot_home>/workspace/`,
  `ROBOT_WORKSPACE_DIRNAME` / `robot_workspace_path()`) is the **sandbox the
  filesystem tools are rooted at** - and nothing else roots there.

The builder constructs a **separate** `FilesystemConnection` rooted at
`<robot_home>/workspace/` and injects *that* into the tool-execution layer,
instead of the persistence connection. The persistence connection still
anchors at the robot home (so `logs/`, `sessions/`, `firmware/`, `configs/`
are unchanged). Without a robot home there is no sandbox, so the tool layer
boots without filesystem tools.

The `cephix.yaml#robots[]` index key for an out-of-convention location is
renamed `workspace:` -> `home:`; the legacy `workspace:` key is still read so
existing host configs keep resolving.

## Consequences

What gets easier / safer:

- **The file tool is sandboxed.** An LLM tool call can read and write the
  robot's working files under `workspace/` but cannot reach `.env`, rewrite
  `firmware/`, or read `logs/`. The machinery/working-files boundary is now a
  filesystem-root boundary, not a convention.
- **Terminology is unambiguous.** `robot_home` (the whole tree) vs.
  `workspace/` (the tool sandbox) vs. `robots_root()` (all robots) no longer
  overload one word.

What we accept:

- **Two `FilesystemConnection` instances** in the default config: one rooted
  at the robot home (persistence - telemetry, audit, sessions, firmware,
  configs), one at `<robot_home>/workspace/` (the file tool). Declaring extra
  `persistence:` entries adds more persistence connections; the workspace
  connection stays exactly one (or zero without a home).
- **The workspace connection is not a lifecycle component.** It is a passive
  object held by the tool layer, because the `MCSFilesystemAdapter` reaches
  the filesystem synchronously via `pathlib` (`connection.root` /
  `connection.resolve()`) and never calls the connection's async methods or
  `start()`. The `workspace/` directory is therefore created by `cephix init`
  and `cephix start`, not by a connection boot step.
- **A persisted-key rename** with a back-compat read path to maintain.

## Relationship to other ADRs

- [ADR-0005 - Robot per user](0005-robot-per-user-and-multi-mailbox-tenancy.md):
  the per-user `~/.cephix/robots/<user>/` directory is that robot's home.
- [ADR-0004 - Credential layers](0004-credential-layers.md): the bot-local
  `.env` lives at the robot-home root, deliberately outside the tool sandbox.
