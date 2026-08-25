"""Mark's minimum age must read the same from every source that states it.

On 2026-08-25 it read four different ways:

    7   app/clinics/theorem/canonical.py AGE_POLICY — owner-confirmed
        2026-07-10, and the value that stands
    15  clinic_config patient_policies.min_patient_age
    15  susie_system_prompt "Children under fifteen not seen" — the ONLY one
        a caller ever heard, so for six weeks the clinic turned away the
        7-14 year olds it actually sees
    18  clinic_config never_autobook "Adults only — no paediatric patients"

canonical.py had even recorded the conflict as RESOLVED, adding "the live susie
prompt (7+) is correct". The prompt said fifteen. A decision was written down
and never applied anywhere it could take effect.

This is the same shape as the clinic's opening hours (four sources, one live)
and the reason-question suppression (config with no renderer). The remedy that
works is not "pick the right one" — it is a test that fails the moment they
disagree again, because the next reader will not know which to trust.

MINIMUM is the single expected value. Change it here and the failures tell you
every place that has to move with it.
"""

import pytest

from app.clinic_config import CLINICS, get_clinic
from app.clinics.theorem import canonical as theorem_canonical
from app.tools.receptionist_tools import minimum_age_years

MINIMUM = 7

# What Susie says out loud. Spelled, because the prompt is read aloud and
# ElevenLabs renders "7" and "seven" differently. The other forms are kept so
# the prompt can be checked for a stale wording surviving beside the new one.
SPOKEN_FORMS = {7: "seven", 15: "fifteen", 18: "eighteen"}
SPOKEN = SPOKEN_FORMS[MINIMUM]

THEOREM_IDS = ("theorem", "theorem_v2", "theorem_v3")


def test_canonical_age_policy():
    assert theorem_canonical.AGE_POLICY["min_patient_age"] == MINIMUM


def test_clinic_config_patient_policies():
    pol = CLINICS["theorem"]["patient_policies"]
    assert pol["min_patient_age"] == MINIMUM
    assert pol["no_children"] is False, (
        "no_children=True would mean no minors at all, which contradicts a "
        "minimum age of 7"
    )


@pytest.mark.parametrize("cid", THEOREM_IDS)
def test_the_engine_gate_reads_the_same_number(cid):
    """theorem_v2 and _v3 are deepcopies made further down clinic_config, so a
    key added to the 'theorem' literal AFTER those lines silently never reaches
    the live clinic. theorem_v3 is what +447380841468 loads."""
    assert minimum_age_years(get_clinic(cid)) == MINIMUM


def test_the_live_prompt_says_the_same_number():
    """_build_theorem_v3 is what Mark's line actually renders — clinic.json does
    not reach it. Asserted against the built prompt, not the source literal, so
    moving the line between blocks does not silently pass."""
    from app.prompts.susie_system_prompt import build_system_prompt
    prompt = build_system_prompt({"clinic_id": "theorem_v3"})
    assert f"Children under {SPOKEN} not seen" in prompt
    for wrong in set(SPOKEN_FORMS) - {SPOKEN}:
        assert f"under {wrong} not seen" not in prompt, (
            f"the prompt still carries the old {wrong!r} wording alongside the "
            f"corrected one — Susie would state two different policies"
        )


def test_no_stale_age_WORD_survives_anywhere_in_the_live_prompt():
    """The catch-all, added after a SIXTH source got through.

    The test above pins one PHRASE — "Children under seven not seen" — and
    checks the old wording is not sitting beside it. On 2026-08-25 that was not
    enough: the CLINIC block opened with

        "Closed all UK bank holidays. Adults fifteen and over only."

    Different sentence, same claim, and it was the one a caller heard. On
    CA750c8d70d2ecab156fc87540749fc863 (Mark's live line, 14:51) a parent asked
    about their son's ankle and Susie said "we do see patients from fifteen
    years old". They rang off.

    So this asserts on the WORD, not on any phrasing: no spelled age other than
    the real minimum may appear in the rendered prompt at all. Phrasing is
    where the previous five fixes kept leaking.
    """
    from app.prompts.susie_system_prompt import build_system_prompt
    prompt = build_system_prompt({"clinic_id": "theorem_v3"}).lower()
    for age, word in SPOKEN_FORMS.items():
        if age == MINIMUM:
            continue
        assert word not in prompt, (
            f"the live theorem_v3 prompt still says {word!r} somewhere. Every "
            f"spelled age in this prompt is an age policy claim, and the "
            f"minimum is {MINIMUM}. Find the sentence and change it — this is "
            f"the sixth place it has hidden."
        )


def test_no_adults_only_claim_survives_anywhere():
    """The 'Adults only — no paediatric patients' line is read by no Python, but
    it is read by people, and it is what made the other three hard to trust."""
    blob = " ".join(str(v) for v in CLINICS["theorem"].get("never_autobook") or [])
    assert "Adults only" not in blob or "contradicted" in blob, (
        "never_autobook still asserts an adults-only policy as fact"
    )


def test_a_number_between_the_minimum_and_eighteen_is_not_refused():
    """The regression that mattered: a 12-year-old is a patient this clinic
    sees, and both the old prompt line and an 18 gate would have turned them
    away."""
    from app.media_streams.connection import capture_under_age
    for age in range(MINIMUM, 18):
        session = {"clinic_id": "theorem_v3"}
        assert capture_under_age(session, f"my son is {age}") is None, (
            f"a {age}-year-old was gated, but the clinic sees {MINIMUM}+"
        )
        assert "_under_age_declared" not in session


def test_below_the_minimum_still_arms_the_gate():
    from app.media_streams.connection import capture_under_age
    session = {"clinic_id": "theorem_v3"}
    assert capture_under_age(session, f"she's {MINIMUM - 1}") == MINIMUM - 1
    assert session["_under_age_declared"] == MINIMUM - 1
