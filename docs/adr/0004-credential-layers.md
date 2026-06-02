# 0004 - Credential layers: build-time, preboot, runtime

- Status: accepted
- Date: 2026-05-30

## Context

Credentials in cephix span three genuinely different moments in the
robot's life. Conflating them produces an architecture that "almost
works" but has a confused ownership story. We had to disentangle them
once during the persistence DAO refactor; this ADR captures the result
so the next contributor doesn't reintroduce the mix.

The three moments:

1. **Build-time.** The builder walks the `robot.yaml`, replaces
   `${KEY}` references during the substitution pass, and constructs
   the component graph. The robot does not exist yet; there is no
   bus, no lifecycle, no audit trail to participate in. Build-time
   resolves are deterministic, fast, and "fail loud and early" if a
   key is missing.
2. **Preboot.** A future deployment model: the build produces an
   artefact (robot bauplan + a prepared `.env`), and at start-up the
   robot loads its `.env` **before phase 1**. Deterministic, no bus,
   no lifecycle yet either, but happens inside the robot's address
   space instead of the builder's. Filesystem-or-environment binary
   choice; not implemented today (we collapse build-time and preboot
   into one process).
3. **Runtime.** Bus components -- LLM drivers, future tools,
   channels -- ask for credentials *while the robot is serving*. This
   is where the `CredentialProvider` earns its keep: it routes the
   lookup through its store chain and emits a `RobotAuditNote` per
   attempt (the value never lands on the bus). The provider exists at
   `BUS_UTILITY` level (8) precisely so the audit subscribers are
   already listening when the first runtime resolve happens.

Until this refactor a single `CredentialProvider` straddled
build-time and runtime: the builder constructed it eagerly, used it
for substitution via `resolve_sync`, then handed the *same instance*
to the robot as a runtime component. Two lifetimes lived in one
object.

## Decision

**Three layers, three sets of instances, no sharing.**

### Build-time layer

The builder owns its own ephemeral credential store chain. It is a
plain `list[CredentialStorePort]` constructed by
`_build_credential_stores(spec, robot_home)`, used by
`_resolve_via_stores(stores, key)` during the substitution pass, and
deleted (`del builder_credentials`) the moment substitution finishes.

These instances never see `start()`, never appear in
`robot.components`, never emit an audit note. They are build material.

### Runtime layer

The same `_build_credential_stores` helper is called *again* with the
same spec to produce a *separate* set of instances. These:

- are `RobotComponent` subclasses (boot category `UTILITY`, boot
  level 3),
- appear in `robot.components` and the boot log,
- are injected into the `CredentialProvider` as the `stores`
  constructor argument,
- run their lifecycle: `start()` logs the key count, `stop()` clears
  the cache.

`EnvCredentialStore` and `ProcessEnvCredentialStore` inherit from
both `RobotComponent` and `CredentialStorePort` so the same class
serves both layers; only the instance lifetimes differ.

### Store loading: direct path, not FilesystemConnection (today)

Unlike the filesystem-backed runtime utilities
(`FilesystemSessionStore`, `FilesystemFirmwareStore`) which receive a
`FilesystemConnection` and load asynchronously inside their `start()`,
the credential stores take a **direct path** (or `os.environ`
directly) and **load eagerly in `__init__`**. The asymmetry is
deliberate and load-bearing for the current single-process build:

- The builder constructs the build-time set *before any event loop
  exists* and immediately calls `lookup(key)` synchronously during
  YAML substitution. There is no place to `await
  connection.read_text(".env")` here -- the substitution pass is sync.
- Routing the load through a `FilesystemConnection` would force
  either a sync mirror API on the connection (works for `LocalFS`,
  doesn't generalize to S3 / Vault) or a two-pass build (sync
  bootstrap of substitution-only stores, async lifecycle for runtime
  stores). Both add complexity for zero current benefit -- a `.env`
  file is small, eager parsing is cheap, the file location is known
  at build time.

This is a constraint the compile-mode refactor (below) must respect:
when build and boot are split processes, the **robot-side** stores
can be reworked to use the same `FilesystemConnection` DI as
sibling utilities -- they no longer have to satisfy the builder's
sync requirement, because by then the builder has already exited.
The build-time stores stay as plain eager-loading value objects in
the builder process; the runtime stores join the filesystem-stack
symmetry on the robot side.

In other words: the direct-path pattern is a build-time concession
that runtime-side stores currently mirror for code simplicity. The
compile-mode split is the trigger to lift the concession from the
runtime side.

### Preboot layer

Not implemented today, deliberately left as a future refactor. When
the compile / deployment mode arrives, the robot will load its
prepared `.env` (or fall back to `os.environ`) in a pre-phase-1 step
before any RobotComponent runs. The runtime layer is unchanged --
the robot's credential stores still construct from whatever the
preboot step exposed.

### No central preflight

The robot does not inspect the bauplan to predict which keys
components will ask for. A component that needs a missing key fails
in its own `start()` -- precise local error, no pre-check duplication.
This matches the rest of cephix's "components drive themselves"
pattern (capabilities, audit notes, lifecycle).

## Consequences

What gets easier:

- The `CredentialProvider` has one job: runtime resolves with audit.
  Build-time substitution is no longer its problem.
- Build-time stores can be cheap and ephemeral; they don't need
  lifecycle plumbing.
- Runtime stores are first-class components: visible in the boot log,
  swappable per yaml, and ready for the same DI shape as the
  filesystem stack (`EnvCredentialStore [~/.cephix/cephix.env] (xxx)
  started: 4 key(s)`).
- A future `VaultCredentialStore` slots in as a UTILITY component
  with its own `VaultConnection` -- same pattern as
  `FilesystemSessionStore` and `FilesystemConnection`.

What gets harder:

- The same `credentials:` spec is materialised twice. The cost is a
  second `dotenv_values` parse per build (microseconds against the
  rest of build cost) plus the extra explicit thinking when a reader
  first sees both sets.
- Cross-process lifetimes diverge: a build-time miss raises
  `CredentialNotFound("builder")`; a runtime miss raises
  `CredentialNotFound` from the provider with audit emission. The
  audit log will not show the build-time misses (no bus exists yet)
  -- this is correct, not a gap.

## Compile-mode (future, not implemented)

When the build splits from the boot (CI produces a deployable
image, the image is started later on a different host), the layers
land like this:

```
[ build host ]
  builder
    ├─ reads source ${KEY} references
    ├─ constructs build-time stores
    ├─ substitutes / writes prepared .env into the image
    └─ emits robot bauplan + .env as artefact

[ deploy host ]
  preboot
    ├─ if filesystem: load <robot_home>/.env
    ├─ else: read os.environ
    └─ exposes resolved values to robot's runtime stores

  robot.start()
    ├─ phase 1: control plane
    ├─ phase 2: skeleton (UTILITY-level credential stores included)
    ├─ phase 3: userspace (BUS_UTILITY-level CredentialProvider)
    └─ runtime: components call provider.resolve(...) with audit
```

The runtime layer in this ADR is already shaped for that
split -- only the preboot step is missing, and that is a robot.py
change, not a builder change.

## Relationship to other ADRs

- [0003 - Wire-format codec is not abstracted](0003-no-codec-abstraction-yet.md)
  rejected a pattern because the use case was speculative. This ADR
  embraces a pattern (the three-layer separation) because the use
  case is real *today* (build vs runtime) and the future case
  (preboot) sits on the same scaffolding.
