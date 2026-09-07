"""The booking read-back must name the caller from the RECORD, not from memory.

Measured over the obs corpus on 7 Sep 2026 — 273 calls carrying a real booking
read-back ("So that's <Name>, <Weekday> the …"):

    spoken == stored               199
    model said the FIRST NAME only  67    25%
    DISAGREE                         6     2.2%   (three of them booked)

The read-back is composed by the model from conversation history. The stored
name reaches it only as a passive `name=` fact in CALL STATE, and nothing
reconciles the two — so a caller confirms a sentence that is not derived from
what gets written. `CA8d5b2e3e` is the shape: CALL STATE carried
`name=Quentin R-O-C-H` and Susie read back "Quentin Roch".

An injection that uses the record already existed (`_rb_name` in llm_stream),
but it is delivered as a blocked-tool RESULT and gated on
`tool_name == "check_availability"`. On the ordinary path the model goes
straight from the phone confirmation to the read-back, no tool call is
attempted, and it never runs.

So this is a CALL STATE steer, in the read-back state only, with one owner in
`name_capture` because both prompt engines need it — `clinic_template_prompt`
for jv_v1 / vital_edge / northgate and `susie_system_prompt` for theorem_v3.

Scored against all 273 stored read-backs:

    steer fires                     268
       readback unchanged           199
       GAINS the surname             66
       CHANGES the name spoken        3   <- all three were booked
    stands down (implausible name)    5

The three it changes are the three wrong-name bookings in the corpus. The five
it stands down on are the reason `name_is_plausible` exists — two are parse
failures ('Actually', 'Been'), one is a spelling that reached the store
('Quentin R-O-C-H'), and one is a store holding LESS than the model already had
('Quentin' where the model said 'Quentin Rock'), where firing would have made
the read-back worse.
"""

import inspect

import pytest

from app.name_capture import name_is_plausible, readback_name_steer
from app.prompts.susie_system_prompt import build_system_prompt_parts

CLINICS = ("jv_v1", "vital_edge", "northgate", "theorem_v3")
_SLOT = "Wednesday the 9th of September at eight in the morning"


def _readback_session(clinic_id="jv_v1", name="Quentin Roch", **kw):
    """A session at the moment the model composes the booking read-back."""
    s = {
        "clinic_id": clinic_id,
        "collected": {"name": name, "phone": "07502211207"},
        "phone_confirmed": True,
        "v3_confirmed_slot_phrase": _SLOT,
    }
    s.update(kw)
    return s


def _rendered(session):
    parts = build_system_prompt_parts(session)
    return "\n".join(p for p in parts if isinstance(p, str))


# ---------------------------------------------------------------------------
# name_is_plausible — this decides what is SPOKEN, so it denies by default
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Quentin Roch",
    "Tom Green",
    "Jean-Baptiste Smith",     # a real double-barrelled name
    "van der Berg Jones",      # particles
    "Sarah O'Neill",
])
def test_a_real_full_name_is_plausible(name):
    assert name_is_plausible(name) is True, name


@pytest.mark.parametrize("name,why", [
    ("Actually", "a stray word captured as a name — seen twice on jv_v1"),
    ("Been", "same shape, same day"),
    ("Would", "the three stored names that fail the project's own stoplists"),
    ("Quentin R-O-C-H", "a SPELLING that reached the store"),
    ("Quentin", "a first name only — see the dedicated test below"),
    ("", "nothing"),
    (None, "not a string"),
    ("   ", "whitespace"),
    ("Yes Please", "both tokens are stoplisted"),
])
def test_an_implausible_name_is_refused(name, why):
    assert name_is_plausible(name) is False, why


def test_a_first_name_alone_is_refused_deliberately():
    """Not a bug — the reason the steer requires a surname.

    Asserting "the booking will be written as 'Quentin'" would contradict the
    standing rule that `patient_name` is always the FULL name, and could talk
    the model into booking without one. The corpus carries the case that proves
    it: a vital_edge call where the store held 'Quentin' and the model had
    already said 'Quentin Rock'. Firing there would have made the read-back
    WORSE.
    """
    assert name_is_plausible("Quentin") is False
    assert readback_name_steer(_readback_session(name="Quentin")) == ""


# ---------------------------------------------------------------------------
# The steer fires in ONE state
# ---------------------------------------------------------------------------

def test_it_fires_in_the_readback_state():
    steer = readback_name_steer(_readback_session())
    assert "NAME ON RECORD" in steer
    assert "Quentin Roch" in steer


@pytest.mark.parametrize("mutation,why", [
    ({"phone_confirmed": False}, "the phone step is still outstanding"),
    ({"v3_confirmed_slot_phrase": "", "booking_flow_active": False},
     "no slot has been agreed"),
    ({"collected": {}}, "no name on record"),
    ({"collected": {"name": "Actually"}}, "the stored name is a parse failure"),
])
def test_it_stands_down_outside_the_readback_state(mutation, why):
    assert readback_name_steer(_readback_session(**mutation)) == "", why


