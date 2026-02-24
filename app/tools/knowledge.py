# app/tools/knowledge.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
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
    Good enough for a per-clinic knowledge base.
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


def _resolve_path(clinic: Optional[Dict[str, Any]] = None) -> Path:
    """
    Resolve which knowledge file to use.

    Priority:
      1. app/clinics/{clinic_id}/knowledge.md  (per-clinic file)
      2. app/knowledge/clinic.md               (shared fallback)
    """
    if clinic:
        clinic_id = (clinic.get("clinic_id") or "").strip()
        if clinic_id:
            per_clinic = Path(f"app/clinics/{clinic_id}/knowledge.md")
            if per_clinic.exists():
                text = per_clinic.read_text(encoding="utf-8", errors="ignore").strip()
                if text:  # only use if non-empty
                    return per_clinic
    return DEFAULT_KNOWLEDGE_PATH


def retrieve_knowledge(
    query: str,
    path: Path = DEFAULT_KNOWLEDGE_PATH,
    top_k: int = 3,
    clinic: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Returns up to top_k relevant chunks from the clinic knowledge base.

    If 'clinic' dict is provided with a 'clinic_id', tries to load from
    app/clinics/{clinic_id}/knowledge.md first. Falls back to DEFAULT_KNOWLEDGE_PATH.
    If file missing or empty, returns empty string (safe).
    """
    actual_path = _resolve_path(clinic) if clinic else path
    try:
        if not actual_path.exists():
            return ""
        text = _read_text(actual_path).strip()
        if not text:
            return ""

        chunks = _chunk(text)
        scored: List[Tuple[float, str]] = [(_score(query, ch), ch) for ch in chunks]
        scored.sort(key=lambda x: x[0], reverse=True)

        best = [ch for s, ch in scored if s > 0][:top_k]
        if not best:
            return ""

        # Keep short-ish so it fits into LLM context
        out = "\n\n---\n\n".join(best)
        return out[:2200]
    except Exception:
        return ""
