# app/tools/knowledge.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Tuple
import re


DEFAULT_KNOWLEDGE_PATH = Path("app/knowledge/clinic.md")


def _read_text(path: Path) -> str:
    # Read as UTF-8 and ignore weird characters rather than crashing deploy
    return path.read_text(encoding="utf-8", errors="ignore")


def _chunk(text: str, max_chars: int = 900) -> List[str]:
    """
    Split into small chunks so retrieval is stable.
    We chunk on blank lines first, then fallback to hard split.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks: List[str] = []
    buf = ""
    for b in blocks:
        if len(buf) + len(b) + 2 <= max_chars:
            buf = (buf + "\n\n" + b).strip()
        else:
            if buf:
                chunks.append(buf)
            if len(b) <= max_chars:
                buf = b
            else:
                # hard split long block
                for i in range(0, len(b), max_chars):
                    chunks.append(b[i : i + max_chars])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _score(query: str, chunk: str) -> float:
    """
    Very simple keyword score (no embeddings).
    Good enough for a demo knowledge base.
    """
    q = query.lower()
    c = chunk.lower()

    # keywords = words longer than 2 chars
    q_words = [w for w in re.findall(r"[a-z0-9]+", q) if len(w) > 2]
    if not q_words:
        return 0.0

    hits = sum(1 for w in q_words if w in c)

    # bonus if query contains bigrams that appear in chunk
    bigrams = [" ".join(q_words[i : i + 2]) for i in range(len(q_words) - 1)]
    bonus = sum(1 for b in bigrams if b in c)

    return float(hits + 2 * bonus)


def retrieve_knowledge(
    query: str,
    path: Path = DEFAULT_KNOWLEDGE_PATH,
    top_k: int = 3,
) -> str:
    """
    Returns up to top_k relevant chunks from clinic.md.
    If file missing, returns empty string (safe).
    """
    try:
        if not path.exists():
            return ""
        text = _read_text(path).strip()
        if not text:
            return ""

        chunks = _chunk(text)
        scored: List[Tuple[float, str]] = [( _score(query, ch), ch) for ch in chunks]
        scored.sort(key=lambda x: x[0], reverse=True)

        best = [ch for s, ch in scored if s > 0][:top_k]
        if not best:
            return ""

        # Keep short-ish so it fits into LLM context
        out = "\n\n---\n\n".join(best)
        return out[:2200]
    except Exception:
        return ""





