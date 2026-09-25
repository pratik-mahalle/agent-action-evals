"""Run-local state. Only trusted tool handlers receive World."""

from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any, Mapping


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def same(a: Any, b: Any) -> bool:
    return canonical(a) == canonical(b)


def select(state: Any, path: str) -> Any:
    current = state
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return copy.deepcopy(current)


class World:
    def __init__(self, initial: Mapping[str, Any]):
        document = canonical(initial)
        self._sealed = False
        self._db = sqlite3.connect(":memory:")
        self._db.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, document TEXT NOT NULL)")
        self._db.execute("INSERT INTO state VALUES (1, ?)", (document,))
        self._db.commit()

    def snapshot(self) -> dict[str, Any]:
        return json.loads(self._db.execute("SELECT document FROM state WHERE id=1").fetchone()[0])

    def get(self, path: str) -> Any:
        return select(self.snapshot(), path)

    def set(self, path: str, value: Any) -> None:
        if self._sealed:
            raise RuntimeError("World is sealed; run has ended")
        state = self.snapshot()
        current = state
        parts = path.split(".")
        for part in parts[:-1]:
            if part not in current or not isinstance(current[part], dict):
                raise KeyError(path)
            current = current[part]
        current[parts[-1]] = copy.deepcopy(value)
        self._db.execute("UPDATE state SET document=? WHERE id=1", (canonical(state),))
        self._db.commit()

    def seal(self) -> None:
        self._sealed = True

    def close(self) -> None:
        self.seal()
        self._db.close()


def changes(before: Any, after: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        result = {}
        for key in sorted(before.keys() | after.keys()):
            path = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                result[path] = {
                    "before_exists": key in before,
                    "after_exists": key in after,
                    "before": before.get(key),
                    "after": after.get(key),
                }
            else:
                result.update(changes(before[key], after[key], path))
        return result
    if not same(before, after):
        return {
            prefix: {"before_exists": True, "after_exists": True, "before": before, "after": after}
        }
    return {}
