# FRD-0005 - Agent workstation (a computer for the robot)

- Status: design / not yet built
- Builds on: [ADR-0007 - Robot home vs. workspace](../adr/0007-robot-home-and-workspace.md) (accepted),
  [ADR-0004 - Credential layers](../adr/0004-credential-layers.md) (accepted)
- Origin: landscape review on 2026-11-15 of how agent projects attach an
  agent to a machine.

If a robot is an employee, the host its process runs on is its body. Like any
employee it must be handed a computer to work on: pre-provisioned, persistent,
its own. This document designs that computer - the **workstation** - and the
layer that makes it irrelevant whether the workstation is the robot's own
host, a container, a VM, a server, or a computer at someone's home.

Two requirements shape the design:

- **CLI first, desktop second.** The robot works through a shell wherever it
  can. A graphical desktop is the fallback, not the default.
- **Browsing that works.** Headless browsers are frequently blocked. The robot
  needs a real, headed browser that it can drive from the CLI, and that it
  (or a human) can also operate directly when automation is not enough.

## Terms

| Term | Meaning |
|---|---|
| Robot host | The machine the robot process runs on (the body). |
| Workstation | The computer the robot works on: shell, files, browser, optionally a desktop. |
| Backend | How the workstation is reached: `local`, `docker`, `ssh`. |
| Recipe | What gets installed so that every workstation looks the same. |
| Provisioner | What creates a machine in the first place (container, VM, cloud server). |

`workspace/` ([ADR-0007](../adr/0007-robot-home-and-workspace.md)) stays what
it is: the file-tool sandbox on the robot host. A workstation has its own
filesystem. How the two relate is an open question below.

## What exists today

**In MCS** (sibling project, `python-sdk/packages/`):

- `mcs-driver-sandbox` defines `SandboxPort`, a six-method protocol:
  `start`, `stop`, `status`, `exec`, `put_file`, `get_file`.
- `SandboxToolDriver` exposes it as tools. Lifecycle tools (`sandbox_start`,
  `sandbox_stop`, `sandbox_status`) are always visible; `shell_exec`,
  `file_put` and `file_get` appear once the sandbox runs.
- `mcs-adapter-docker` implements the port against the Docker Engine API with
  a named volume for persistence. It already accepts `ports` and a
  `home_volume`.
- `mcs-adapter-ssh` implements it over SSH and SFTP (paramiko) for any
  reachable Linux host.

This is already the transparent layer in its core: one driver, exchangeable
backends, persistent by design.

Known limits found while reading the adapters:

- `exec` is one-shot. There is no long-running or interactive process model.
- The Docker adapter accepts `timeout` but does not pass it to the engine, so
  a hanging command blocks. It also returns stderr folded into stdout.
- There is no adapter for the robot's own host. The port docstring names a
  "local subprocess" backend, but none is implemented.

**In Cephix:**

- `MCSToolExecutionLayer` hosts the clock, calculator and filesystem
  ToolDrivers. The sandbox driver is not a dependency and not wired.
- Tool calls already hop to a worker thread (`asyncio.to_thread`), so a
  blocking `shell_exec` does not stall the bus.
- Several docs still describe a `src/workstation/` package with a
  `DockerWorkstationBackend`. That code no longer exists on the bus-centric
  branch; this FRD replaces that concept.

## Landscape (as of 2026-11-15)

| Layer | Projects | Relevance |
|---|---|---|
| Hosted sandboxes | E2B, Modal, Fly Sprites, Daytona, Vercel, Cloudflare, Blaxel, Runloop | Shell-centric and cloud-bound. Sprites is closest to a persistent workstation (checkpoint and restore). Daytona went closed source on 2026-06-11. |
| Self-hosted sandboxes | microsandbox, Docker Sandboxes, Kubernetes agent-sandbox, OpenSandbox, E2B (Terraform + Nomad) | Isolation is solved; none ships a desktop. |
| Workstation in a container | AIO Sandbox (agent-infra), E2B Desktop, Bytebot, Cua | AIO Sandbox bundles Chromium with VNC and CDP, shell, files, VSCode and MCP in one container. Cua provides VMs for Linux, Windows, macOS and Android. |
| Browser infrastructure | Steel Browser (Apache-2.0, self-hostable), Kernel, Browserbase, selenium/standalone-chrome | Headed sessions with a live view are standard here. |
| Browser by CLI | agent-browser (Vercel Labs), playwright-cli (Microsoft) | Built for agents that have a shell. Browser state lives on disk, not in the context window. |
| Desktop by CLI | agent-desktop (Vercel Labs, 1.0 in late October 2026 per a secondary source), computer-use-linux | Read the accessibility tree (AT-SPI) instead of screenshots. |
| Exchangeable backends | Hermes Agent, OpenAI Agents SDK, OpenClaw | Hermes puts local, docker, ssh, singularity, modal and daytona behind one interface. OpenClaw runs commands on the gateway, a sandbox, or a paired node. |
| "Agent OS" images | Alibaba Cloud Linux 4 Agentic Edition, SUSE, Canonical + NVIDIA OpenShell | Focus on policy, audit and snapshots, not on the workstation itself. |

