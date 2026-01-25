# app/tools/knowledge.py
from __future__ import annotations

import os
import re
from typing import List, Tuple

DEFAULT_KB_PATH = os.getenv("CLINIC_KB_PATH", "app/knowledge/clinic.md")


def _read_kb(path: str = DEFAULT_KB_PATH) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _split_into_sections(md: str) -> List[Tuple[str, str]]:
    """
    Split markdown into sections by headings.
    Returns list of (title, content).
    """
    lines = md.splitlines()
    sections: List[Tuple[str, List[str]]] = []
    cur_title = "General"
    cur: List[str] = []

    heading_re = re.compile(r"^\s*#{2,3}\s+(.*)\s*$")  # ## or ### headings

    for line in lines:
        m = heading_re.match(line)
        if m:
            # flush previous
            if cur:
                sections.append((cur_title, cur))
            cur_title = m.group(1).strip()
            cur = []
        else:
            cur.append(line)

    if cur:
        sections.append((cur_title, cur))

    return [(t, "\n".join(c).strip()) for t, c in sections if "\n".join(c).strip()]


def retrieve_knowledge(query: str, top_k: int = 3, path: str = DEFAULT_KB_PATH) -> List[str]:
    """
    Very simple scoring: counts keyword overlaps.
    Good enough for an MVP; upgrade later to embeddings if you want.
    """
    q = (query or "").lower()
    if not q.strip():
        return []

    md = _read_kb(path)
    sections = _split_into_sections(md)

    tokens = [t for t in re.findall(r"[a-z0-9']+", q) if len(t) >= 3]
    if not tokens:
        return []

    scored: List[Tuple[int, str]] = []
    for title, content in sections:
        text = f"{title}\n{content}".lower()
        score = sum(text.count(tok) for tok in tokens)
        if score > 0:
            snippet = f"## {title}\n{content}"
            scored.append((score, snippet))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:top_k]]
