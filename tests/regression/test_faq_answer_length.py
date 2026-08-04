"""
Susie must answer the question, not deliver a lecture (T-5).

The single most pervasive defect of the 2026-08-04 acceptance sweep — present in
all seven calls. Worst turns ran 20.2s and 20.1s of unbroken speech. On call 5
the caller barged in on every long answer; on call 6 they said "say that again
you got cut off"; call 3 ended `abandoned` two seconds after a ~20s monologue
finished.

The cap was NOT missing. The FAQ rule already said "two to three sentences is
right for most answers" and "don't volunteer information not asked about". The
first clause was broadly honoured. The second was ignored:

    Asked: opening hours + parking.
    Said:  hours, parking, the train-station walk, an offer to transfer to
           Mark, AND an offer to book at Awlstuh instead.   (call 2, 20.2s)

    Asked: the price of a standalone shockwave session.
    Said:  the price, plus an unprompted aside about Mark warning them before
           applying it.                                      (call 3, 14.8s)

Two causes, and only one is sentence count. Call 6's acupuncture answer was
three sentences and still ran 20.1s, on a 138-character middle clause — so
sentence LENGTH matters independently of how many there are.

Two things this fix must not break, and both are pinned below:

  1. Warmth. The prompt's "don't give clipped one-word answers" clause is
     deliberate and stays. The owner's brief was explicitly that Susie should
     still answer "like a human receptionist would" — the fix is to stop
     adding what nobody asked for, not to become curt.

  2. Slot lists. Call 7's 14.5s and 14.0s turns were Susie reading three days
     with two times each. That is meant to be complete; capping it would leave
     the caller unable to choose.

All assertions run against the RENDERED prompt — theorem_v3 has no
prompt_engine key, so much of susie_system_prompt.py is dead text for it.
"""

import pytest

from app.prompts.susie_system_prompt import _build_theorem_v3


def _render():
    out = _build_theorem_v3({
        "clinic_id": "theorem",
        "twilio_from": "+447502211207",
        "collected": {},
    })
    parts = out if isinstance(out, tuple) else (out,)
    return "\n".join(x for x in parts if isinstance(x, str))


@pytest.fixture(scope="module")
def prompt():
    return _render()


def test_prompt_actually_renders(prompt):
    assert len(prompt) > 50_000, f"theorem_v3 rendered only {len(prompt)} chars"


# ── the rule that was being broken ──────────────────────────────────────────

def test_answer_only_what_was_asked_is_stated_as_a_rule(prompt):
    """The old wording — 'don't volunteer information not asked about' — was
    present and ignored. It is now a named, capitalised rule carrying the two
    live examples that broke it, because a rule the model has already walked
    past needs evidence rather than repetition."""
    assert "ANSWER ONLY WHAT WAS ASKED" in prompt


def test_the_live_failures_are_quoted_as_examples(prompt):
    """Concrete beats abstract. Both examples are real turns from the sweep."""
    low = prompt.lower()
    assert "the train station is" in low, "the call-2 example is missing"
    assert "shockwave session" in low, "the call-3 example is missing"


def test_sentence_length_is_capped_independently(prompt):
    """Call 6 was three sentences and still ran 20.1s. Capping sentence COUNT
    alone would have passed that turn."""
    assert "KEEP THE SENTENCES SHORT TOO" in prompt
    assert "twenty words" in prompt


def test_only_one_offer_per_turn(prompt):
    """Call 2 ended with a transfer offer AND an alternative-clinic offer, which
    is why the caller had to choose between Susie's options instead of
    answering her question."""
    assert "ONE OFFER, NEVER TWO" in prompt


# ── what must NOT be broken ─────────────────────────────────────────────────

def test_warmth_clause_survives(prompt):
    """The owner's brief: still answer like a human receptionist. A curt system
    is a different defect, not a fix for this one."""
    assert "Don't give clipped one-word answers" in prompt, (
        "the anti-clipped clause was removed — Susie will start sounding like "
        "a call centre"
    )


def test_the_worked_example_shows_warmth_not_terseness(prompt):
    """The example in the rule is the thing the model actually copies, so it
    has to model the register we want, not just the length."""
    assert "Redditch is Thursdays only, nine till five" in prompt
    assert "not 'Thursdays'" in prompt


def test_slot_lists_are_explicitly_exempt(prompt):
    """The most dangerous possible side effect: a shortened slot list leaves the
    caller unable to pick, which breaks booking outright."""
    assert "NONE OF THIS APPLIES TO READING OUT APPOINTMENT SLOTS" in prompt


def test_mid_booking_reask_is_untouched(prompt):
    """Owner decision, 2026-08-04: keep it as is. It stops the caller thinking
    the line dropped, and it behaved correctly throughout the sweep."""
    assert "MANDATORY WHEN CALL STATE SHOWS BOOKING FLOW ACTIVE" in prompt
    assert "NEVER end the reply on a statement while a booking is in progress" in prompt


def test_the_superseded_wording_is_gone(prompt):
    """'Two to three sentences' was the ceiling being met while the real defect
    ran unchecked. Leaving both would let the model pick the looser one."""
    assert "Two to three sentences" not in prompt


# ── the safety rules this sits next to must survive ─────────────────────────

@pytest.mark.parametrize("rule", [
    "AI DISCLOSURE",                       # T-0
    "Adults fifteen and over only",        # age gate, verified live on call 5
    "Children under fifteen not seen",     # its counterpart
])
def test_neighbouring_safety_rules_intact(prompt, rule):
    """A brevity edit is exactly the kind of change that quietly trims a safety
    instruction sitting nearby.

    The exact wording is pinned rather than paraphrased. Writing this test with
    a guessed phrase ("minimum age") failed while the gate was perfectly
    intact — which is the same trap that produced T-0 and T-4 on this branch:
    assume the prompt says what you expect and you test nothing.
    """
    assert rule.lower() in prompt.lower(), f"{rule!r} disappeared from the prompt"