The pieces all exist. What does not exist is their combination: the
exchangeable-backend projects only do shell, the workstation containers only
do containers, and the persistent "computers" are hosted services.

Most comparison sources are vendor blogs, and none of these tools has been
run by us yet. Migration step 1 is the spike that checks the claims.

Sources:
[Daytona closed source](https://daytona.io/dotfiles/updates/daytona-is-going-closed-source),
[AIO Sandbox](https://github.com/agent-infra/sandbox),
[Playwright CLI](https://playwright.dev/agent-cli/introduction),
[agent-desktop review](https://aicybr.com/blog/vercel-agent-desktop-accessibility-tree-ai-agent-cli-setup-security),
[Steel vs Kernel](https://steel.dev/blog/steel-vs-kernel),
[Hermes Agent tools](https://hermes-agent.nousresearch.com/docs/user-guide/features/tools),
[OpenAI Agents SDK sandboxes](https://openai.com/index/the-next-evolution-of-the-agents-sdk/),
[OpenClaw node host](https://docs.openclaw.ai/nodes/node-host),
[Kubernetes agent-sandbox](https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox),
[Linux for AI agents](https://wavect.io/blog/linux-for-ai-agents.md),
[anti-detect browser benchmark](https://ianlpaterson.com/?p=2188).

## Target shape

### Principles

1. **Access and provisioning are separate.** Reaching a workstation
   (`SandboxPort`) knows nothing about who created it. A provisioner creates
   a machine and returns connection parameters, nothing more. This separation
   is what makes the layer transparent.
2. **CLI first, desktop second.** Browser and desktop are driven by CLI tools
   that live *inside* the workstation and are called through `shell_exec`.
   No new tool family reaches the LLM; the image and a skill are enough.
3. **One recipe, every backend.** The same provisioning produces the Docker
   image, the VM, and the home computer. A robot must not notice which one
   it got.
4. **A workstation, not a throwaway sandbox.** State persists. What the robot
   installed on day 1 is there on day 30.

### Layers

```
robot (MCSToolExecutionLayer)
  SandboxToolDriver
    SandboxPort
      ├─ local    (new)  the robot's own host
      ├─ docker          a container on the robot host
      └─ ssh             server, VM, or home computer

recipe (new)             installs the same toolset on any of the above
provisioner (new, later) creates a machine, returns connection parameters
```

### The recipe

One idempotent provisioning that turns a bare Linux into a workstation:

- **Base:** Ubuntu 24.04 LTS or Debian stable. Not a rolling distribution -
  reproducibility matters more than freshness.
- **Display:** X11 on a virtual display (Xvfb) with a lightweight window
  manager. Not Wayland: input injection and window control are restricted
  there, and agent-desktop documents exactly those limits.
- **Shell:** tmux, plus the usual developer tooling.
- **Browser:** headed Chrome with a persistent profile and CDP bound to
  loopback.
- **Browser CLI:** playwright-cli or agent-browser.
- **Desktop CLI:** agent-desktop, or xdotool plus a screenshot tool.
- **Human takeover:** noVNC on the same display.
- **Identity:** a dedicated unprivileged user for the robot.

Opinionated desktop distributions for humans (Omarchy: Arch, Hyprland,
Wayland) are the wrong base, but the right idea: one command, and the system
is set up.

### Browser: one session, three rungs

All three rungs act on the **same** browser window and profile, so cookies and
logins carry across:

1. **CLI over CDP.** playwright-cli or agent-browser drives the headed
   Chrome. This is the default.
2. **Desktop control.** If a site detects the automation, the robot operates
   the same window through the desktop CLI or computer use.
3. **Human takeover.** For logins, second factors and CAPTCHAs, a human takes
   the window over via noVNC. The robot does not attempt to defeat these.

`selenium/standalone-chrome` has the right build (headed Chrome on Xvfb plus
noVNC), but WebDriver is reported as the most easily detected driver. The
same build driven over CDP is the better starting point.

No tool is undetectable. A workstation at home has one structural advantage
here: a residential address instead of a datacenter one.

### SandboxPort extensions

The six methods stay. Additions, in order of need:

- **Sessions.** Long-running and interactive work runs in tmux inside the
  workstation (`new-session -d`, `send-keys`, `capture-pane`), all through
  `exec`. This needs no protocol change and behaves the same on every
  backend.
- **Facts.** The driver probes the workstation once after `start` (OS,
  architecture, display present, browser present, available CLIs) and reports
  it in `sandbox_status`. A probe script through `exec` keeps this out of the
  adapters.
- **Port exposure.** noVNC must be reachable for the human, not for the
  robot. Docker already takes `ports` at creation; SSH needs a tunnel or an
  overlay network; local needs nothing.
- **Snapshots.** An optional capability, not part of the base protocol:
  checkpoint before risky work, roll back when the robot goes off track.

Fixes to the existing adapters belong here too: enforce `timeout` and return
stderr separately in the Docker adapter.

### Reaching a computer at home

The SSH adapter needs the workstation to be reachable inbound, which a home
computer behind NAT is not.

- **First:** an overlay network (WireGuard, Tailscale) plus the existing SSH
  adapter. No new code.
- **Later, if needed:** a small node process on the home computer that dials
  out to the robot and is paired once, with approvals stored on the node.
  OpenClaw's node host is the reference for this model.

### Provisioner

A separate, optional port. Sketch, not a commitment:

```python
class WorkstationProvisioner(Protocol):
    def create(self, spec: WorkstationSpec) -> ConnectionParams: ...
    def destroy(self, ref: str) -> None: ...
```

Candidates: Docker (today implicit in `DockerAdapter.start`), Incus (system
containers and VMs behind one API), a cloud API such as Hetzner. The robot
keeps working unchanged whether a provisioner exists or the workstation was
set up by hand.

### Governance

- **Credentials.** Connection secrets (SSH key, host) are robot credentials
  per [ADR-0004](../adr/0004-credential-layers.md) and live outside the tool
  sandbox, like `.env` today.
- **Audit.** `shell_exec` already passes through the tool execution layer, so
  every command lands in the existing audit trail.
- **Approvals.** Shell access on a real machine is the strongest tool the
  robot has. It needs the approval workflow from the roadmap before it runs
  unattended, most of all on the `local` backend, which has no isolation.
- **Egress.** Network rules belong to the workstation (firewall, proxy), not
  to the robot.

## Migration steps

1. **Spike.** Run the AIO Sandbox image through the existing Docker adapter
   and walk the three browser rungs by hand. This checks the vendor claims
   and shows what the recipe must contain.
2. **Fix the Docker adapter:** enforce `timeout`, return stderr separately.
3. **Add `mcs-adapter-local`** (subprocess on the robot host).
4. **Wire `SandboxToolDriver` into `MCSToolExecutionLayer`**, selected by a
   `workstation:` block in the robot config.
5. **Extract the recipe** from what the spike taught, build the image from
   it, and apply the same recipe to an SSH target.
6. **Sessions and facts** in the driver.
7. **Home computer** via overlay network.
8. **Provisioner**, once creating workstations by hand becomes the
   bottleneck.

Steps 2 to 4 are independent of the spike's outcome and can start first.

## Open questions

- **Recipe format.** Idempotent shell script, Ansible, Nix, or
  Dockerfile plus cloud-init? Nix gives the strongest reproducibility across
  all three targets at the highest learning cost.
- **Adopt or build.** Use the AIO Sandbox image as the container variant, or
  only learn from it and build our own from the recipe? Adopting it splits
  the container variant from the VM and home variants.
- **Browser CLI.** playwright-cli or agent-browser. Decide in the spike.
- **Workspace vs. workstation.** Is `<robot_home>/workspace/` mounted into
  the workstation, synced, or kept apart? With the `local` backend the two
  could be the same directory.
- **Cardinality.** One workstation per robot (matches
  [ADR-0005 - robot per user](../adr/0005-robot-per-user-and-multi-mailbox-tenancy.md)),
  or can a robot hold several, for example a container and a home computer?
- **Isolation of `local`.** Is an unprivileged user plus approvals enough, or
  must `local` always go through an OS-level sandbox?
- **Sessions in the protocol.** Is the tmux convention enough, or does the
  port need streaming output?
- **Other operating systems.** Windows and macOS workstations are out of
  scope here. Cua is the reference if that changes.

## Related

- [ADR-0007 - Robot home vs. workspace](../adr/0007-robot-home-and-workspace.md):
  the file-tool sandbox this design sits next to.
- [Roadmap](../project/roadmap.md): "General sandboxing across all tools" is
  parked there. That item is about isolating every tool call; this FRD is
  about giving the robot a computer. They are related but not the same.
- [Governance](../concepts/governance.md): the approval workflow that shell
  access depends on.
