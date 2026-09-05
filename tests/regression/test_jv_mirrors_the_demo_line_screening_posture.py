"""
JV runs the demo line's screening posture, and the 999 intercept survives it.

OWNER decision (Quentin), 2026-09-05, after four rounds of demo-line calls.
`jv_v1` now carries the same two keys northgate took the same day:

    clinical_screening.enabled  = false
    condition_knowledge.mandatory = false

THE ARGUMENT IS ROLE COHERENCE, NOT BASE RATES. A receptionist who asks a
red-flag question and then says "that's reassuring" and books has ADJUDICATED,
on a recorded line, without being a clinician and without the screens being
validated. Declining to ask is staying in lane; asking and clearing is triage
done badly. The check MOVES to the assessment, where it is indemnified and
trained for -- it is not deleted.

WHAT MUST SURVIVE, and is the reason this file exists. `emergency_keywords()`
reads the keywords being CONFIGURED and never reads `enabled`, so the
deterministic 999/A&E intercept is independent of the screens. Routing that
lookup through `screening_config()` would look like a tidy-up and would take
the emergency response with it, silently, with nothing failing and nothing in
the log. THAT is the regression this file is here to catch -- on a patient
line, not a demo line.

ACCEPTED RESIDUAL RISK, recorded rather than glossed: the presentations these
six screens target are the ones that do NOT feel serious to the caller -- DVT
reads as a strained calf, cauda equina as ordinary back pain -- so those
callers are now booked instead of asked. Transferred to the assessment, not
removed. Marcus reviewed the screens' wording before go-live and has NOT signed
off switching them off; that is recorded in `clinic.json` deliberately.

SCOPE. Only `jv_v1` changed. Verified by hashing every clinic's rendered prompt
either side: vital_edge, northgate and theorem_v2/v3 are byte-identical, and
jv_v1 moved 111,269 -> 105,383 chars -- within one character of the 5,885 that
northgate lost for the same edit.
"""

import pytest

from app.clinic_config import get_clinic
from app.media_streams.clinical_screening import (
    detect_emergency,
    emergency_intercept_enabled,
    emergency_keywords,
    emergency_response_text,
    screening_enabled,
    update_screening_state,
)


CHEST_PAIN = "yeah i've got chest pain and i can't breathe"

# The presentations the six screens used to arm on.
SCREENED_PRESENTATIONS = [
    "my lower back's been really bad and my leg's gone numb",
    "my calf is swollen and hot",
    "i fell off my bike and heard a crack",
    "my neck hurts and i keep going dizzy",
    "i've been getting night sweats and my back is worse at night",
    "i can't control my bladder and my back is agony",
]


def _jv():
    cl = get_clinic("jv_v1")
    if not cl:
        pytest.skip("jv_v1 not present on this branch")
    return cl


# ---------------------------------------------------------------------------
# The posture itself
# ---------------------------------------------------------------------------
def test_jv_matches_the_demo_line_exactly():
    """The ask was 'exactly like the demo line', so assert the equality.

    Comparing against northgate rather than against literals means the two
    cannot drift apart silently: if northgate's posture is revised and JV's is
    not, this fails and the divergence is a decision someone has to take
    rather than something that just happened.
    """
    jv, demo = _jv(), get_clinic("northgate")
    if not demo:
        pytest.skip("northgate not present on this branch")

    for key, block in (("enabled", "clinical_screening"),):
        assert (jv.get(block) or {}).get(key) == (demo.get(block) or {}).get(key), (
            f"jv_v1.{block}.{key} no longer matches northgate"
        )
    assert (jv.get("condition_knowledge") or {}).get("mandatory") == (
        (demo.get("condition_knowledge") or {}).get("mandatory")
    ), "jv_v1.condition_knowledge.mandatory no longer matches northgate"


def test_the_screens_are_off_but_kept_on_file():
    """Off is one boolean. Deleting them would make the decision hard to undo."""
    jv = _jv()
    cs = jv.get("clinical_screening") or {}
    assert cs.get("enabled") is False
    assert screening_enabled(jv) is False
    assert len(cs.get("screens") or []) == 6, (
        "the six screens have been deleted rather than switched off -- keep "
        "them so reversing the decision stays a one-key change"
    )


def test_the_condition_library_is_kept_only_the_compulsion_is_dropped():
    """`mandatory: false` is not a safety setting -- it is warmth and expertise.

    Dropping the library too would remove the capability the owner explicitly
    wanted to keep.
    """
    jv = _jv()
    ck = jv.get("condition_knowledge") or {}
    assert ck.get("mandatory") is False
    assert len(ck.get("conditions") or []) >= 39, (
        f"the condition library has shrunk to {len(ck.get('conditions') or [])} "
        "-- `mandatory: false` drops the COMPULSION, never the library"
    )


def test_no_dangling_reference_to_a_block_that_no_longer_renders():
    """A rule asserting a false premise is the pattern that has bitten this
    codebase repeatedly. With the screens off, `how_to_use` must not still
    promise that a screen comes first."""
    ck = _jv().get("condition_knowledge") or {}
    how = (ck.get("how_to_use") or "").lower()
    assert "screen comes first" not in how, (
        "condition_knowledge.how_to_use still defers to a safety screen that "
        "no longer renders"
    )


@pytest.mark.parametrize("utterance", SCREENED_PRESENTATIONS)
def test_no_screen_arms_on_jv_any_more(utterance):
    result = update_screening_state({}, _jv(), utterance)
    assert result["action"] == "none", (
        f"a screen still arms on {utterance!r} -- action={result['action']}"
    )


# ---------------------------------------------------------------------------
# ...and the half that must never regress
# ---------------------------------------------------------------------------
def test_the_999_intercept_survives_the_screens_being_off():
    """`emergency_keywords()` keys on the keywords being CONFIGURED and never
    reads `enabled`. Routing it through `screening_config()` would look like a
    tidy-up and would silently take the emergency response with it."""
    jv = _jv()
    assert emergency_intercept_enabled(jv) is True
    assert len(emergency_keywords(jv)) == 21
    assert detect_emergency(CHEST_PAIN, jv) is True

    result = update_screening_state({}, jv, CHEST_PAIN)
    assert result["action"] == "emergency"
    assert result["speak"] == emergency_response_text(jv)
    assert "999" in result["speak"]
    # ...and B-139: nothing is appended that competes with "call 999".
    assert "put you through" not in result["speak"].lower()


def test_the_intercept_does_not_depend_on_screening_being_enabled():
    """Stated as the rule, not as an example.

    This is the exact coupling that would make switching the screens off take
    the emergency response with it, on a live patient line, with nothing
    failing and nothing in the log.
    """
    jv = _jv()
    assert screening_enabled(jv) is False
    assert emergency_intercept_enabled(jv) is True, (
        "the emergency intercept has become conditional on screening being "
        "enabled -- with the screens off, a caller reporting chest pain would "
        "now be booked"
    )


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cid,screens,enabled", [
    ("vital_edge", 0, True),
    ("theorem_v3", 0, None),
])
def test_the_other_live_clinics_are_untouched(cid, screens, enabled):
    """vital_edge and theorem already had no screens; this decision was about
    the one clinic that did."""
    cl = get_clinic(cid)
    if not cl:
        pytest.skip(f"{cid} not present on this branch")
    cs = cl.get("clinical_screening") or {}
    assert len(cs.get("screens") or []) == screens
    assert cs.get("enabled") is enabled
    # ...and every one of them keeps the intercept.
    assert emergency_intercept_enabled(cl) is True
