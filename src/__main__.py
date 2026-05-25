"""Entry point for ``python -m src``: dispatch to the cephix CLI."""

from src.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