def test_booking_flow_active_is_accepted_as_the_slot_signal():
    """The same two signals the PHONE STEP steer reads, so the pair cannot
    disagree about when the read-back moment is."""
    s = _readback_session(v3_confirmed_slot_phrase="", booking_flow_active=True)
    assert "NAME ON RECORD" in readback_name_steer(s)


@pytest.mark.parametrize("junk", [None, "", 0, [], "a string", {"collected": None},
                                  {"collected": {"name": 12345}}])
def test_it_never_raises(junk):
    """A caller mid-booking must not lose their turn to a steer."""
    assert readback_name_steer(junk) == ""


# ---------------------------------------------------------------------------
# It must actually REACH the model — the recurring trap in this repo is a rule
# that is written and never rendered
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("clinic_id", CLINICS)
def test_every_clinic_renders_it_in_the_readback_state(clinic_id):
    text = _rendered(_readback_session(clinic_id))
    assert "NAME ON RECORD" in text, (
        "%s does not render the steer. theorem_v3 builds its prompt from "
        "_build_theorem_v3 and the template clinics from _b7_call_state, so "
        "both call sites are needed and neither covers the other." % clinic_id
    )
    assert "Quentin Roch" in text


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_no_clinic_renders_it_outside_the_readback_state(clinic_id):
    text = _rendered(_readback_session(clinic_id, phone_confirmed=False))
    assert "NAME ON RECORD" not in text, (
        "%s renders the steer while the phone step is outstanding — it would "
        "compete with the step the model is actually on" % clinic_id
    )


@pytest.mark.parametrize("clinic_id", CLINICS)
def test_an_implausible_stored_name_is_never_spoken(clinic_id):
    text = _rendered(_readback_session(clinic_id, name="Would"))
    assert "NAME ON RECORD" not in text
    assert "written as \"Would\"" not in text


# ---------------------------------------------------------------------------
# One owner
# ---------------------------------------------------------------------------

def test_both_prompt_engines_use_the_same_definition():
    """Two copies of this rule would be two answers to "which name does the
    read-back use", and they would drift the first time one was edited."""
    from app.prompts import clinic_template_prompt, susie_system_prompt

    for mod in (clinic_template_prompt, susie_system_prompt):
        src = inspect.getsource(mod)
        assert "readback_name_steer" in src, mod.__name__
        assert "NAME ON RECORD — the booking will be written as" not in src, (
            "%s spells the steer out itself instead of importing it" % mod.__name__
        )


# ---------------------------------------------------------------------------
# The corpus cases, as a table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stored,was_spoken", [
    # the three wrong-name bookings the steer corrects
    ("Tom Green", "Come"),
    ("Quinton Rock", "Quentin"),
    ("Quentin Roch", "Quentin Rook"),
])
def test_the_corpus_corrections_would_now_speak_the_record(stored, was_spoken):
    steer = readback_name_steer(_readback_session(name=stored))
    assert stored in steer, stored
    assert steer, "the steer must fire on all three — each one was booked"


@pytest.mark.parametrize("stored", [
    "Actually",          # jv_v1, parse failure
    "Been",              # jv_v1, parse failure
    "Quentin R-O-C-H",   # northgate, a spelling in the store
    "Quentin",           # vital_edge, store holds LESS than the model had
])
def test_the_corpus_stand_downs_keep_todays_behaviour(stored):
    assert readback_name_steer(_readback_session(name=stored)) == "", stored


# ---------------------------------------------------------------------------
# A deliberate non-change, recorded so it is not "fixed" later by accident
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "the point of", "correct you", "reading it back", "which is the",
])
def test_the_steer_carries_no_narratable_rationale(phrase):
    """The steer is an INSTRUCTION and nothing else.

    An earlier draft closed with "...if it is wrong the caller will correct
    you, which is the point of reading it back" — true, and the most
    narratable line in the block. Sonnet paraphrases CALL STATE into speech
    (`internal_call_state_leak` exists for exactly that), and Susie explaining
    her own confirmation ritual to a patient is the one leak this steer could
    plausibly produce. The reasoning lives in the docstring, which the model
    never sees.
    """
    steer = readback_name_steer(_readback_session())
    assert steer, "the steer must fire, or this asserts nothing"
    assert phrase not in steer.lower(), (
        "%r reads as narration rather than instruction" % phrase)


def test_the_label_is_not_added_to_the_call_state_leak_stripper():
    """`internal_call_state_leak` strips sentences containing "booking flow",
    "call state" or "cta count" — machine vocabulary a caller must never hear.

    "name on record" is NOT added to it, for two reasons. The existing steers
    (SLOT ALREADY AGREED, PHONE STEP OUTSTANDING) are not in that list either,
    so this is consistent rather than an oversight; and unlike "CTA COUNT" the
    phrase is harmless if spoken, while a strip rule would delete a legitimate
    confirmation sentence ("the name on record is Quentin — is that right?")
    along with the leak.
    """
    from app.media_streams import turn_handler

    src = inspect.getsource(turn_handler)
    i = src.find("internal_call_state_leak")
    assert i != -1, "the leak stripper moved — re-aim this test"
    assert "name on record" not in src[i:i + 400].lower()
