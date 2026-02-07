from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


CODE_RE = re.compile(r"\b[A-HJ-NP-Z2-9]{6}\b")


@dataclass
class SkillResult:
    ok: bool
    notes: str = ""
    params: dict[str, Any] | None = None


SkillFn = Callable[..., Awaitable[SkillResult]]


def normalize_code(s: str) -> str | None:
    if not s:
        return None
    s = s.strip().upper()
    if CODE_RE.fullmatch(s):
        return s
    return None
