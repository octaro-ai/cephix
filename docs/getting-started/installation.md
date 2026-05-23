# Installation

## Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or a standard `pip`-based setup
- **Docker** (only required if you want to use workstation tools, which run
  isolated containers)

## Install dependencies

```bash
uv sync
```

This installs the base package and creates a virtual environment in `.venv/`.

### Optional extras

The project declares several optional dependency groups in `pyproject.toml`:

| Extra | Pulls in | When to use |
|---|---|---|
| `cli` | `rich` | Coloured CLI output |
| `dev` | `pytest` | Running the test suite |
| `anthropic` | `anthropic>=0.40` | Claude planner |
| `openai` | `openai>=1.50` | OpenAI planner |
| `litellm` | `litellm>=1.50` | LiteLLM gateway |
| `all-llm` | All three LLM extras | Switching providers at runtime |

Install one or more with:

```bash
uv sync --extra anthropic --extra cli --extra dev
```

## Verify the installation

```bash
uv run python -m src --help
```

If the help text appears, you are good to go. Move on to the
[Quickstart](quickstart.md).

## Configure an LLM provider

The planner needs an API key. Set one of:

```bash
# Anthropic (recommended)
export ANTHROPIC_API_KEY="sk-ant-..."

# Or OpenAI
export OPENAI_API_KEY="sk-..."
```

The full list of configuration options is in
[Reference › Configuration](../reference/configuration.md).
