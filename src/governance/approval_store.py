from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.governance.domain import ApprovalRule, ApprovalScope
from src.utils import utc_now_iso

logger = logging.getLogger(__name__)


class FileApprovalStore:
    """Persists approval rules as JSON-Lines on disk.

    Thread-safety: append-only writes; reads scan the full file each time.
    Good enough for MVP volumes.  A database-backed store can replace this
    later without changing the port interface.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def check(
        self,
        principal_id: str,
        action: str,
        source_scope: str | None = None,
        target_scope: str | None = None,
    ) -> ApprovalRule | None:
        """Return the most specific matching rule, or None."""
        rules = self._load_all()
        best: ApprovalRule | None = None
        best_specificity = -1

        for rule in rules:
            if rule.principal_id != principal_id:
                continue
            if rule.action != action:
                continue

            specificity = 0
            if rule.source_scope is not None:
                if rule.source_scope != source_scope:
                    continue
                specificity += 1
            if rule.target_scope is not None:
                if rule.target_scope != target_scope:
                    continue
                specificity += 1

            if rule.scope == ApprovalScope.ONCE:
                self._consume_once_rule(rule)
                return rule

            if specificity > best_specificity:
                best = rule
                best_specificity = specificity

        return best

    def grant(self, rule: ApprovalRule) -> None:
        record = {
            "principal_id": rule.principal_id,
            "action": rule.action,
            "source_scope": rule.source_scope,
            "target_scope": rule.target_scope,
            "scope": rule.scope.value,
            "granted_by": rule.granted_by,
            "granted_at": rule.granted_at,
            "expires_at": rule.expires_at,
            "sop_name": rule.sop_name,
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info("Approval granted: %s %s (scope=%s)", rule.action, rule.principal_id, rule.scope.value)

    def revoke(
        self,
        principal_id: str,
        action: str,
        source_scope: str | None = None,
        target_scope: str | None = None,
    ) -> bool:
        rules = self._load_all()
        remaining: list[ApprovalRule] = []
        removed = False
        for rule in rules:
            if (
                rule.principal_id == principal_id
                and rule.action == action
                and rule.source_scope == source_scope
                and rule.target_scope == target_scope
            ):
                removed = True
            else:
                remaining.append(rule)
        if removed:
            self._write_all(remaining)
        return removed

    def _load_all(self) -> list[ApprovalRule]:
        if not self._path.exists():
            return []
        rules: list[ApprovalRule] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                rules.append(ApprovalRule(
                    principal_id=data["principal_id"],
                    action=data["action"],
                    source_scope=data.get("source_scope"),
                    target_scope=data.get("target_scope"),
                    scope=ApprovalScope(data["scope"]),
                    granted_by=data.get("granted_by", ""),
                    granted_at=data.get("granted_at", ""),
                    expires_at=data.get("expires_at"),
                    sop_name=data.get("sop_name"),
                ))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Skipping malformed approval rule: %s", exc)
        return rules

    def _write_all(self, rules: list[ApprovalRule]) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            for rule in rules:
                record = {
                    "principal_id": rule.principal_id,
                    "action": rule.action,
                    "source_scope": rule.source_scope,
                    "target_scope": rule.target_scope,
                    "scope": rule.scope.value,
                    "granted_by": rule.granted_by,
                    "granted_at": rule.granted_at,
                    "expires_at": rule.expires_at,
                    "sop_name": rule.sop_name,
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _consume_once_rule(self, rule: ApprovalRule) -> None:
        """Remove a ``once`` rule after it has been consumed."""
        self.revoke(
            principal_id=rule.principal_id,
            action=rule.action,
            source_scope=rule.source_scope,
            target_scope=rule.target_scope,
        )
