"""
P3: a bare "Right —" reaching callers — three fragments before a question.

OPEN_DEFECTS_HOLD_AND_SLOTS_2026-09-01.md, evidenced on the demo line:

    assi  Let's get you booked in —
    assi  Right —
    assi  What's the appointment for?

CA6a59e59f0a67fe, CA320e6b1cb78217, CA9fda59b3a01981 and 29 more across the
stored corpus (32 in 8,639 assistant chunks; 798 calls).

WHAT IT IS NOT
--------------
The defect doc reads this as "the model echoing the head's register", i.e. a
generation problem needing a prompt fix. It is not. The prompt MANDATES it:

    clinic_template_prompt.py:2266  "acknowledge simply: 'Right —' and NOTHING
                                     ELSE. This phrase is your ..."

because connection.py injects the next question itself once it sees that ack
(`_next_question_after_booking_ack`). The stub is deliberate, and it reads
correctly when it is the ONLY acknowledgement the caller hears — which it is on
every turn where the model beats the 600ms head delay and no head plays at all.

The defect is a RACE, not a register. When the head wins, the same contentless
speech act is performed twice, ~1s apart, and the second time carries no words.
This is the third failure mode listed at hold_speech.py:216 — the one that made
bare markers unusable as fillers — arriving through the other door.

WHY THE FIX IS AT THE AUDIO AND NOT IN join_after_head
------------------------------------------------------
`join_after_head` has a `suppress_pure_duplicate` branch built for exactly this
shape, and BOTH call sites already pass it True. It is unreachable for a bare
marker: `_strip_interim_opener` does not recognise one, so the body is never
empty. Making it reachable is the obvious fix and it is the wrong one — it
empties the turn, and an empty turn here is not silence, it is worse:

  * `_record_spoken` is skipped -> `_display_reply` is "" -> history stores ""
    -> the booking-ack injector's `_last_bot` no longer contains "right —" ->
    the question the caller is owed is never asked.
  * `_any_tts_emitted` stays False -> Gate 5's empty-turn fallback arms. On
    freeform clinics (all four live ones) it is deferred to the v3 post-turn
    path and fires only if nothing else spoke — which, with the injector dead,
    is what happens. The caller says "I'd like to book an appointment" and
    hears "Sorry, I didn't quite catch that — could you say that again?"

That is the dead end `strip_head_echo` documents and refuses to walk into. It
is right at its layer. The TTS loop is the complement: the head has already
been spoken, the record is already written, and the injected question is still
coming — so the two words can be dropped from the AUDIO and nothing else moves.

Hence: no `_unrecord_spoken` in the new branch. That is load-bearing, and
`test_the_record_is_deliberately_left_standing` is what stops it being
"tidied up" later.
"""

import inspect

from app.media_streams import connection as c
from app.hold_speech import (
    is_bare_discourse_marker,
    strip_marker_before_question,
)


# The live calls, verbatim.
ACK = "Right —"
INJECTED_Q = "What's the appointment for?"


# -- the predicate ---------------------------------------------------------

def test_the_mandated_booking_stub_is_recognised():
    """The exact chunk from CA6a59e59f0a67fe, and its documented siblings.

    hold_speech.py:216 names the family that failed live: "Right —", "So —",
    "Okay —"."""
    for marker in (ACK, "So —", "Okay —", "Now.", "Alright —",
                   "Well —", "right —", "  Right —  "):
        assert is_bare_discourse_marker(marker), marker


def test_a_marker_with_anything_behind_it_is_ordinary_speech():
    """The whole-chunk requirement. A marker that opens a real sentence IS the
    sentence, and dropping it would delete content.

    "Right — Tuesday at ten is free" is the case that makes a shape-based rule
    ("short, starts with a marker") unusable, and it is why ACK_OPENER_RE
    argues for an allow-list in the first place."""
    for speech in (
        "Right — Tuesday at ten is free",
        "Right, that's booked in for you",
        "Okay — I've got you down for Thursday",
        "So, on our prices —",
        "Got it — ankle pain can be really frustrating",
        "Of course — go ahead, take your time.",
        INJECTED_Q,
        "",
    ):
        assert not is_bare_discourse_marker(speech), speech


def test_the_wider_ack_family_is_deliberately_not_swept_in():
    """ACK_OPENER_RE serves `strip_head_echo` at a different layer and covers a
    wider family. Reusing it here would drop 67 chunks across the stored corpus
    where the evidenced defect is 32 — a blast-radius widening on the barge-in
    path, bought for nothing."""
    for wider in ("Of course —", "Got it —", "No problem at all —",
                  "Sorry about that —", "Let me see —"):
        assert not is_bare_discourse_marker(wider), wider


# -- the guard, pinned -----------------------------------------------------

def _tts_loop_source() -> str:
    return inspect.getsource(c.WebSocketCallHandler._tts_loop)


def _code_only(text: str) -> str:
    """Source with comment lines removed.

    These assertions are about what the code DOES. The comments in this region
    necessarily name `_unrecord_spoken` and `_obs_chunk_text` to explain why
    neither is touched, and a substring search over raw source cannot tell an
    explanation from a call."""
    return " ".join(
        ln for ln in text.splitlines() if not ln.strip().startswith("#")
    )


def test_the_guard_drops_the_marker_at_the_audio():
    src = _tts_loop_source()
    assert "is_bare_discourse_marker" in src, (
        "the bare-marker drop is gone from _tts_loop — P3 is live again and "
        "callers hear three fragments before the booking question"
    )


