"""Concrete :class:`~src.llm.ports.ModelDataSource` implementations.

Today: only :class:`BundledLiteLLMSource`, which loads a JSON
snapshot shipped with cephix. Offline, deterministic, audit-silent.

Tomorrow: a :class:`LLMPriceKitSource` that wraps the llmprice-kit
library and refreshes from upstream LiteLLM. The refresh path will
publish a :class:`~src.bus.messages.RobotAuditNote` on every
successful fetch (audit-loud). That source lives behind the same
:class:`~src.llm.ports.ModelDataSource` interface, so the metadata
service stays unchanged.
"""

from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BundledLiteLLMSource:
    """Loads a LiteLLM-shaped JSON snapshot bundled with cephix.

    The bundled file lives at ``src/llm/data/models.json``. It is a
    manually curated subset of the LiteLLM
    ``model_prices_and_context_window.json`` format -- enough models
    to demonstrate spec/pricing semantics, exercise the metadata
    service, and run offline tests without network access.

    Output shape: ``{(provider, model_id): raw_row_dict}``. The raw
    row dict matches the LiteLLM JSON schema (with ``model_id`` /
    ``provider`` injected so the metadata service can build the
    composite key without splitting strings).

    Why not just ship the upstream JSON file: that file is ~1 MB
    with 700+ models, most of which we don't need offline. The
    bundled snapshot stays small and human-readable; the upstream
    refresh path (Iteration 1b via llmprice-kit) is opt-in.
    """

    def __init__(self, *, file_path: Path | None = None) -> None:
        """Construct a source.

        ``file_path`` overrides the default bundled location. Useful
        for tests that want a tightly-scoped fixture, and for
        operators that want to ship a project-local catalog
        alongside their ``robot.yaml``.
        """
        self._file_path = file_path
        self._snapshot_id: str = ""

    @property
    def snapshot_id(self) -> str:
        """Snapshot identifier from the JSON's ``_snapshot_id`` field."""
        return self._snapshot_id

    async def load(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Read the JSON file and parse it into the canonical shape."""
        raw = self._read_raw()
        snapshot_id = raw.get("_snapshot_id", "")
        if not isinstance(snapshot_id, str):
            raise ValueError(
                f"BundledLiteLLMSource: '_snapshot_id' must be a string, "
                f"got {type(snapshot_id).__name__}"
            )
        self._snapshot_id = snapshot_id

        models_raw = raw.get("models")
        if not isinstance(models_raw, dict):
            raise ValueError(
                "BundledLiteLLMSource: missing or malformed 'models' "
                "object at the JSON root"
            )

        result: dict[tuple[str, str], dict[str, Any]] = {}
        for key, row in models_raw.items():
            if not isinstance(row, dict):
                logger.warning(
                    "BundledLiteLLMSource: skipping non-dict row at %r", key
                )
                continue
            model_id = row.get("model_id")
            provider = row.get("provider")
            if not isinstance(model_id, str) or not model_id:
                logger.warning(
                    "BundledLiteLLMSource: row %r has no model_id; skipping",
                    key,
                )
                continue
            if not isinstance(provider, str) or not provider:
                logger.warning(
                    "BundledLiteLLMSource: row %r has no provider; skipping",
                    key,
                )
                continue
            result[(provider, model_id)] = dict(row)
        return result

    def _read_raw(self) -> dict[str, Any]:
        if self._file_path is not None:
            text = self._file_path.read_text(encoding="utf-8")
        else:
            # importlib.resources keeps the file lookup robust across
            # source checkouts, wheels and editable installs.
            text = (
                resources.files("src.llm.data")
                .joinpath("models.json")
                .read_text(encoding="utf-8")
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"BundledLiteLLMSource: failed to parse JSON ({exc})"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"BundledLiteLLMSource: JSON root must be an object, "
                f"got {type(data).__name__}"
            )
        return data
