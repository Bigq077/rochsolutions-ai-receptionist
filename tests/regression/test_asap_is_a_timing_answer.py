# tests/regression/test_asap_is_a_timing_answer.py
"""
JV Bolton live call (2026-07-24, CA3e342642f57273e29e618074b87ba181) — the
caller stated their timing preference in their opening utterance and Susie
asked for it anyway, costing a full turn (~33 s) before check_availability ran.

Live-call trace:
    18:03:39  caller  "i said i'd like to book an appointment as soon as
                       possible please"
    18:03:42  Susie   "Right — do you have a preference for when you'd like
                       to come in?"          <-- re-asks what was just answered
    18:03:55  Susie   (watchdog) repeats the same question
    18:04:13  caller  "now as soon as possible"
    18:04:15  tool    check_availability(date_hint="as soon as possible")

Root cause — two rules in clinic_template_prompt.py's booking flow contradicted
each other, and the model correctly followed the stronger-worded one:

  * The TIME PREFERENCE GATE's "no preference" bullet said: "if the caller has
    given NO day, date, or time of day AND has NOT explicitly said they're
    flexible, you MUST ask the timing question ... and WAIT before calling
    check_availability."
  * A later bullet said: "Urgency ('ASAP', 'earliest you have') -> date_hint
    'as soon as possible'."

"As soon as possible" is not a day, a date, or a time of day, and it is not in
the enumerated flexible list ('flexible', "doesn't matter", 'anytime', "I'm not
sure", "I don't know").  So the MUST-ask clause matched and the urgency bullet,
sitting BELOW it in the same list, never got a chance.  Step 2 reinforced this
by presenting the timing question as an unconditional flow step, and Step 3's
exemption excused only "a date, day, or time of day" — urgency was named
nowhere in any exemption path.

Fix (prompt text only — no engine change):
  1. Urgency added to every enumeration of the sufficient-signal set: Step 2's
     skip caveat, Step 3's exemption, the "no preference" MUST-ask clause, and
     the clinic's-own-hours WAIT clause.
  2. The urgency bullet hoisted to FIRST in the gate — ahead of both MUST-ask
     clauses — and restated as "a STATED preference, NOT an absence of one".
  3. Step 2 (both modality variants) now carries an explicit SKIP caveat, so
     the timing question is never presented as unconditional.

The assertions below are deliberately semantic rather than literal: they locate
each MUST-ask / WAIT clause and require urgency to be named in its
sufficient-signal enumeration.  Rewording is fine; dropping urgency is not.

Both deployed template_v1 clinics are covered, which also exercises both
branches of the Step 2 text: jv_v1 has modalities ['in_clinic', 'remote'] and
renders "2. MODALITY THEN TIMING"; vital_edge is in-clinic only and renders
"2. TIMING".
"""

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import build_clinic_prompt

# Every clinic whose prompt_engine is template_v1 — i.e. every clinic this
# prompt file actually serves.  jv_v1 is the clinic from the incident.
TEMPLATE_CLINICS = ["jv_v1", "vital_edge"]

# The exact phrase from the abandoned turn, plus the family a caller answering
# "when would you like to come in?" reaches for.  Each must be recognisable as
# a timing answer in the prompt's own vocabulary.
URGENCY_PHRASES = [
    "as soon as possible",   # the reproduced failure
    "ASAP",
    "soonest",
    "earliest",
]

# Anchors for the clauses that previously enumerated the sufficient-signal set
# WITHOUT urgency.  Each maps to (needle, how many chars of preceding context
# hold that clause's enumeration).
SUFFICIENT_SIGNAL_CLAUSES = [
    # TIME PREFERENCE GATE, "no preference" bullet — the clause that actually
    # fired on the live call.
    ("you MUST ask the timing", 280),
    # TIME PREFERENCE GATE, clinic's-own-hours bullet.
    ("If they have not stated one, ask the timing question", 220),
    # Step 3 — the "already stated it earlier" exemption.
    ("do NOT ask again; use", 260),
]


@pytest.fixture(scope="module", params=TEMPLATE_CLINICS)
def static_prompt(request):
    """The cacheable spine of a template clinic's system prompt."""
    static, _dynamic = build_clinic_prompt({}, get_clinic(request.param))
    return static


