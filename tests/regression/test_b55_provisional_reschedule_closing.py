"""B-55 — a provisional clinic must not narrate a reschedule as confirmed.

`clinic_template_prompt.py`'s `is_provisional` arm rewrote the BOOKING success
line and banned 'all booked' / 'confirmed' / "you're booked in". It stopped
there. The RESCHEDULE closing was shared, so Vital Edge was instructed — word
for word, with no model judgement in the loop — to say:

    'That's you rescheduled — you're now in for Monday the 1st of June at
     three in the afternoon. We'll see you then — take care.'

On a provisional clinic that is false. Moving a pending request leaves it
pending until Jonathan agrees.

Gate 5f cannot backstop it: `_armed_write_families` arms the reschedule family
only on a REFUSAL, and a successful reschedule refuses nothing. That is correct
for every confirmed-booking clinic and leaves a provisional one with nothing
behind the prompt.

Two halves are pinned here:

  1. Vital Edge gets a provisional-aware reschedule closing, and the old
     confirmed wording survives only inside the prohibition that forbids it.
  2. Every non-provisional clinic renders a BYTE-IDENTICAL prompt. The fix was
     required to change Vital Edge and nothing else; test 2 is what makes that
     claim checkable rather than asserted.
"""
import hashlib
import re

import pytest

from app.prompts.susie_system_prompt import build_system_prompt_parts
from app.media_streams.turn_handler import (
    _false_write_claim,
    WRITE_FAMILY_BOOKING,
    WRITE_FAMILY_RESCHEDULE,
)

# Prompt SHA-256 for every clinic, captured on latency-eval immediately BEFORE
# the B-55 edit. Vital Edge is deliberately absent: its prompt is meant to move.
#
# If one of these fails after an unrelated prompt change, that is the test doing
# its job — confirm the change was intended for that clinic, then re-baseline
# with:  python -c "from tests.regression.test_b55_provisional_reschedule_closing
#        import _sha; print(_sha('jv_v1'))"
UNCHANGED_CLINIC_PROMPTS = {
    "demo": "a245db1d4d06abd5",
    "jv_v1": "1c14e1bf976fdb0d",
    "theorem": "61b93fdac3e8fe18",
    "theorem_v3": "e6202afb47d91820",
}

OLD_CONFIRMED_WORDING = ("that's you rescheduled", "you're now in for")


def _rendered(clinic_id: str) -> str:
    session = {
        "call_sid": "CAtest_b55",
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "collected": {},
    }
    static, dynamic = build_system_prompt_parts(session)
    return f"{static}\n\n{dynamic}"


def _sha(clinic_id: str) -> str:
    return hashlib.sha256(_rendered(clinic_id).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 1. Vital Edge
# ---------------------------------------------------------------------------
def test_vital_edge_reschedule_closing_is_provisional_aware():
    low = _rendered("vital_edge").lower()
    assert "reschedule closing — this booking is provisional" in low, (
        "Vital Edge is not getting the provisional reschedule closing."
    )
    assert "it's not confirmed until he comes back to you" in low


@pytest.mark.parametrize("phrase", OLD_CONFIRMED_WORDING)
def test_old_confirmed_wording_survives_only_inside_the_prohibition(phrase):
    """The banned wording may appear only where it is being forbidden."""
    low = _rendered("vital_edge").lower()
    for m in re.finditer(re.escape(phrase), low):
        window = low[max(0, m.start() - 200):m.start()]
        assert "do not say" in window, (
            f"{phrase!r} occurs outside a prohibition: "
            f"...{low[max(0, m.start() - 130):m.end() + 40]!r}"
        )


def test_the_ve_reschedule_closing_is_not_a_gate5f_claim():
    """The line Susie is told to say must not itself read as a completion claim.

    Gate 5f is disarmed on a successful reschedule, so this is not what protects
    the caller — but a mandated line that trips the detector is a mandated false
    promise, which is exactly what B-55 was.
    """
    low = _rendered("vital_edge").lower()
    start = low.find("reschedule closing")
    assert start != -1
    # The mandated sentence only, up to the end of the quoted line.
    quoted = low[start:low.find("take care.'", start) + len("take care.'")]
    spoken = quoted[quoted.find("'"):]
    for family in (WRITE_FAMILY_RESCHEDULE, WRITE_FAMILY_BOOKING):
        assert not _false_write_claim(spoken, family), (
            f"the mandated VE reschedule closing reads as a {family} "
            f"completion claim: {spoken[:160]!r}"
        )


# ---------------------------------------------------------------------------
# 2. Every other clinic is untouched
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clinic_id,expected", sorted(UNCHANGED_CLINIC_PROMPTS.items()))
def test_non_provisional_clinics_render_byte_identical_prompts(clinic_id, expected):
    assert _sha(clinic_id) == expected, (
        f"{clinic_id}'s system prompt changed. B-55 was scoped to "
        f"is_provisional and must not alter any confirmed-booking clinic."
    )


def test_the_confirmed_closing_still_reaches_non_provisional_clinics():
    """The else-branch must still say the original line — the fix is a branch,
    not a removal. jv_v1 is the live confirmed-booking template clinic."""
    low = _rendered("jv_v1").lower()
    assert "that's you rescheduled — you're now in for" in low
