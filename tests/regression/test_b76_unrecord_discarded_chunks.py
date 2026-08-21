# tests/regression/test_b76_unrecord_discarded_chunks.py
"""
B-76 — Susie's record of what she said included a sentence the caller never heard.

JV `CAe84b871bcbd8da1c08b8421c3d5705b1`, 21 Aug 2026, build cc747083e9cd.

    21:23:36  "Shall I go ahead and move it for you?"      <- asked, HEARD
    21:23:49  caller: "uh yeah go for it"                  <- approved
    21:23:58  tts_inhibit: discarding stale chunk
              'Quick question before I lock that in - would you like the 30'
                                                           <- NEVER heard
    21:24:07  reschedule_appointment BLOCKED - the move confirmation question was
              never asked (last_bot_prompt='Quick question before I lock that in
              - would you like the 30-minute session at f')

The caller had approved the move. The write was refused because
`last_bot_prompt` named a question that was generated, recorded, and then thrown
away before it reached TTS.

Mechanism. `_record_spoken` runs synchronously inside the turn, immediately
before the chunk is put on `tts_text_queue`. connection.py's `_tts_loop` then
dequeues it and can still drop it - `tts_inhibit` after a confirmed barge-in, a
cancelled ack filler, a cancelled pre-slot chunk. Nothing ever corrected the
record, and `last_bot_prompt` is derived from it. `_record_spoken`'s own
docstring conceded the seam ("strictly closer to what was spoken, not perfectly
equal to it") but named only `_pre_slot_cancelled`; this call shows the cost is
a refused write, not an inaccurate transcript.

Recording early is deliberate and stays: the TTS loop is async, and anything
read from it at turn end is empty or partial (CA7e389a47). So the record is
optimistic by design, and B-76 adds the correction:

  * `_spoken_this_turn` is now backed by a LIST, so a dropped chunk is removed
    exactly rather than by substring surgery on the joined form.
  * `_unrecord_spoken` is called from the `tts_inhibit` drop.
  * EXCEPT for slot chunks saved for re-presentation: connection.py can clear
    the inhibit and re-queue them so they DO play, and un-recording those would
    be wrong. They are un-recorded only if that recovery gives up.

Two traps this file pins, both found while building the fix and either of which
would have silently defeated it:

  1. Turn end falls back to `sanitise_response(full_reply)` when nothing was
     recorded. Un-record everything and that fallback puts the unheard sentence
     straight back into `last_bot_prompt`. The presence of the backing key
     separates "seam used, all dropped" from "seam never used".
  2. Blanking `last_bot_prompt` does not help either - an empty prompt fails the
     move gate exactly as the wrong prompt did. A turn that said nothing leaves
     the field alone, because the last thing Susie said is still the previous
     turn's question, which is what the caller is still answering.
"""
from __future__ import annotations

import inspect

import pytest

from app.media_streams import llm_stream as ls


MOVE_CTA = (
    "Just to confirm - I am moving your appointment to Monday the 24th of "
    "August at half past four in the afternoon. "
    "Shall I go ahead and move it for you?"
)
DURATION_Q = (
    "Quick question before I lock that in - would you like the 30-minute "
    "session or the 60-minute one?"
)


# ══════════════════════════════════════════════════════════════════════════
# 1 — the helper removes exactly what was dropped
# ══════════════════════════════════════════════════════════════════════════
def test_a_dropped_chunk_leaves_the_record():
    s = {}
    for c in ("One.", "Two.", "Three."):
        ls._record_spoken(s, c)
    ls._unrecord_spoken(s, "Two.")
    assert s["_spoken_this_turn"] == "One. Three."


def test_a_repeated_phrase_loses_only_the_last_copy():
    """Chunks are appended in order and dropped in order."""
    s = {}
    for c in ("Hi.", "Hi."):
        ls._record_spoken(s, c)
    ls._unrecord_spoken(s, "Hi.")
    assert s["_spoken_this_turn"] == "Hi."
    assert s[ls.SPOKEN_CHUNKS_KEY] == ["Hi."]


@pytest.mark.parametrize("junk", ["", "   ", None])
def test_unrecording_nothing_is_a_no_op(junk):
    s = {}
    ls._record_spoken(s, "Real.")
    ls._unrecord_spoken(s, junk)
    assert s["_spoken_this_turn"] == "Real."


