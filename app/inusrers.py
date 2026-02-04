# app/insurers.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class InsurerMatch:
    normalized: str
    display_name: str
    accepted: Optional[bool]   # True / False / None
    confidence: float          # 0..1

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def match_insurer(user_text: str, accepted_map: dict[str, bool]) -> InsurerMatch:
    raw = user_text or ""
    n = _norm(raw)
    if not n:
        return InsurerMatch(normalized="", display_name="", accepted=None, confidence=0.0)

    if n in accepted_map:
        return InsurerMatch(normalized=n, display_name=n.upper(), accepted=accepted_map[n], confidence=1.0)

    aliases = {
        "axa ppp": "axa",
        "axa health": "axa",
        "bupa global": "bupa",
        "vitality health": "vitality",
        "aviva health": "aviva",
    }
    if n in aliases and aliases[n] in accepted_map:
        key = aliases[n]
        return InsurerMatch(normalized=key, display_name=key.upper(), accepted=accepted_map[key], confidence=0.95)

    # conservative substring match
    for key in accepted_map.keys():
        if key in n or n in key:
            return InsurerMatch(normalized=key, display_name=key.upper(), accepted=accepted_map[key], confidence=0.80)

    return InsurerMatch(normalized=n, display_name=raw.strip(), accepted=None, confidence=0.40)
