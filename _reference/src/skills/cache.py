from __future__ import annotations

from collections import OrderedDict

from src.skills.models import SkillDefinition
from src.skills.ports import SkillRepositoryPort


class LRUSkillCache:
    """LRU-caching decorator around a ``SkillRepositoryPort``.

    Caches individual skill lookups up to ``max_size`` entries.
    ``list_available`` always delegates to the underlying repository.
    """

    def __init__(self, repository: SkillRepositoryPort, *, max_size: int = 64) -> None:
        self._repository = repository
        self._max_size = max_size
        self._cache: OrderedDict[str, SkillDefinition | None] = OrderedDict()

    def list_available(self) -> list[SkillDefinition]:
        return self._repository.list_available()

    def get_skill(self, name: str) -> SkillDefinition | None:
        if name in self._cache:
            self._cache.move_to_end(name)
            return self._cache[name]

        skill = self._repository.get_skill(name)
        self._cache[name] = skill
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
        return skill

    def invalidate(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)
