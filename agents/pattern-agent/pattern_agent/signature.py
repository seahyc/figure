from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable


_WS = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")
_CODE = re.compile(r"\b[a-hj-np-z2-9]{6}\b", re.IGNORECASE)


@dataclass(frozen=True)
class PageSignature:
    key: str
    features: list[str]


def _stable_features(texts: Iterable[str], *, limit: int = 64) -> list[str]:
    feats: list[str] = []
    for t in texts:
        t = _WS.sub(" ", (t or "").strip().lower())
        if not t:
            continue
        # Canonicalize obvious per-run/per-step variance.
        t = _CODE.sub("{code}", t)
        t = _DIGITS.sub("{n}", t)
        if len(t) > 80:
            t = t[:80]
        feats.append(t)
        if len(feats) >= limit:
            break
    return sorted(set(feats))


def signature_from_dom_snapshot(*, visible_text: str, button_texts: list[str], has_canvas: bool, draggable_count: int) -> PageSignature:
    feats = []
    feats.extend(_stable_features([visible_text]))
    feats.extend(_stable_features(button_texts, limit=24))
    feats.append(f"canvas:{int(has_canvas)}")
    feats.append(f"draggable:{min(draggable_count, 20)}")
    blob = "\n".join(feats).encode()
    key = hashlib.sha256(blob).hexdigest()[:16]
    return PageSignature(key=key, features=feats)