def test_the_guard_requires_a_head_to_have_been_spoken():
    """Without this the stub is dropped on the turns where the model beat the
    head delay — and there the marker is the ONLY acknowledgement the caller
    gets before the injected question."""
    src = _tts_loop_source()
    marker_at = src.index("is_bare_discourse_marker")
    gate_at = src.index('"_hold_head_spoken"')
    assert gate_at < marker_at, (
        "the bare-marker drop no longer requires a hold phrase to have already "
        "played this turn, so it now eats the acknowledgement on turns that "
        "have no other one"
    )


def test_the_record_is_deliberately_left_standing():
    """THE load-bearing assertion.

    `_unrecord_spoken` in this branch would correct `last_bot_prompt` and the
    spoken record to say the marker was never heard — which is true of the
    audio and fatal to the turn: the booking-ack injector gates on `_last_bot`
    containing "right —", and Gate 5's empty-turn fallback arms behind it. The
    consecutive-duplicate dedup guard immediately below drops its chunk and
    leaves the record standing for the same reason."""
    src = _tts_loop_source()
    start = src.index("TTS bare-marker drop")
    end = src.index("TTS dedup: skipping duplicate chunk")
    branch = _code_only(
        src[src.rindex("if not _watchdog_reask", 0, start):end]
    )
    assert "_unrecord_spoken" not in branch, (
        "the bare-marker drop now un-records the chunk. That empties "
        "conversation_history for the turn, so the booking-ack injector never "
        "fires and Gate 5's deferred fallback answers 'I'd like to book an "
        "appointment' with 'Sorry, I didn't quite catch that'"
    )


def test_a_watchdog_reask_is_never_eaten():
    """A deliberate replay bypasses this guard exactly as it bypasses dedup."""
    src = _tts_loop_source()
    start = src.index("P3: a bare marker on top of a hold phrase")
    branch = src[start:src.index("TTS dedup: skipping duplicate chunk")]
    assert "not _watchdog_reask" in branch, (
        "the bare-marker drop no longer exempts a watchdog re-ask"
    )


# -- the mechanism, stated rather than assumed -----------------------------

def test_the_injector_still_recognises_the_stub_it_keys_on():
    """States the coupling the fix relies on: the phrase the booking-ack
    injector matches is the phrase being dropped from the audio. If that list
    is ever re-pointed, the reasoning above needs re-checking — the record is
    being kept FOR this consumer."""
    src = inspect.getsource(c)
    assert '"right —",' in src, (
        "_V3_ACK_PHRASES no longer contains the bare booking stub — the reason "
        "the record is deliberately left standing may no longer hold"
    )


# -- the owner's follow-up, 2026-09-01 -------------------------------------
# "I asked for a booking appointment and it went 'Right'." The bare-marker
# drop above only fires when the marker stands ALONE after a head. When the
# model welds the question onto it instead — one chunk, one synthesis call,
# which is what CAf5785c49 actually did — there is no second fragment to
# drop and the marker has to come off the front of the sentence.


def test_the_marker_comes_off_the_front_of_the_booking_question():
    assert strip_marker_before_question(
        "Right — what's the appointment for?"
    ) == "What's the appointment for?"
    assert strip_marker_before_question(
        "Right — do you have a preference for when you'd like to come in?"
    ) == "Do you have a preference for when you'd like to come in?"


def test_a_marker_doing_real_work_keeps_it():
    """The ten stored cases where the marker acknowledges what the caller SAID.
    These are statements, not questions, and cutting the marker turns an
    acknowledgement into a bare assertion."""
    for kept in (
        "Right — mornings it is.",
        "Right — Thursday afternoon. Let me check what's available.",
        "Right — so you'd like to come in next week.",
        "Right — let me look that up for you.",
    ):
        assert strip_marker_before_question(kept) == kept


def test_the_comma_form_is_never_touched():
    """"Right, Alcester." is Susie agreeing with the clinic the caller just
    named — the marker carries the agreement. Em/en dash only."""
    for kept in (
        "Right, Alcester. I've got you on oh seven five oh two — is that best?",
        "Right, that's the number confirmed.",
        "Right, whenever suits you best — do you have a preference?",
    ):
        assert strip_marker_before_question(kept) == kept


def test_stripping_never_empties_a_chunk():
    """A chunk that is nothing but the marker has no question behind it, so it
    never matches here — that case belongs to the drop above. Between them
    neither path can leave the turn with no audio."""
    for bare in ("Right —", "So —", "Okay —", ""):
        assert strip_marker_before_question(bare) == bare


def test_the_strip_is_wired_in_and_leaves_the_obs_text_alone():
    """`_obs_chunk_text` is what `_unrecord_spoken` matches against the record
    written in llm_stream, and what `_slot_readout_chunks` compares by
    equality. Rewrite it and both break silently by making the strings differ,
    so only `chunk_text` — the synthesis form — is touched."""
    src = _tts_loop_source()
    assert "strip_marker_before_question" in src, (
        "the leading-marker strip is gone from _tts_loop — callers hear "
        '"Right —" in front of the booking question again'
    )
    start = src.index("TTS leading-marker strip")
    branch = _code_only(
        src[src.rindex("Owner, 2026-09-01", 0, start):
            src.index("Skip consecutive identical chunks")]
    )
    assert "_obs_chunk_text" not in branch, (
        "the leading-marker strip now rewrites _obs_chunk_text — "
        "_unrecord_spoken and the slot-readout comparison both match on that "
        "string and will stop matching"
    )
