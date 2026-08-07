"""
Regression: the obs transcript must contain everything Susie said, not just her
LLM turns.

Call CAe2120b (theorem_v3, 2026-08-06 23:17). The caller reached the booking
readback and went silent. What Susie actually said after that:

    the silence watchdog:  "Sorry, I didn't catch that. Shall I go ahead and book that in?"
    the dead-air net #1:   "Sorry, I can't quite hear you — how can I help today?"
    the dead-air net #2:   "I'm not able to hear you at the moment — feel free to
                            call back and we'll get that sorted for you."

None of those three is an LLM turn, and obs_turns was written only by
llm_stream._append_history — once per LLM turn. So the stored transcript ended
at the readback. The judge, reading a conversation that simply stopped, told the
operator by SMS that the caller had hung up. There are ~85 tts_text_queue.put
sites in the pipeline and a handful reach _append_history; the greeting, the
DTMF questions, the transfer line and the STT/pipeline failure phrases were all
invisible for the same reason.

The recorder now lives in connection.py's TTS loop, which every one of those
sites goes through. These tests pin the ordering contract that made it possible
to move it there — the assistant side is written DURING a turn and the caller
side at the END of one, so the caller's line has to be inserted, not appended.
"""
from __future__ import annotations

from app.obs import turns as obs_turns


def test_speech_that_is_not_an_llm_turn_is_still_recorded():
    """The three utterances that were missing from CAe2120b."""
    session: dict = {}
    obs_turns.mark_turn_start(session)
    obs_turns.record_assistant(
        session, "So that's John Smith, Monday the 10th of August at three in "
                 "the afternoon — shall I go ahead and book that in?")
    obs_turns.record_user(session, "you're yes sir")
    # …caller goes quiet. Nothing below is an LLM turn.
    obs_turns.record_assistant(
        session, "Sorry, I didn't catch that. Shall I go ahead and book that in?")
    obs_turns.record_assistant(
        session, "Sorry, I can't quite hear you — how can I help today?")
    obs_turns.record_assistant(
        session, "I'm not able to hear you at the moment — feel free to call "
                 "back and we'll get that sorted for you.")

    spoken = " ".join(t["text"] for t in session["obs_turns"]
                      if t["role"] == "assistant")
    assert "I'm not able to hear you" in spoken, (
        "the transcript still ends at the last LLM turn — the judge cannot see "
        "who ended the call and will guess"
    )
    assert session["obs_turns"][-1]["role"] == "assistant"


def test_the_caller_line_lands_before_the_replies_it_caused():
    """The assistant side is recorded during the turn, the caller side after it.

    A plain append would print every exchange backwards — reply first, then the
    question it answered — which reads to a judge as Susie ignoring the caller.
    """
    session: dict = {}
    obs_turns.mark_turn_start(session)
    obs_turns.record_assistant(session, "Sure — what day suits?")
    obs_turns.record_user(session, "I'd like to book")

    assert session["obs_turns"] == [
        {"role": "user", "text": "I'd like to book"},
        {"role": "assistant", "text": "Sure — what day suits?"},
    ]


def test_the_greeting_stays_ahead_of_the_first_caller_turn():
    """The insert must not reach back past speech from BEFORE this turn.

    The greeting is spoken before any caller turn exists, so there is no mark
    yet; the first mark must land after it.
    """
    session: dict = {}
    obs_turns.record_assistant(session, "Hi there, I'm Susie.")   # greeting
    obs_turns.mark_turn_start(session)
    obs_turns.record_assistant(session, "Sure — what day suits?")
    obs_turns.record_user(session, "I'd like to book")

    assert [t["text"] for t in session["obs_turns"]] == [
        "Hi there, I'm Susie.",
        "I'd like to book",
        "Sure — what day suits?",
    ]


def test_an_unmarked_caller_turn_appends_rather_than_reordering():
    """Not every caller utterance reaches run_turn (DTMF, direct-queued paths).

    Without a mark the safe answer is the end of the list: late is a small
    inaccuracy, in front of the greeting is a wrong transcript.
    """
    session: dict = {}
    obs_turns.record_assistant(session, "Hi there, I'm Susie.")
    obs_turns.record_user(session, "one")

    assert [t["role"] for t in session["obs_turns"]] == ["assistant", "user"]


def test_a_watchdog_replay_is_not_recorded_twice():
    """The TTS loop bypasses its dedup guard for watchdog re-asks deliberately —
    it is a real second playing. But two identical lines back to back read as a
    loop, and `loop` is a failure tag the judge reaches for."""
    session: dict = {}
    obs_turns.record_assistant(session, "Shall I go ahead and book that in?")
    obs_turns.record_assistant(session, "Shall I go ahead and book that in?")
    assert len(session["obs_turns"]) == 1


def test_blank_chunks_are_dropped():
    session: dict = {}
    obs_turns.record_assistant(session, "   ")
    obs_turns.record_user(session, "")
    assert not session.get("obs_turns")