def test_urgency_is_named_as_a_complete_timing_answer(static_prompt):
    """The gate must state outright that urgency IS an answer, not a gap."""
    assert "URGENCY IS A COMPLETE TIMING ANSWER" in static_prompt, (
        "the TIME PREFERENCE GATE no longer declares urgency a complete "
        "timing answer — a caller saying 'as soon as possible' will be asked "
        "for their timing preference again (JV Bolton 2026-07-24)."
    )


def test_urgency_precedes_the_must_ask_clause(static_prompt):
    """Ordering is what broke it: the urgency rule must come FIRST.

    In the failing version the urgency bullet sat below the "you MUST ask the
    timing question ... and WAIT" clause in the same bullet list, so the
    mandatory-ask rule won the conflict.
    """
    urgency_at = static_prompt.index("URGENCY IS A COMPLETE TIMING ANSWER")
    must_ask_at = static_prompt.index("you MUST ask the timing")
    assert urgency_at < must_ask_at, (
        "the urgency rule now sits BELOW the mandatory-ask clause; that is "
        "the exact ordering that produced the redundant timing question."
    )


@pytest.mark.parametrize("needle,context_chars", SUFFICIENT_SIGNAL_CLAUSES)
def test_every_sufficient_signal_enumeration_includes_urgency(
    static_prompt, needle, context_chars
):
    """Wherever the prompt lists what counts as a timing signal, urgency is in it.

    Each of these clauses previously enumerated only "day, date, or time of
    day", which silently excluded "as soon as possible".
    """
    assert needle in static_prompt, f"anchor clause missing from prompt: {needle!r}"
    idx = static_prompt.index(needle)
    window = static_prompt[max(0, idx - context_chars):idx].lower()
    assert "urgency" in window, (
        f"the clause ending at {needle!r} enumerates the sufficient timing "
        "signals without naming urgency — 'as soon as possible' will fall "
        "through to the mandatory-ask path again.\n"
        f"clause context:\n...{static_prompt[max(0, idx - context_chars):idx]}"
    )


@pytest.mark.parametrize("phrase", URGENCY_PHRASES)
def test_urgency_vocabulary_is_present(static_prompt, phrase):
    """The words a caller actually uses must appear in the gate's vocabulary."""
    assert phrase.lower() in static_prompt.lower(), (
        f"urgency phrase {phrase!r} is not recognised anywhere in the prompt"
    )


def test_asap_maps_to_the_asap_date_hint(static_prompt):
    """Urgency must resolve to date_hint 'as soon as possible', not a day filter."""
    idx = static_prompt.index("URGENCY IS A COMPLETE TIMING ANSWER")
    bullet = static_prompt[idx:idx + 700]
    assert "date_hint 'as soon as possible'" in bullet, (
        "the urgency rule no longer specifies the date_hint value; the model "
        "may invent a day filter instead of asking for the soonest slot."
    )
    assert "check_availability" in bullet, (
        "the urgency rule must send the model straight to check_availability."
    )


def test_step_two_timing_question_is_not_unconditional(static_prompt):
    """Step 2 must carry the skip caveat.

    On the live call the model spoke Step 2's question verbatim.  Step 2 is
    where the model acts, so the exemption has to be visible there and not
    only in Step 3.
    """
    assert "SKIP" in static_prompt, "Step 2 lost its skip caveat entirely"
    idx = static_prompt.index("TIMING question")
    step_two = static_prompt[idx:idx + 700]
    assert "SKIP" in step_two, (
        "Step 2 presents the timing question unconditionally again — the "
        "model will ask it even when the caller already stated urgency."
    )
    assert "urgency" in step_two.lower(), (
        "Step 2's skip caveat does not name urgency, so 'as soon as possible' "
        "will not trigger the skip."
    )


def test_clinical_urgency_boundary_is_preserved(static_prompt):
    """Scheduling urgency must not swallow the red-flag safety net.

    'As soon as possible' is a scheduling preference; red-flag symptoms are a
    clinical escalation that must still outrank booking.  Keep these distinct
    — and in particular do NOT broaden the trigger list to the bare word
    'urgent', which is how callers describe symptoms.
    """
    idx = static_prompt.index("URGENCY IS A COMPLETE TIMING ANSWER")
    bullet = static_prompt[idx:idx + 700]
    assert "red-flag" in bullet.lower(), (
        "the urgency rule no longer carves out clinical urgency; a caller "
        "describing red-flag symptoms could be routed into booking instead "
        "of the urgent-care safety net."
    )