def test_unrecording_a_chunk_that_was_never_recorded_is_a_no_op():
    s = {}
    ls._record_spoken(s, "Real.")
    ls._unrecord_spoken(s, "Never said this.")
    assert s["_spoken_this_turn"] == "Real."


def test_unrecording_on_an_empty_session_does_not_raise():
    """The drop arrives from the async TTS loop; the session may be anything."""
    ls._unrecord_spoken({}, "x")
    ls._unrecord_spoken({ls.SPOKEN_CHUNKS_KEY: "not-a-list"}, "x")


def test_the_backing_store_is_json_serialisable():
    """The session round-trips through Redis with json.dumps."""
    import json
    s = {}
    ls._record_spoken(s, "One.")
    assert json.loads(json.dumps(s))[ls.SPOKEN_CHUNKS_KEY] == ["One."]


# ══════════════════════════════════════════════════════════════════════════
# 2 — trap 1: a fully-dropped turn must not fall back to the raw reply
# ══════════════════════════════════════════════════════════════════════════
def test_the_key_survives_so_a_fully_dropped_turn_is_distinguishable():
    """`_unrecord_spoken` must leave the key present, not delete it.

    Its PRESENCE is what separates "the seam was used and everything was
    dropped" from "nothing ever reached the seam". Delete it and turn end falls
    back to full_reply, which contains the unheard sentence - the defect,
    restored.
    """
    s = {}
    ls._record_spoken(s, DURATION_Q)
    ls._unrecord_spoken(s, DURATION_Q)
    assert ls.SPOKEN_CHUNKS_KEY in s, "the seam marker was deleted"
    assert s[ls.SPOKEN_CHUNKS_KEY] == []
    assert s["_spoken_this_turn"] == ""


