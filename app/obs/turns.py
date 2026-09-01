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
suppressed text is not recorded (that was the second half of the same defect: the
judge quoted a suppressed "Let me just check what we have for you." back as a
redundant step Susie had added).

WHAT THIS LIST IS, EXACTLY: INTENT TO SPEAK — NOT AUDIO
------------------------------------------------------
P2 of OPEN_DEFECTS_HOLD_AND_SLOTS_2026-09-01. This docstring used to say "text
the caller never heard is never recorded", and connection.py's comment at the
call site said the same. **Both were false, and the claim is what made them
expensive.** The seam is past every *suppression* check, but it sits BEFORE
`split_tts_text`, before synthesis and before playback. Three things can still
take the audio away afterwards:

  * a barge-in cancelling synthesis part-way through the sub-chunks;
  * a barge-in tearing down PLAYBACK after synthesis completed — the audio was
    already in Twilio's buffer and the teardown flushed it (this is P1,
    CAa2bdff2b: 12.2s of appointment times synthesised, ~1.9s heard, four
    assistant entries stored);
  * the TTS loop rewriting a chunk before synthesis, which `record_assistant`
    is now given the rewritten form of.

Two sessions read this list as audio and diagnosed working features as broken —
once reporting a live, working disclosure as missing. So: **a fragment here means
Susie was about to say this, not that anyone heard it.**

`cut` (see `note_cut`) closes the first case only. Its ABSENCE is not evidence
the fragment was heard — the playback case leaves no mark here at all, and
answering it would mean threading chunk identity through `_send_loop`'s playout
clock. Cross-check `[ms_tts] tts_finished` and `barge-in start` in the Render
log before concluding a caller heard anything.

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


def note_cut(session: Dict[str, Any], *, spoke: int, of: int) -> None:
    """Mark the fragment just recorded as cut off part-way through synthesis.

    Called from the TTS loop when a barge-in cancels the sub-chunk loop, which
    is the ONE downstream loss knowable at that seam without following the audio
    into `_send_loop`. `spoke`/`of` are sub-chunks, not seconds: coarse, but the
    difference between "she said this" and "she got a third of the way in".

    The text is deliberately NOT truncated to what was spoken. `split_tts_text`
    works on the post-substitution string ("oh seven five oh two"), while this
    list stores the pre-substitution form on purpose so it reads back as
    English — so the sub-chunks cannot be spliced into it without corrupting
    every phone number in the corpus. A marker on the whole fragment is honest;
    a spliced one would not be.

    Absence of this marker is NOT evidence the fragment was heard — see the
    module docstring. It says nothing about the playback case.
    """
    turns = _turns(session)
    if not turns or turns[-1].get("role") != "assistant":
        return
    turns[-1]["cut"] = {"spoke": int(spoke), "of": int(of)}
