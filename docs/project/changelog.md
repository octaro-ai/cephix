# Changelog

> A human-curated log of meaningful changes.
> For the raw history, run `git log`.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):
each section lists what was **Added**, **Changed**, **Fixed**, or **Removed**.

## Unreleased

### Added

- Documentation site with MkDocs Material:
    - English-only, single-version
    - Mermaid diagrams with pan / zoom via `mkdocs-panzoom-plugin`
    - Architecture Decision Records (ADRs)
    - Per-page sections for Getting Started, Concepts, Architecture,
      Project (Status / Roadmap / Changelog), and Reference

### Changed

- `RUN_FLOW.md` split into two pages: a conceptual lifecycle view under
  Concepts and a file-level trace under Architecture.
- All documentation translated from German to English.

### Notes

This is a development-stage project. Pre-1.0 releases may include
backwards-incompatible changes. The first tagged release will reset this
changelog into a `## [0.1.0] — YYYY-MM-DD` section and begin the regular
cadence.

---

## How to add an entry

When merging a non-trivial change, append a bullet to **Unreleased** under
the appropriate category. Keep entries:

- **Short** — one line where possible.
- **User-facing** — describe the change in terms a reader unfamiliar with
  the PR can understand. Link to the relevant doc or ADR.
- **Actionable** — if a change requires user action (e.g. config update),
  say so explicitly.

Avoid:

- Implementation details that a doc / ADR already covers in depth.
- Internal refactors that have no observable effect.
- "Bumped version" or "updated dependencies" unless they break something.
