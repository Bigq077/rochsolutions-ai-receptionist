# app/flows/brain.py
from __future__ import annotations

from typing import Tuple, Dict, Any

from app.flows.triage import triage_turn


def _append_turn(session: dict, role: str, text: str) -> dict:
    if not text:
        return session
    turns = session.get("turns", [])
    turns.append({"role": role, "text": text})
    session["turns"] = turns
    return session


async def handle_user_text(user_text: str, session: dict) -> Tuple[str, dict]:
    """
    Shared brain handler for any channel (Twilio, Avatar web, etc.)
    - adds user turn
    - runs triage_turn (your real AI logic)
    - adds assistant turn
    - returns reply text + updated session
    """
    session = _append_turn(session, "user", user_text)

    reply_text, session = await triage_turn(user_text, session)

    session = _append_turn(session, "assistant", reply_text)
    return reply_text, session
