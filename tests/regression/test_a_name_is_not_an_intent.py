"""A surname fragment was read as a request to cancel the booking.

`CAffe1e08713631e5178c6fe73f44e4037`, northgate, 2026-09-08 07:52, build
`4d68f3d8`. outcome=abandoned, score 2.

    07:52:47  "So that's Tuesday the 8th of September at twenty to ten
               - could I take your first name and surname?"
    07:52:58  caller: 'yeah we cancelled and started roch'     <- the name, garbled
    07:53:08  [ms_conn] same-breath straggler KEPT (name collection, short
                        fragment - likely surname): 'canceling it'
    07:53:09  [ms_llm] situational head (cancel_req): 'Yes, no problem -'
    07:53:39  [ms_llm] tool: lookup_patient args={"purpose": "cancel", ...}
    07:53:56  caller hung up.  The booking under way was never made.

The lookup found a REAL earlier appointment on that number, so Susie then
offered to cancel a genuine booking the caller had not mentioned. (The judge
tagged the call `hallucination`; it is not one, and that label would send the
next reader after the wrong thing.)

-- TWO RULES THAT DISAGREE ABOUT WHAT THE FRAGMENT IS ------------------------
Each is correct alone:

  * the same-breath straggler rule KEEPS a short fragment during name capture,
    on purpose, so that a surname arriving late is not dropped;
  * `_INTENT_RULES` reads `\\bcancel\\w*\\b` corroborated by `it` as a request
    to cancel.

So one says "surname candidate" and the other says "intent", and nothing
reconciles them. The straggler rule is load-bearing -- it exists because
surnames were being lost -- so the intent side is the one that yields.

Measured: `'yeah we cancelled and started roch'` classifies as `[]` even
without this fix, because it has no corroborator. **The whole hijack came from
the `'canceling it'` straggler**, which is why the fix is aimed there and not at
the longer utterance.

-- SELF-INFLICTED, AND DATABLE ----------------------------------------------
`790f4604` (29 Aug 2026) -- "choose the head from what the caller asked, not
just the tool". Before it the head came from the tool being invoked, so a
cancellation could not be announced before something had decided to cancel one.
After it, raw caller text can. Zero prior instances across 921 stored calls;
every name-ask turn in the corpus was checked and this is the only one ever
answered with a cancel-word.

-- DELIBERATELY NARROW -------------------------------------------------------
It suppresses the HEAD, not the model. A caller who genuinely wants to cancel
mid-name is still heard; they just do not get the acknowledgement before
anything has decided. And only the topic-switch family is gated -- SYMPTOM,
SLOT_PICKED and the FAQ intents stay live, because none of them changes what
Susie is doing.
"""
from __future__ import annotations

import pytest

from app.hold_speech import Intent, classify_intent
from app.media_streams.latency_timing import capture_phase

NAME_ASK = (
    "So that's Tuesday the 8th of September at twenty to ten "
    "- could I take your first name and surname?"
)


#: The only head a name-capture fragment may earn: it acknowledges receipt
#: and switches no topic.
NAME_GIVEN = Intent.NAME_GIVEN.value


def _intents(text, prev=NAME_ASK, **kw):
    return [i.value for i in classify_intent(text, prev, **kw)]


# ---------------------------------------------------------------------------
# The live defect
# ---------------------------------------------------------------------------

def test_the_straggler_no_longer_announces_a_cancellation():
    """THE call. 'canceling it' was kept as a surname candidate and then read
    as a request."""
    assert "cancel_req" in _intents("canceling it")
    # 12 Sep 2026: a fragment answering the name question is an ANSWER, and
    # the answer-moment arm gives it "Thank you -" (NAME_GIVEN). That head
    # announces nothing -- the point of this test is that no topic switch is
    # spoken, and it still is not.
    assert _intents("canceling it", name_pending=True) == [NAME_GIVEN]


def test_the_longer_garble_was_never_the_trigger():
    """Pins the diagnosis, not just the fix. This utterance is what the caller
    is transcribed as saying, and it classifies as nothing either way -- it has
    no corroborator. Aiming a fix at it would have changed nothing."""
    # Since 12 Sep 2026 it earns the receipt head for a name answer, which
    # announces nothing; the point stands -- no cancel intent anywhere in it.
    assert _intents("yeah we cancelled and started roch") == [NAME_GIVEN]


