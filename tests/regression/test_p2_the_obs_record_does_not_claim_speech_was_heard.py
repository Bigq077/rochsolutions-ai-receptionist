"""
P2: the obs transcript records speech the caller never heard.

OPEN_DEFECTS_HOLD_AND_SLOTS_2026-09-01. Severity LOW as a defect, HIGH as a
trap — it caused two wrong diagnoses in one session, one of which reported a
live, working disclosure as missing.

connection.py's comment at the record site claimed:

    "it is past every suppression check above, so nothing the caller did not
     hear is stored"

and app/obs/turns.py's docstring said the same in its own words. Both were
false. The seam is past every SUPPRESSION check, but it sits before
`split_tts_text`, before synthesis and before playback, so anything killed
downstream is still recorded exactly as though it had been spoken in full.

Proof in the corpus: CAa2bdff2b8, the P1 call. Four assistant entries stored for
the Friday turn; the caller heard about 1.9 seconds of the first one.

WHAT THIS FIXES AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------
Three things can take the audio away after the record is written:

  1. the TTS loop rewriting the chunk before synthesis — the P3 leading-marker
     strip. CLOSED: the record now stores the rewritten form.
  2. a barge-in cancelling synthesis part-way through the sub-chunks. CLOSED by
     `note_cut`, which annotates the fragment already written.
  3. a barge-in tearing down PLAYBACK after synthesis completed, the audio
     already in Twilio's buffer. NOT CLOSED, and deliberately: answering it
     means threading chunk identity through `_send_loop`'s cumulative playout
     clock, which is the audio path, for a LOW-severity defect.

So the absence of `cut` still does not prove a fragment was heard, and the
docstring has to keep saying so. `test_absence_of_the_marker_is_not_a_claim`
is what stops that caveat being quietly dropped once the marker exists — an
over-claim here is exactly the original defect, rebuilt.
"""

import inspect

from app.media_streams import connection as c
from app.obs import judge, turns


# -- the false claim ------------------------------------------------------

def test_the_record_site_no_longer_claims_the_caller_heard_it():
    src = inspect.getsource(c.WebSocketCallHandler._tts_loop)
    assert "nothing the caller did not hear is stored" not in src, (
        "the false claim is back at the obs record site. It is not past "
        "synthesis or playback, and stating otherwise is what made two "
        "sessions read this list as audio"
    )


def test_the_module_docstring_no_longer_claims_it_either():
    doc = turns.__doc__ or ""
    assert "text the caller never heard is never recorded" not in doc
    assert "INTENT TO SPEAK" in doc, (
        "turns.py must say what the list actually is — the docstring is the "
        "only thing a corpus reader sees before trusting it"
    )


# -- what the marker records ----------------------------------------------

def _session_with_one_fragment():
    s = {}
    turns.record_assistant(s, "The available slots for Friday are — Number 1")
    return s


def test_a_cut_fragment_is_marked_with_how_much_was_spoken():
    s = _session_with_one_fragment()
    turns.note_cut(s, spoke=1, of=4)
    assert s["obs_turns"][-1]["cut"] == {"spoke": 1, "of": 4}


def test_the_text_is_not_truncated_to_what_was_spoken():
    """`split_tts_text` works on the POST-substitution string, and this list
    stores the pre-substitution form so it reads back as English. Splicing
    sub-chunks into it would corrupt every phone number in the corpus."""
    s = _session_with_one_fragment()
    before = s["obs_turns"][-1]["text"]
    turns.note_cut(s, spoke=1, of=4)
    assert s["obs_turns"][-1]["text"] == before


def test_marking_never_raises_on_an_empty_or_caller_led_record():
    """It runs on the barge-in path, which is the worst place to throw."""
    turns.note_cut({}, spoke=1, of=2)
    s = {}
    turns.record_user(s, "yeah go on then")
    turns.note_cut(s, spoke=1, of=2)
    assert "cut" not in s["obs_turns"][-1]


def test_absence_of_the_marker_is_not_a_claim():
    """THE caveat. `cut` closes synthesis-cancellation only — the P1 playback
    teardown leaves no mark here at all. If this ever reads as "no cut means
    heard", P2 is rebuilt with an extra step."""
    doc = (turns.note_cut.__doc__ or "") + (turns.__doc__ or "")
    assert "not" in doc.lower() and "heard" in doc.lower(), (
        "the note_cut/module docs no longer warn that absence of the marker "
        "is not evidence the fragment was heard"
    )


# -- the judge sees it ----------------------------------------------------

def test_the_judge_is_told_the_line_was_interrupted():
    """The judge inventing an ending is the documented cost of this list being
    wrong — it once texted the operator that a caller had hung up when they had
    not. A truncated line read as complete is the same failure."""
    rendered = judge._format_transcript([
        {"role": "user", "text": "friday please"},
        {"role": "assistant",
         "text": "The available slots for Friday are — Number 1",
         "cut": {"spoke": 1, "of": 4}},
    ])
    assert "interrupted" in rendered
    assert "1 of 4" in rendered


def test_an_uncut_line_renders_exactly_as_before():
    """No marker, no annotation — the judge prompt for a normal call is
    unchanged, so this cannot move scoring on calls that had no barge-in."""
    rendered = judge._format_transcript([
        {"role": "user", "text": "friday please"},
        {"role": "assistant", "text": "Friday at ten is free."},
    ])
    assert rendered == "USER: friday please\nASSISTANT: Friday at ten is free."


# -- the P3 strip no longer lies into the record --------------------------

def test_the_record_stores_the_text_that_was_actually_synthesised():
    """The leading-marker strip rewrites what ElevenLabs is given. Recording
    the un-stripped form would store a "Right —" no caller heard — this defect,
    a third time, added by the fix for P3."""
    src = inspect.getsource(c.WebSocketCallHandler._tts_loop)
    assert "record_assistant(self.session, _obs_display_text)" in src, (
        "the obs record no longer stores the stripped form, so it claims a "
        "leading marker the caller did not hear"
    )


def test_the_matching_string_is_still_the_unstripped_one():
    """`_obs_chunk_text` is what `_unrecord_spoken` matches against the record
    written in llm_stream, and what `_slot_readout_chunks` compares by
    equality. Rewrite it and both stop matching, silently."""
    src = inspect.getsource(c.WebSocketCallHandler._tts_loop)
    assert "_unrecord_spoken(self.session, _obs_chunk_text)" in src, (
        "_unrecord_spoken is no longer given the un-stripped string — B-76's "
        "correction will stop finding the chunk it needs to un-record"
    )
