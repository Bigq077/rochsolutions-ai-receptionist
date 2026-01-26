# app/tools/knowledge.py
from __future__ import annotations

import os
from pathlib import Path
from typing import List


DEFAULT_KNOWLEDGE_PATH = Path("app/knowledge/clinic.md")


def _read_text_file(path: Path) -> str:
    """
    Safe UTF-8 reader. Won't crash on missing file.
    """
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _simple_chunk(text: str, max_chars: int = 1200) -> List[str]:
    """
    Very simple chunker:
    - splits on blank lines
    - merges into chunks <= max_chars
    """
    if not text.strip():
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    buf = ""

    for p in paragraphs:
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


def _score(query: str, chunk: str) -> int:
    """
    Naive keyword overlap scorer.
    Good enough for a demo.
    """
    q = query.lower()
    c = chunk.lower()

    # lightweight tokenisation
    q_words = {w for w in q.replace(",", " ").replace(".", " ").split() if len(w) > 2}
    if not q_words:
        return 0

    score = 0
    for w in q_words:
        if w in c:
            score += 1

    return score


def retrieve_knowledge(
    query: str,
    top_k: int = 3,
    path: str | None = None,
) -> str:
    """
    Returns top_k relevant chunks from clinic.md as a single string.
    If no knowledge is available, returns an empty string.
    """
    knowledge_path = Path(path) if path else DEFAULT_KNOWLEDGE_PATH
    text = _read_text_file(knowledge_path)
    chunks = _simple_chunk(text)

    if not chunks:
        return ""

    ranked = sorted(chunks, key=lambda ch: _score(query, ch), reverse=True)
    picked = [ch for ch in ranked[:top_k] if ch.strip()]

    return "\n\n---\n\n".join(picked)




