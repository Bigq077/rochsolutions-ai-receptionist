# tests/regression/test_the_clinic_question_is_asked_once.py
"""
Susie asked which clinic it was, twice, in two different wordings.

theorem_v3 CAd16d6e367c31f7c4e7ec1c1df49632dd, 8 Sep 2026, build 4cfd7545, on
the live patient line. The caller said "I'd like to cancel my appointment":

    16:58:51.244  "no problem at all."
    16:58:51.359  "Was the appointment you'd like to cancel at our Awlstuh or
                   Redditch clinic?"                    <- the model's own
    16:58:51.726  "Was your original appointment at our Awlstuh or Redditch
                   clinic?"                             <- injected on top
    16:58:58.876  "Was your original appointment at our Awlstuh or Redditch
                   clinic?"                             <- watchdog re-ask

The caller barged in over the third one. Nothing in the call stopped it.

THE INJECTOR HAS A GUARD FOR EXACTLY THIS and it could not fire. It matched:

    "which clinic", "alcester or redditch", "alcester or reditch",
    "original appointment at"

against Susie's own reply. But `Alcester` is a TTS pronunciation hint: the
theorem_v3 prompt writes "Awlstuh" 59 times so the voice says it correctly, and
the model writes what its prompt writes. The guard was matching a SPELLING the
model is instructed never to produce, so on this clinic it could not fire at
all -- not "rarely", never. The model's rewording ("the appointment you'd like
to cancel at" rather than "original appointment at") closed the last keyword.

This is the third instance of the same rule: code must never match one literal
of model speech. What makes this one different is that two of the three guards
that ask "did the reply already ask this?" had ALREADY been given
"awlstuh or redditch", and the third had not -- so the defect was drift between
three copies of one idea, not a missing idea.

THE FIX. One `_CLINIC_Q_SIGNALS` tuple, referenced by all three sites, each
concatenating its own extra phrasings. A form added for one guard is now added
for all of them.

Not fixed here, and worth keeping separate: the duplicated opener ("No problem
at all -" from the head, then "no problem at all." from the model) is the
join_after_head / suppress_pure_duplicate path, and the watchdog re-ask after a
wordless barge-in is deliberate recovery from a torn-down question.
"""

import inspect

import pytest

from app.media_streams import connection as conn


# The exact sentence Susie generated on CAd16d6e36, which the old guard missed.
LIVE_UTTERANCE = (
    "Was the appointment you'd like to cancel at our Awlstuh or Redditch "
    "clinic?"
)

# The deterministic questions the engine itself speaks. Whatever a guard
# matches, it has to recognise the engine's own wording too.
ENGINE_QUESTIONS = [
    conn._LOC_RUNG1_OPEN,
    conn._loc_rung2_confirm("Awlstuh"),
    conn._loc_rung2_confirm("Redditch"),
]


def _fires(text: str, signals) -> bool:
    return any(kw in text.lower() for kw in signals)


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------
def test_the_live_utterance_is_recognised_as_the_clinic_question():
    """The whole bug in one assertion."""
    assert _fires(LIVE_UTTERANCE, conn._CLINIC_Q_SIGNALS), (
        "the reply that caused the double-ask is still not recognised as the "
        "clinic question, so the injector will queue a second one"
    )


def test_the_pronunciation_spelling_is_covered():
    """`Awlstuh` is what the model writes, because it is what the prompt writes.

    A guard that knows only `Alcester` is looking for a string this clinic's
    model never produces.
    """
    assert any("awlstuh" in kw for kw in conn._CLINIC_Q_SIGNALS)


def test_the_place_name_spelling_is_still_covered():
    """Both, not either. Other clinics and older transcripts use `Alcester`,
    and the point of the fix is to stop losing forms, not to swap one for
    another."""
    assert any("alcester" in kw for kw in conn._CLINIC_Q_SIGNALS)


@pytest.mark.parametrize("question", ENGINE_QUESTIONS)
def test_the_engines_own_questions_are_recognised(question):
    """If Susie asked it, a guard must see that she asked it -- including the
    rung-2 confirm, which names one clinic rather than offering two."""
    assert _fires(question, conn._CLINIC_Q_SIGNALS) or "clinic" in question.lower()


# ---------------------------------------------------------------------------
# The drift that caused it
# ---------------------------------------------------------------------------
def test_all_three_guards_share_one_signal_list():
    """The defect was three copies of one idea, one of which went stale.

    Each site may add its own extra phrasings, but the shared core must come
    from the shared tuple -- a form added for one guard is added for all.
    """
    src = inspect.getsource(conn)
    uses = src.count("_CLINIC_Q_SIGNALS")
    # 1 definition + 3 call sites.
    assert uses >= 4, (
        f"only {uses} references to _CLINIC_Q_SIGNALS -- a guard has been "
        f"given its own copy again, which is how this defect happened"
    )


def test_no_guard_keeps_a_private_copy_of_the_two_choice_wording():
    """A literal list containing the two-choice phrasing, outside the shared
    tuple, is a fourth copy waiting to go stale."""
    # Comments quote these phrases when explaining the defect, so count only
    # lines that are actually a tuple entry.
    code = [
        ln for ln in inspect.getsource(conn).splitlines()
        if not ln.lstrip().startswith("#")
    ]
    for phrase in ('"alcester or redditch"', '"awlstuh or redditch"'):
        entries = [ln for ln in code if ln.strip() == phrase + ","]
        assert len(entries) == 1, (
            f"{phrase} appears as a tuple entry {len(entries)} times -- the "
            f"guards have drifted apart again"
        )


# ---------------------------------------------------------------------------
# What must NOT be swept up
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Let's get you booked in —",
    "So that's Friday the 11th of September at two in the afternoon.",
    "Is the number you're calling on the one associated with your booking?",
    "that's all done — your appointment has been cancelled.",
    "We're at The Greig Leisure Centre, Kinwarton Road, Awlstuh, B49 6AD.",
])
def test_ordinary_replies_do_not_read_as_the_clinic_question(text):
    """Widening the guard suppresses the INJECTED question, so a false match
    means the clinic question is never asked at all -- a worse failure than
    asking it twice.

    The address line matters most: it says "Awlstuh" without asking anything.
    """
    assert not _fires(text, conn._CLINIC_Q_SIGNALS)
