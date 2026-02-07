from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LearnedRecipe:
    signature_key: str
    skill_name: str
    params: dict[str, Any]
    successes: int = 1


class MemoryStore:
    """
    Very small, pragmatic pattern memory:
    - Keyed by a coarse signature string (hashable).
    - Stores which skill worked and what params were used.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path
        self._recipes: dict[str, LearnedRecipe] = {}
        if self._path:
            self._load()

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            for item in raw.get("recipes", []):
                r = LearnedRecipe(
                    signature_key=item["signature_key"],
                    skill_name=item["skill_name"],
                    params=item.get("params", {}),
                    successes=int(item.get("successes", 1)),
                )
                self._recipes[r.signature_key] = r
        except Exception:
            # Corrupt memory should not break runs.
            self._recipes = {}

    def save(self) -> None:
        if not self._path:
            return
        payload = {"recipes": [asdict(r) for r in self._recipes.values()]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2))

    def get(self, signature_key: str) -> LearnedRecipe | None:
        return self._recipes.get(signature_key)

    def record_success(self, signature_key: str, *, skill_name: str, params: dict[str, Any]) -> None:
        cur = self._recipes.get(signature_key)
        if cur and cur.skill_name == skill_name and cur.params == params:
            self._recipes[signature_key] = LearnedRecipe(
                signature_key=signature_key,
                skill_name=skill_name,
                params=params,
                successes=cur.successes + 1,
            )
        else:
            self._recipes[signature_key] = LearnedRecipe(
                signature_key=signature_key,
                skill_name=skill_name,
                params=params,
                successes=1,
            )