@pytest.mark.parametrize("frag", [
    "canceling it", "cancel it", "cancelled it", "cancel that", "cancel my",
])
def test_the_whole_cancel_family_is_gated_during_name_capture(frag):
    assert _intents(frag, name_pending=True) == [NAME_GIVEN], frag


@pytest.mark.parametrize("frag,intent", [
    ("move it", "reschedule_req"),
    ("change that", "reschedule_req"),
    ("speak to someone", "transfer_req"),
])
def test_the_other_topic_switches_are_gated_too(frag, intent):
    """Same shape, same exposure: each acknowledges doing something other than
    what Susie just asked for. A surname that happens to sound like "move it"
    must not reschedule anything."""
    assert intent in _intents(frag), frag
    assert _intents(frag, name_pending=True) == [NAME_GIVEN], frag


# ---------------------------------------------------------------------------
# The guards. Suppressing too much is how this becomes a worse defect.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "can you cancel my appointment",
    "i need to cancel it",
    "actually cancel that booking",
    "can you move my appointment",
    "put me through to someone",
])
def test_a_genuine_request_outside_name_capture_is_untouched(said):
    """The gate is on the PHASE, not the words. Everywhere else in the call
    these must behave exactly as before."""
    assert _intents(said, "How can I help you today?") != [], said


def test_the_non_switching_intents_survive_name_capture():
    """SYMPTOM and the FAQ family do not change what Susie is doing, so they
    stay live. Suppressing everything would drop the turn into UNKNOWN_SLOW,
    which apologises for a wait -- the exact regression `Intent.SLOT_PICKED`'s
    own comment records."""
    hits = _intents("my shoulder really hurts",
                    "what's the appointment for?", name_pending=True)
    assert "symptom" in hits, hits


def test_the_flag_defaults_off():
    """Every other caller of `classify_intent` must be unaffected."""
    assert "cancel_req" in _intents("cancel it")


# ---------------------------------------------------------------------------
# The phase, which is what arms it
# ---------------------------------------------------------------------------

def test_the_name_question_reads_as_name_capture():
    """If this stops saying "name", the gate silently never fires."""
    assert capture_phase({"last_bot_prompt": NAME_ASK,
                          "v3_awaiting_surname": True}) == "name"


@pytest.mark.parametrize("prompt", [
    "Here's what we've got coming up - Number 1, Tuesday 8th September.",
    "I've got you on oh seven five oh two - is that the best number?",
    "How can I help you today?",
])
def test_another_question_on_the_table_ends_name_capture(prompt):
    """B-15. `v3_awaiting_surname` is sticky by design and never cleared when
    the conversation moves on, so a phase read off the FLAG alone would gate
    these heads for the rest of the call. The live prompt outranks it."""
    assert capture_phase({"last_bot_prompt": prompt,
                          "v3_awaiting_surname": True}) != "name", prompt


# ---------------------------------------------------------------------------
# The call site
# ---------------------------------------------------------------------------

def test_the_call_site_passes_the_phase():
    """The behavioural tests above pass the flag themselves, so they would stay
    green if nothing ever set it -- the failure mode this session has already
    hit twice."""
    import inspect
    from app.media_streams import llm_stream
    src = inspect.getsource(llm_stream)
    i = src.index("_hs_hits = _classify_intent(")
    # To the closing paren of the call, not a fixed byte count -- the first
    # version cut at +500 and the argument sat past it behind its own comment.
    window = src[i - 900:src.index(chr(10) + " " * 16 + ")", i)]
    assert "name_pending=" in window, "classify_intent is called without the phase"
    assert "capture_phase" in window, (
        "the phase is not read from `capture_phase`, which is its one owner")


def test_an_unreadable_phase_cannot_cost_the_caller_a_head():
    """It runs inside the turn. A raise must fall back to "not capturing a
    name", which is the behaviour before this guard."""
    import inspect
    from app.media_streams import llm_stream
    src = inspect.getsource(llm_stream)
    i = src.index("_hs_capture_phase")
    assert "except Exception" in src[i:i + 600]


@pytest.mark.parametrize("junk", [None, "", "   ", 0])
def test_it_never_raises(junk):
    assert isinstance(classify_intent(junk or "", NAME_ASK,
                                      name_pending=True), list)