def test_turn_end_gates_the_fallback_on_the_seam_marker():
    src = inspect.getsource(ls.LLMStream.run_turn)
    assert "_seam_used = isinstance(session.get(SPOKEN_CHUNKS_KEY), list)" in src
    assert "_nothing_spoken = _seam_used and not _spoken_turn" in src
    assert "elif _nothing_spoken:" in src, (
        "the fallback is no longer gated - a fully dropped turn will reach for "
        "full_reply and re-introduce the unheard sentence"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3 — trap 2: a silent turn must not blank the outstanding question
# ══════════════════════════════════════════════════════════════════════════
def test_a_silent_turn_leaves_last_bot_prompt_and_last_question_alone():
    src = inspect.getsource(ls.LLMStream.run_turn)
    assert "if not _nothing_spoken:\n                session[F_LAST_BOT_PROMPT]" in src, (
        "last_bot_prompt is blanked by a silent turn - an empty prompt fails "
        "the move gate exactly as the wrong prompt did"
    )
    assert "if not _nothing_spoken:\n                session[F_LAST_QUESTION]" in src


def test_the_live_sequence_no_longer_blocks_the_move():
    """CAe84b871b end to end, against the real gate predicate.

    Turn A speaks the move CTA. Turn B generates a duration question that the
    TTS loop discards. The gate must still see the move CTA.
    """
    session = {}

    # Turn A - spoken and heard.
    ls._record_spoken(session, MOVE_CTA)
    seam = isinstance(session.get(ls.SPOKEN_CHUNKS_KEY), list)
    spoken = (session.pop("_spoken_this_turn", "") or "").strip()
    session.pop(ls.SPOKEN_CHUNKS_KEY, None)
    if not (seam and not spoken):
        session[ls.F_LAST_BOT_PROMPT] = spoken
    assert ls._move_confirmation_asked(session[ls.F_LAST_BOT_PROMPT])

    # Turn B - generated, recorded, then dropped by tts_inhibit.
    ls._record_spoken(session, DURATION_Q)
    ls._unrecord_spoken(session, DURATION_Q)
    seam = isinstance(session.get(ls.SPOKEN_CHUNKS_KEY), list)
    spoken = (session.pop("_spoken_this_turn", "") or "").strip()
    session.pop(ls.SPOKEN_CHUNKS_KEY, None)
    if not (seam and not spoken):
        session[ls.F_LAST_BOT_PROMPT] = spoken

    assert ls._move_confirmation_asked(session[ls.F_LAST_BOT_PROMPT]), (
        "the move gate lost the CTA to a turn the caller never heard - "
        "reschedule_appointment is refused although the caller approved it"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4 — lifetime: a late drop must never resurrect a finished turn
# ══════════════════════════════════════════════════════════════════════════
def test_a_drop_arriving_after_turn_end_cannot_resurrect_the_turn():
    """The TTS loop is async and can drop a chunk after the turn has ended."""
    s = {}
    ls._record_spoken(s, "Heard this.")
    s.pop("_spoken_this_turn", None)
    s.pop(ls.SPOKEN_CHUNKS_KEY, None)          # turn end
    ls._unrecord_spoken(s, "Heard this.")      # late drop
    assert "_spoken_this_turn" not in s, "a finished turn was resurrected"


def test_turn_end_pops_the_backing_store_with_the_string():
    src = inspect.getsource(ls.LLMStream.run_turn)
    i_str = src.index('session.pop("_spoken_this_turn", "")')
    i_key = src.index("session.pop(SPOKEN_CHUNKS_KEY, None)", i_str)
    assert i_key - i_str < 700, (
        "the backing store is not popped with the string at turn end"
    )


def test_the_backing_store_is_cleared_at_the_top_of_every_turn():
    """Two independent sites in two different methods, both load-bearing.

    run_turn pops it at TURN END so a late drop cannot resurrect a finished
    turn; _streaming_tool_loop clears it at the TOP of every turn so a turn
    cannot inherit the previous one's chunks.
    """
    assert "session.pop(SPOKEN_CHUNKS_KEY, None)" in inspect.getsource(
        ls.LLMStream.run_turn
    ), "turn end no longer pops the backing store"
    assert "session.pop(SPOKEN_CHUNKS_KEY, None)" in inspect.getsource(
        ls.LLMStream._streaming_tool_loop
    ), "the top-of-turn clear is missing - a turn will inherit stale chunks"


# ══════════════════════════════════════════════════════════════════════════
# 5 — the wiring, and the carve-out that keeps re-presented slots correct
# ══════════════════════════════════════════════════════════════════════════
def test_the_tts_loop_unrecords_a_discarded_chunk():
    from app.media_streams import connection as conn
    src = inspect.getsource(conn)
    assert "_unrecord_spoken(self.session, _obs_chunk_text)" in src, (
        "the tts_inhibit drop no longer corrects the record"
    )


def test_it_unrecords_the_PRE_substitution_form():
    """`_record_spoken` was given the pre-substitution text.

    Un-recording the substituted form ("Awlstuh" for "Alcester") would never
    match, and the correction would silently do nothing.
    """
    from app.media_streams import connection as conn
    src = inspect.getsource(conn)
    i_obs = src.index("_obs_chunk_text = chunk_text")
    i_sub = src.index("chunk_text = _apply_tts_subs(chunk_text)", i_obs)
    assert i_obs < i_sub, "the obs form is captured after substitution"
    assert "_unrecord_spoken(self.session, chunk_text)" not in src, (
        "un-recording the substituted form - it cannot match what was recorded"
    )


def test_slot_chunks_held_for_re_presentation_are_not_unrecorded():
    """connection.py can clear the inhibit and re-queue slot chunks so they DO
    play. Un-recording those would under-record real speech - the opposite
    defect. They are corrected only if the re-presentation gives up.
    """
    from app.media_streams import connection as conn
    src = inspect.getsource(conn)
    assert "if not _saved_for_represent:" in src, (
        "the re-presentation carve-out is gone - re-presented slot lines will "
        "be missing from the record of what was said"
    )
    assert "_saved_for_represent = True" in src
    # and the give-up branch does correct them
    assert "for _oc in self._inhibited_slot_obs:" in src


def test_the_two_inhibited_slot_lists_stay_in_step():
    """A length mismatch would un-record the wrong sentence."""
    from app.media_streams import connection as conn
    src = inspect.getsource(conn)
    assert src.count("self._inhibited_slot_chunks = []") == src.count(
        "self._inhibited_slot_obs = []"
    ), "the parallel lists are reset a different number of times"
