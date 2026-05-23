# External References

> The prior art cephix draws on, and where each piece sits relative to our
> architecture. Use this page to follow up on a concept, or to anchor a
> design discussion in something more solid than "we just felt like it".

---

## Robotics — ROS and the physical-robot heritage

Even though cephix is a *digital* robot, the architectural vocabulary
("kernel", "channels", "actors", "audit trail") borrows liberally from
real-world robotics middleware.

### ROS 2 — the current robotics middleware standard

[ROS 2 (Robot Operating System 2)](https://docs.ros.org/) is the
industry-standard middleware for building robot software. It is **not** a
traditional OS but a structured software layer running on Linux, Windows,
or macOS, with the following building blocks:

- **Topics** — typed publish/subscribe channels for continuous data
  (sensor readings, odometry).
- **Services** — synchronous request/reply for stateful queries.
- **Actions** — long-running tasks with feedback and pre-emption.
- **Nodes** — the unit of computation; one process can host many.

ROS 2 uses [**DDS (Data Distribution Service)**](https://www.dds-foundation.org/)
as its underlying middleware — a decentralised pub/sub system with
proven use in defence and avionics.

**Mapping to cephix:**

| ROS 2 concept | Cephix analogue | Notes |
|---|---|---|
| Topic (pub/sub) | `SemanticBus` (`src/bus.py`) | Same idea, different scale |
| Node | Tool driver / channel | We compose, ROS distributes |
| Action | Long-running plan with revisions | We add LLM-driven re-planning |
| Parameter server | Firmware + memory documents | Markdown-first instead of binary |
| `rclpy` (Python client) | The whole cephix package | We *are* a single-node super-agent |

A cephix robot is, in ROS terms, **one ROS 2 node that hosts a planner, a
governance guard, and several tool drivers** — packaged as a coherent
agent. The parallels are not accidental: a digital robot needs the same
discipline (deterministic kernel, clear ingress/egress, observability)
that physical robots have practiced for two decades.

!!! note "ROS 3?"
    There is currently no officially released "ROS 3". Open Robotics has
    publicly discussed potential successor architectures and the
    [ROS 2 Iron / Jazzy / Rolling](https://docs.ros.org/en/rolling/Releases.html)
    distributions continue to evolve. When ROS 3 (or whatever it ends up
    called) lands, the alignment between cephix and the robotics world
    should be revisited.

### Related — physical-world background reading

- [SMED — Single-Minute Exchange of Die](https://en.wikipedia.org/wiki/Single-minute_exchange_of_die)
  (Shigeo Shingo). The original lean-manufacturing technique for rapid
  re-tooling. Conceptually parallels what cephix's **AutonomyLevel +
  SOP-bound tool mounting** achieves at runtime: switching the robot's
  "tools" without reconfiguring the kernel.
- [Cellular manufacturing](https://en.wikipedia.org/wiki/Cellular_manufacturing) —
  grouping related processes into a cell. Analogous to a per-SOP tool subset.

---

## Standard Operating Procedures — the industrial heritage

SOPs in cephix are not a new invention; they descend directly from
quality-management and lean-manufacturing practice.

### ISO 9001 — the formal definition

In [ISO 9001 quality management](https://www.iso.org/iso-9001-quality-management.html),
the standard distinguishes three artefacts that map cleanly onto cephix:

| ISO 9001 | What it is | Cephix counterpart |
|---|---|---|
| **Process** | A set of interrelated activities producing an output | The harness lifecycle (Observe → Plan → Execute → Respond) |
| **Procedure (SOP)** | A specific way to carry out a process; *what* is done, by *whom*, *when* | `SOPDefinition` YAML files |
| **Work instruction** | The detailed *how* — tools, methods, equipment, measurements | `steps` inside an SOP, plus the planner's runtime decisions |

The ISO model also names **Betriebsmittel** (operating resources /
equipment) as a first-class concept of a work instruction. In cephix that
is the `required_tools` and `required_skills` fields of an
`SOPDefinition` — the explicit list of capabilities the procedure is
allowed to draw on.

### Lean — standardised work

[Standardised work](https://www.lean.org/lexicon-terms/standardized-work/)
in the Toyota Production System captures the *current best known way* to
do a job. Two ideas transfer directly:

- **Living documents.** Standardised work is updated when a better way is
  found. Cephix's `audit:sop:<id>` notebook is exactly this mechanism for
  the LLM operator.
- **Visual reference at the workplace.** The standardised-work sheet is
  visible to the operator while doing the work. Cephix injects the SOP's
  steps and learnings into the system prompt for the same reason.

---

## Digital work — the workforce heritage

Cephix is a software system, but the design problem is organisational:
how can digital workers perform bounded work with visible instructions,
auditable decisions, and human override? The references below are useful
for keeping that framing honest.

### Digital work and digital labour

[Christian Fuchs and Sebastian Sevignani's "What Is Digital Labour? What
Is Digital Work?"](https://www.triple-c.at/index.php/tripleC/article/view/461)
is a good conceptual starting point for distinguishing *digital work*
from the narrower, value-extraction framing of *digital labour*.

[Moritz Altenried's "The platform as factory"](https://journals.sagepub.com/doi/10.1177/0309816819899410)
is useful as a warning label: digital work can easily become invisible,
fragmented, and algorithmically managed labour. Cephix's governance,
audit, and approval layers exist partly to avoid building another opaque
task-control system.

### Digital workforce and process automation

The business-automation lineage is usually discussed as **Robotic Process
Automation (RPA)** and **Intelligent Process Automation (IPA)**. RPA
automates repetitive UI/system tasks; IPA adds AI/ML/NLP to handle more
judgement-heavy process steps.

Useful follow-up references:

- [RPA in Business Process Management — systematic literature review](https://www.mdpi.com/2227-7080/14/4/225)
- [A Survey on Intelligent Process Automation](https://arxiv.org/pdf/2007.13257)
- [Artificial Intelligence and the Future of Work](https://www.nationalacademies.org/publications/27644)

Cephix differs from classic RPA by putting a deterministic harness around
an LLM planner rather than recording a brittle UI macro. The closest
research benchmark for "digital worker" style tasks is
[TheAgentCompany](https://arxiv.org/html/2412.14161v2), which evaluates
LLM agents on workplace-like tasks such as browsing, coding, running
programs, and communicating with coworkers.

### Algorithmic management as a risk frame

Algorithmic management literature is a useful counterweight when designing
approval, audit, and observability. A system that coordinates work can
also become a system that hides control.

- [Taylorism on steroids or enabling autonomy?](https://pmc.ncbi.nlm.nih.gov/articles/PMC10074337/)
- [Data dignity / data as labour](https://eliassi.org/lanier_and_weyl_hbr2018.pdf)

---

## LLM agents — the Anthropic and OpenAI heritage

### Anthropic Agent SDK and Agent Skills

Anthropic's [Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview)
exposes the same tool-loop machinery that powers Claude Code. Within that
SDK, **[Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)**
are a packaged, declarative way to give Claude specialised capabilities.

A Skill is a folder with a `SKILL.md` file containing YAML frontmatter
(metadata) and prose (instructions). Claude **autonomously decides** when
to invoke a Skill based on its description, the user's request, and the
runtime context. The
[public skills repository](https://github.com/anthropics/skills) on
GitHub provides authoring examples.

**Mapping to cephix:**

| Anthropic Skill | Cephix counterpart | Difference |
|---|---|---|
| `SKILL.md` with YAML frontmatter | `SOPDefinition` (YAML) | Cephix splits frontmatter (metadata) and steps (prose) explicitly |
| Auto-invocation by Claude | `SOPResolver.resolve()` matching `trigger_patterns` | Cephix is deterministic; Anthropic relies on LLM judgement |
| Optional supporting resources | `required_tools`, `learnings_document` | Cephix names operating resources up front |
| Discovery from a directory | `FileSOPRepository` | Same idea, different file scheme |

The architectural intent is **the same**: ship a *capability* as a
self-contained, discoverable artefact rather than as bespoke code.
Cephix's tiering (Tool → Skill → SOP) is a more layered take on the same
idea — see [Tools, Skills & Toolbuilder](../concepts/toolbuilder.md).

### Tool use / function calling

The underlying mechanism — letting the model emit structured calls that
the runtime executes — is shared with:

- [Anthropic tool use](https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview)
- [OpenAI function calling](https://platform.openai.com/docs/guides/function-calling)
- [LiteLLM](https://docs.litellm.ai/) (provider-agnostic gateway, optional
  dependency in cephix)

Cephix wraps all of these behind `PlannerPort`. The
`GovernedToolExecutor` then adds risk classification, approval flow, and
telemetry on top of the raw tool-use loop — the cross-vendor concerns
none of the SDKs handle natively.

### Language-programmed agent systems

Cephix treats natural language instructions, SOPs, firmware, and memory
documents as part of the executable control surface. The LLM is not
"the app"; it is an interpreter-like planner inside a deterministic
runtime.

The closest research lines:

- [ReAct — Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629):
  interleaves reasoning traces, tool actions, and environment
  observations.
- [Toolformer](https://arxiv.org/abs/2302.04761): trains language models
  to decide when and how to call external APIs.
- [CoRE / AIOS Compiler — LLM as Interpreter for Natural Language
  Programming](https://arxiv.org/html/2405.06907v1): explicitly frames
  LLMs as interpreters for natural-language, pseudo-code, and flow-based
  agent programs.
- [Generative Agents](https://arxiv.org/abs/2304.03442): combines memory,
  reflection, planning, and natural-language interaction into believable
  long-running agents.
- [Voyager](https://arxiv.org/abs/2305.16291): shows an LLM agent growing
  a reusable skill library from environment feedback and execution errors.
- [TheAgentCompany](https://arxiv.org/html/2412.14161v2): evaluates
  agents as workplace-like digital workers rather than toy chatbots.
- [Adapting the Interface, Not the Model](https://arxiv.org/abs/2605.22166):
  frames deterministic LLM agents as frozen models inside an adaptive
  runtime harness. The paper's Life-Harness adapts environment contracts,
  procedural skills, action realization, and trajectory regulation without
  changing model weights — very close to cephix's harness-first premise.

Karpathy's [Software 2.0](https://karpathy.medium.com/software-2-0-a64152b37c35)
is not an academic paper, but it is a useful framing essay: some software
behaviour is no longer written directly as imperative code but shaped by
data, examples, prompts, and model behaviour. Cephix keeps the boundary
explicit: deterministic kernel and governance in code; judgement and
planning behind `PlannerPort`.

---

## Memory — agent memory architectures

The cephix memory model (consciousness / subconsciousness, scopes
identity/user/memory, notebook tiers) is informed by several public lines
of work.

### MemGPT and Letta — memory as an OS

[**MemGPT**](https://memgpt.ai/) (UC Berkeley, 2023) and its commercial
successor [**Letta**](https://www.letta.com/) treat the LLM context window
as a constrained memory resource and apply OS-style paging:

- **Main context** (in-window) ≈ RAM
- **Archival memory** (external) ≈ disk
- The agent **self-edits** memory via tool calls, deciding what to keep,
  evict, or retrieve.

Cephix's distinction between **consciousness** (always-loaded firmware +
memory + active notebooks) and **subconsciousness** (archived tails,
rotated memory, inactive notebooks) is the same insight, with a
documentation-first surface: humans can read the files, the agent reads
the same files, and the load policy lives in the context assembler.

See [Concepts › Memory](../concepts/memory.md#model-of-consciousness) for
how the layers compose.

### OpenClaw — the personal-agent reference

OpenClaw (mentioned in the [Memory concept doc](../concepts/memory.md#reference-openclaw-comparison))
is the personal-agent design that directly informed several cephix
decisions:

- Inject `MEMORY.md` whole into the system prompt → cephix's
  "always load into consciousness".
- Pre-compaction memory flush → cephix's planned flush mechanism.
- Experimental "dreaming" promotion → cephix's planned dreaming subsystem.

### Working memory vs. long-term memory

The naming (working memory, episodic vs. semantic memory) borrows from
cognitive science. Two accessible jumping-off points:

- [Atkinson–Shiffrin multi-store model](https://en.wikipedia.org/wiki/Atkinson%E2%80%93Shiffrin_memory_model) —
  the classic short-term / long-term distinction.
- [Working memory (Baddeley)](https://en.wikipedia.org/wiki/Working_memory) —
  the modern refinement that maps reasonably onto an LLM's context window.

The point is not to claim cephix is a cognitive model; it is to recognise
that the distinctions we draw (in-context vs. searchable, artefact-bound
vs. global) have an established shape outside AI.

---

## Software architecture — the design heritage

The way cephix is *constructed* (not just what it does) follows several
well-documented patterns. Knowing the references makes the source code
easier to navigate.

### Ports and adapters (hexagonal architecture)

[Alistair Cockburn's hexagonal architecture](https://alistair.cockburn.us/hexagonal-architecture/)
is the reason every cephix layer has a `*Port` (Protocol class) and one
or more concrete implementations. The composition root
([`src/app.py`](https://github.com/your-org/cephix/blob/main/src/app.py))
wires ports to adapters; the kernel never sees concrete implementations.

### SOLID and clean boundaries

The local style is closest to the SOLID family of object-oriented design
principles, especially:

- **SRP** — keep modules focused on one reason to change.
- **ISP** — prefer small, client-specific ports over wide interfaces.
- **DIP** — depend on abstractions, not concrete details.

Robert C. Martin's [Principles of Object Oriented
Design](http://butunclebob.com/ArticleS.UncleBob.PrinciplesOfOod) is the
compact reference for the acronym. His [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
essay also explains the dependency rule that cephix follows in practice:
inner policy code must not know outer infrastructure details.

### Composition root and dependency injection

The "composition root" naming and pattern follows
[Mark Seemann's *Dependency Injection in .NET / Principles, Practices,
and Patterns*](https://www.manning.com/books/dependency-injection-principles-practices-patterns).
Cephix is Python, not .NET, but the discipline carries: do all wiring in
one place; never use a service locator at runtime.

[Martin Fowler's Dependency Injection article](https://martinfowler.com/articles/injection.html)
is the short primary reference for separating configuration from use and
contrasting dependency injection with service locator.

### Data access objects and enterprise patterns

When cephix grows persistent stores beyond JSONL/YAML files, the relevant
reference family is Martin Fowler's [*Patterns of Enterprise Application
Architecture* catalog](https://martinfowler.com/eaaCatalog/).

For the "DAO" term specifically, Oracle's [Data Access Object
pattern](https://www.oracle.com/java/technologies/dataaccessobject.html)
describes the core idea: encapsulate access to a data source behind a
stable object so business code does not depend on storage mechanics.

Related patterns to keep nearby:

- [Repository](https://martinfowler.com/eaaCatalog/repository.html) —
  collection-like access to domain objects.
- [Data Mapper](https://martinfowler.com/eaaCatalog/dataMapper.html) —
  mapping between domain objects and storage without coupling either side.
- [Unit of Work](https://martinfowler.com/eaaCatalog/unitOfWork.html) —
  tracking changed objects and coordinating writes.

### Architecture Decision Records

[Michael Nygard's "Documenting Architecture Decisions"](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions.html)
introduced the ADR format we use under [`docs/adr/`](../adr/index.md). The
[ADR community on GitHub](https://adr.github.io/) maintains templates and
tooling references.

### C4 model

[Simon Brown's C4 model](https://c4model.com/) (Context, Container,
Component, Code) is the diagram vocabulary we are adopting incrementally.
See [ADR 0002](../adr/0002-diagrams-mermaid-and-c4.md) for when we plan
to bring in [Structurizr DSL](https://structurizr.com/).

### Diátaxis — the documentation framework

This doc site is structured around [Diátaxis](https://diataxis.fr/)
(Daniele Procida): the four-quadrant model of tutorials, how-to guides,
reference, and explanation. We collapse it pragmatically (Concepts =
explanation, Reference = reference, Getting Started = tutorials+how-to)
but the framework is the source.

---

## Runtime and documentation toolchain

These references are not conceptual ancestors; they are the concrete
tools a maintainer needs when debugging the docs site or runtime surface.

### Documentation stack

- [MkDocs](https://www.mkdocs.org/) — static documentation build.
- [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) —
  theme, navigation, search, admonitions, and code UX.
- [mkdocstrings](https://mkdocstrings.github.io/) — API reference from
  Python docstrings.
- [pymdown-extensions](https://facelessuser.github.io/pymdown-extensions/) —
  Markdown extensions used for tabs, highlighting, superfences, and tasks.
- [Mermaid](https://mermaid.js.org/) — diagram source embedded in docs.
- [mkdocs-panzoom-plugin](https://github.com/PLAYG0N/mkdocs-panzoom) —
  zoom support for Mermaid diagrams.

### Runtime and operations stack

- [aiohttp](https://docs.aiohttp.org/) — WebSocket server and CLI client
  transport. Cephix does **not** use FastAPI/Uvicorn today.
- [WebSocket protocol / RFC 6455](https://www.rfc-editor.org/rfc/rfc6455)
  and [MDN WebSocket API](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API)
  — useful when documenting `/ws`, scopes, and client behaviour.
- [PyYAML](https://pyyaml.org/wiki/PyYAMLDocumentation) — YAML-backed
  defaults, SOPs, and robot configuration.
- [Python asyncio](https://docs.python.org/3/library/asyncio.html) —
  event loop, service host, and queue-driven runtime behaviour.
- [JSON Lines](https://jsonlines.org/) — event logs and approval/audit
  files.
- [uv](https://docs.astral.sh/uv/) and the [Python Packaging User Guide](https://packaging.python.org/) —
  installation, `pyproject.toml`, and script entry points.
- [pytest](https://docs.pytest.org/) — behaviour tests as executable
  specification.
- [Docker](https://docs.docker.com/), [docker-py](https://docker-py.readthedocs.io/),
  [nginx](https://nginx.org/en/docs/), [GitHub Actions](https://docs.github.com/en/actions),
  and [Coolify](https://coolify.io/docs) — docs image, workstation backend,
  CI, and deployment.

---

## Related projects worth a look

These are not direct influences but inhabit overlapping problem spaces.
Useful for sanity-checking design decisions:

- [**LangChain**](https://www.langchain.com/) and
  [**LlamaIndex**](https://www.llamaindex.ai/) — agent frameworks that
  take a very different (highly composable, lots of moving parts)
  approach to the same problem.
- [**AutoGen**](https://microsoft.github.io/autogen/) (Microsoft) —
  multi-agent orchestration; instructive on planner/critic separation.
- [**Open-RMF**](https://www.open-rmf.org/) — Open Robotics' multi-robot
  fleet coordination. The cross-fleet protocol style is relevant if
  cephix ever runs as a fleet rather than as one robot.
- [**Octaro**](https://octaro.io) — the company building digital workers
  on cephix-style architecture (Octaro is the commercial home of this
  project; see the [README](https://github.com/your-org/cephix/blob/main/README.md)).

---

## Sources used to compile this page

External references cited above were checked against current public
documentation at the time of writing.

- ROS 2: [official documentation](https://docs.ros.org/),
  [ROS releases](https://docs.ros.org/en/rolling/Releases.html)
- Anthropic Agent SDK and Skills:
  [Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview),
  [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview),
  [public skills repo](https://github.com/anthropics/skills)
- ISO 9001 SOP vs. work instruction:
  [9000 Store — Processes, Procedures, Work Instructions](https://the9000store.com/iso-9001-2015-requirements/iso-9001-2015-context-of-the-organization/processes-procedures-work-instructions/),
  [SafetyCulture — ISO 9001 work instructions](https://safetyculture.com/topics/work-instruction/iso-9001-work-instructions)
- Digital work / workforce:
  [Fuchs and Sevignani, "What Is Digital Labour?"](https://www.triple-c.at/index.php/tripleC/article/view/461),
  [Altenried, "The platform as factory"](https://journals.sagepub.com/doi/10.1177/0309816819899410),
  [TheAgentCompany](https://arxiv.org/html/2412.14161v2)
- LLM agents:
  [ReAct](https://arxiv.org/abs/2210.03629),
  [Toolformer](https://arxiv.org/abs/2302.04761),
  [CoRE](https://arxiv.org/html/2405.06907v1),
  [Generative Agents](https://arxiv.org/abs/2304.03442),
  [Voyager](https://arxiv.org/abs/2305.16291),
  [Adapting the Interface, Not the Model](https://arxiv.org/abs/2605.22166)
- MemGPT / Letta:
  [Letta agent memory blog post](https://www.letta.com/blog/agent-memory),
  [MemGPT paper coverage](https://www.leoniemonigatti.com/papers/memgpt.html),
  [MemGPT arXiv](https://arxiv.org/abs/2310.08560)
- Software architecture:
  [Hexagonal Architecture](https://alistair.cockburn.us/hexagonal-architecture/),
  [SOLID / Principles of OOD](http://butunclebob.com/ArticleS.UncleBob.PrinciplesOfOod),
  [Clean Architecture](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html),
  [Fowler on Dependency Injection](https://martinfowler.com/articles/injection.html),
  [Patterns of Enterprise Application Architecture catalog](https://martinfowler.com/eaaCatalog/),
  [Oracle Data Access Object pattern](https://www.oracle.com/java/technologies/dataaccessobject.html)
- Documentation/runtime stack:
  [MkDocs](https://www.mkdocs.org/),
  [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/),
  [mkdocstrings](https://mkdocstrings.github.io/),
  [aiohttp](https://docs.aiohttp.org/),
  [JSON Lines](https://jsonlines.org/),
  [uv](https://docs.astral.sh/uv/)
