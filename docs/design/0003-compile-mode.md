# FRD-0003 - Compile mode (split build from boot)

- Status: design / not yet built
- Builds on: [ADR-0004 - Credential layers](../adr/0004-credential-layers.md) (accepted)
- Origin: extracted from ADR-0004 (compile-mode + preboot future sections)
  when ADRs were narrowed to record only made decisions.

The settled decision (ADR-0004) is the **build-time vs runtime** credential
separation, both implemented today in one process. ADR-0004 also names a
third layer - **preboot** - and deliberately defers it. This document designs
that deferred layer: what happens when the build splits from the boot (CI
produces a deployable image; the image starts later on another host).

## The preboot layer

A future deployment model: the build produces an artefact (robot bauplan +
a prepared `.env`), and at start-up the robot loads its `.env` **before
phase 1**. Deterministic, no bus, no lifecycle yet - but inside the robot's
address space instead of the builder's. Filesystem-or-environment binary
choice. Not implemented today (build-time and preboot collapse into one
process).

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

The runtime layer from ADR-0004 is **already shaped for this split**. Only
the preboot step is missing, and that is a `robot.py` change, not a builder
change.

## Lifting the build-time store concession

ADR-0004 records that runtime credential stores currently mirror the
build-time "direct path, eager load in `__init__`" pattern instead of taking
a `FilesystemConnection` like sibling utilities (`FilesystemSessionStore`,
`FilesystemFirmwareStore`). That mirroring is a build-time concession: the
builder resolves keys synchronously during YAML substitution, before any
event loop exists, so it cannot `await connection.read_text(".env")`.

When build and boot are split processes, the **robot-side** stores no longer
have to satisfy the builder's sync requirement (the builder has already
exited). The compile-mode split is the trigger to rework the runtime stores
onto the same `FilesystemConnection` DI as the rest of the filesystem stack.
The build-time stores stay as plain eager-loading value objects in the
builder process.

## Open questions

- Artefact format for the bauplan + prepared `.env` (image layer, tarball,
  OCI artefact?).
- Whether preboot reads a single `.env` or a layered chain in the deploy
  environment.
- Exact `robot.py` hook point for the pre-phase-1 load.
