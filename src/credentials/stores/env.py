"""``EnvCredentialStore`` -- a ``.env`` file as a credential backend.

Reads a single ``.env`` file at construction time using
:func:`dotenv.dotenv_values`, the same parser the rest of the
ecosystem uses (quoted values, escapes, comments, multi-line
values). The parsed dict is held in-memory; subsequent
:meth:`lookup` calls are pure dictionary lookups.

Why eager parsing:

- The builder needs synchronous, predictable lookups during the
  YAML substitution pass.
- The file is small (a handful of secrets) and the cost is
  negligible at process startup.
- Hot-reloading isn't a feature we need: changing ``.env``
  content is an operational action that warrants a restart.

Tolerance:

- A non-existent file is *not* an error: the store simply holds
  nothing. This is the "global ``~/.cephix/.env`` is optional"
  case. The builder lists multiple stores and the user only fills
  in the ones they actually use.
- A malformed file (parser failure) raises
  :class:`~src.credentials.exceptions.CredentialStoreError` at
  construction so the builder fails loud and early instead of
  surprising the user with phantom missing keys later.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import dotenv_values

from src.credentials.exceptions import CredentialStoreError
from src.credentials.ports import CredentialStorePort

logger = logging.getLogger(__name__)


class EnvCredentialStore(CredentialStorePort):
    """A ``.env`` file as a synchronous credential backend.

    Constructor:

    - ``path`` -- the file to read. Tilde expansion is applied
      (``~`` -> user home). Missing file is tolerated; the store
      then holds an empty mapping.
    - ``name`` -- override the audit name. Default uses the file
      stem with an ``env:`` prefix (e.g. ``"env:robot"`` for a
      ``.env`` next to ``robot.yaml``). Pass an explicit name when
      multiple stores would collide on the default.
    """

    def __init__(self, path: str | Path, *, name: str | None = None) -> None:
        self._path = Path(path).expanduser()
        self._name = name or self._derive_name(self._path)
        self._values: dict[str, str] = self._load(self._path, self._name)

    @property
    def name(self) -> str:
        return self._name

    @property
    def path(self) -> Path:
        return self._path

    def lookup(self, key: str) -> str | None:
        return self._values.get(key)

    def has_key(self, key: str) -> bool:
        return key in self._values

    @staticmethod
    def _load(path: Path, name: str) -> dict[str, str]:
        if not path.is_file():
            logger.debug(
                "EnvCredentialStore %s: %s does not exist; "
                "store starts empty",
                name,
                path,
            )
            return {}
        try:
            parsed = dotenv_values(path)
        except Exception as exc:  # noqa: BLE001 -- dotenv raises a few things
            raise CredentialStoreError(
                name, f"failed to parse {path}: {exc}"
            ) from exc
        return {k: v for k, v in parsed.items() if v is not None}

    @staticmethod
    def _derive_name(path: Path) -> str:
        # Use the parent directory name as a discriminator: a ``.env``
        # next to ``robot.yaml`` becomes ``"env:<bot-slug>"``; one in
        # ``~/.cephix`` becomes ``"env:.cephix"``. Falls back to the
        # full path when neither parent name nor file stem are useful.
        parent = path.parent.name
        if parent and parent != ".":
            return f"env:{parent}"
        return f"env:{path}"
