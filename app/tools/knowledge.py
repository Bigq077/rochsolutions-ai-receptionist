# app/tools/knowledge.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple
import re

_KB_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "clinic.md"


def _load_kb_text() -> str:
    if not _KB_PATH.exists():
        return ""
    # UTF-8 handles curly quotes etc.
    return _KB_PATH.read_text(encoding="utf-8", errors="ignore")


def _split_into_chunks(text: str, max_chars: int = 900) -> List[str]:
    """
    Very simple chunking: split by blank lines, then re-pack into chunks.
    """
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in parts:
        if not buf:
            buf = p
            continue
        if len(buf) + 2 + len(p) <= max_chars:
            buf = buf + "\n\n" + p
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def retrieve_knowledge(query: str, clinic: Dict[str, Any] | None = None, top_k: int = 2) -> str:
    """
    Lightweight retrieval without embeddings:
    - loads app/knowledge/clinic.md
    - returns the most relevant chunk(s) by keyword overlap
    """
    kb = _load_kb_text()
    if not kb:
        return ""

    q = (query or "").lower()
    if not q:
        return ""

    chunks = _split_into_chunks(kb)

    # Tokenize query into keywords
    q_words = set(re.findall(r"[a-z0-9]+", q))
    if not q_words:
        return ""

    scored: List[Tuple[int, str]] = []
    for ch in chunks:
        ch_l = ch.lower()
        ch_words = set(re.findall(r"[a-z0-9]+", ch_l))
        score = len(q_words.intersection(ch_words))
        if score > 0:
            scored.append((score, ch))

    if not scored:
        # If nothing matches, return a short "about the clinic" top chunk
        return chunks[0][:1200]

    scored.sort(key=lambda x: x[0], reverse=True)
    best = [c for _, c in scored[: max(1, int(top_k))]]
    return "\n\n---\n\n".join(best)[:2000]


