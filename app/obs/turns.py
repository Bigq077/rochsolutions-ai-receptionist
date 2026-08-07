"""
app/obs/turns.py
----------------
The observability transcript: session["obs_turns"].

This list is what app/obs/store.capture_call persists and what app/obs/judge.py
scores — and the judge's free-text verdict is the body of the operator CALL BACK
SMS. So whatever is missing from here, the judge invents.

It used to be built entirely inside llm_stream._append_history, which runs once
per LLM turn. Everything Susie says that is NOT an LLM turn was therefore absent:
the greeting, the silence-watchdog re-asks, the dead-air safety net's two re-asks
and its sign-off, the DTMF-driven questions, the transfer line, the pipeline and
STT failure phrases. There are ~85 tts_text_queue.put sites and only a handful go
through _append_history.

The visible cost, call CAe2120b (theorem_v3, 2026-08-06): the caller reached the
booking readback and went quiet. Susie re-asked three times and signed off. The
stored transcript ended at the readback, because none of those three re-asks was
an LLM turn — so the judge saw a conversation that simply stopped, and texted the
operator that the caller had hung up. They had not.

The fix is to record the assistant side at the one seam every utterance passes
through: connection.py's TTS loop, at the point a chunk is about to be spoken —
after the pre-slot suppression, the tts_inhibit discard and the dedup guard, so
text the caller never heard is never recorded (that was the second half of the
same defect: the judge quoted a suppressed "Let me just check what we have for
you." back as a redundant step Susie had added).

Fragments are NOT merged into one entry per utterance. A merge rule needs to know
where one utterance ends and the next begins, and at this seam it does not — the
gap between two chunks of one sentence and the gap before a watchdog re-ask are
both "a few seconds". Fragment-per-line is faithful and reads fine:

    ASSISTANT: So that's John Smith, Monday the 10th of August at three in the afternoon —
    ASSISTANT: shall I go ahead and book that in?

Ordering: assistant fragments are recorded DURING a turn, the caller's utterance
is recorded at the END of it (llm_stream._append_history), so a plain append would
put every exchange backwards. `mark_turn_start` records the insert point at the
top of run_turn and `record_user` inserts there.
"""
from __future__ import annotations

from typing import Any, Dict, List

_TURN_START = "_obs_turn_start"


def _turns(session: Dict[str, Any]) -> List[Dict[str, str]]:
    return session.setdefault("obs_turns", [])


def mark_turn_start(session: Dict[str, Any]) -> None:
    """Remember where this caller turn begins, so record_user can insert there.

    Called at the top of LLMStream.run_turn, before anything is spoken.
    """
    session[_TURN_START] = len(_turns(session))


def record_user(session: Dict[str, Any], text: str) -> None:
    """Record what the caller said, positioned before this turn's replies."""
    text = (text or "").strip()
    if not text:
        return
    turns = _turns(session)
    idx = session.pop(_TURN_START, None)
    if not isinstance(idx, int) or not (0 <= idx <= len(turns)):
        # No marker (or a stale one): the safe fallback is the end of the list.
        idx = len(turns)
    turns.insert(idx, {"role": "user", "text": text})


def record_assistant(session: Dict[str, Any], text: str) -> None:
    """Record one fragment Susie is about to speak.

    Call ONLY from the TTS loop, once a chunk has survived every suppression
    check. Consecutive byte-identical fragments are dropped: the loop's own dedup
    guard is bypassed for watchdog re-asks, and a re-ask that repeats the
    previous line verbatim would otherwise read as Susie saying it twice.
    """
    text = (text or "").strip()
    if not text:
        return
    turns = _turns(session)
    if turns and turns[-1].get("role") == "assistant" and turns[-1].get("text") == text:
        return
    turns.append({"role": "assistant", "text": text})
