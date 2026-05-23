# 0001 — Use MkDocs Material for documentation

- Status: accepted
- Date: 2026-05-22

## Context

The project needs a documentation site that is:

- **Co-located with code** so docs cannot drift out of sync.
- **Easy to author** (plain Markdown, no MDX/JSX learning curve).
- **Python-native** since the project itself is Python (toolchain via `pip`).
- **Static-site output** for trivial hosting on Coolify behind nginx.
- Able to **pull docstrings out of `src/`** as API reference.

Alternatives considered:

- **Docusaurus** — strong, but Node toolchain and MDX add complexity that is not
  paid back for a single-language, single-version site.
- **Sphinx + Read the Docs theme** — capable but heavier and rST-flavored;
  Markdown support via MyST is fine but adds a layer.
- **Starlight (Astro)** — modern, fast, but again a JS toolchain.

## Decision

Use **MkDocs Material** as the documentation site generator, with:

- `mkdocstrings[python]` for API reference pulled from docstrings.
- `pymdownx.superfences` for Mermaid diagrams in Markdown.
- Output deployed as a static nginx container on Coolify.

Docs live in `docs/` next to `src/`. Every PR that changes behavior is
expected to update relevant docs in the same PR.

## Consequences

**Easier:**

- Single `pip install -r requirements-docs.txt` to author locally.
- One CI pipeline builds both the package and the docs.
- Docstrings in `src/` become the source of truth for API documentation.

**Harder / accepted trade-offs:**

- No MDX / React components in pages. Acceptable: we want documentation, not a microsite.
- Versioned docs (v1/v2 side-by-side) require the `mike` plugin if we want them.
  Deferred until needed.
- i18n requires a plugin; we ship English-only for now.
