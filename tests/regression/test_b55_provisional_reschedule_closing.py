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
# Re-pinned 2026-08-04 (T-4, commit 76cef3d). demo, theorem and theorem_v3 all
# moved together because the caller-ID read-back fix edits all three renderers:
# build_system_prompt, get_system_prompt's known-context block, and the
# _build_theorem_v3 worked examples. Susie used to offer "just say use this
# number" without ever speaking the digits, so callers confirmed a number they
# had never heard. Intended drift, not B-55 leaking into confirmed-booking
# clinics — the property this table exists to prove still holds.
    "demo": "24113de0267dac94",
    # Re-pinned 2026-08-04. jv_v1 is the only other clinic with a
    # duration-choice service, so it is the only one the DURATIONS-ARE-FIXED
    # rewrite could move — demo, theorem and theorem_v3 are byte-identical
    # across that change, which is the property this table exists to prove.
    #
    # What moved, and why it is a FIX for jv_v1 rather than drift: the engine
    # used to hardcode "(there is no 30-minute session)" into that block.
    # jv_v1's Sports Massage IS 30 minutes at £40 — so Susie was being handed
    # a sentence denying a session the clinic sells, in the same breath as the
    # line offering it. The claim is now scoped per service and derived from
    # clinic.json instead of asserted in engine code.
    "jv_v1": "023c5092170d2f31",
    "theorem": "5de9580c45d5fb15",
    # Re-pinned 2026-08-04 on theorem-onboarding ONLY — this value deliberately
    # diverges from latency-eval's e6202afb47d91820, and a cherry-pick conflict
    # here is expected rather than a mistake.
    #
    # theorem_v3 is the clinic this branch exists to port. Its prompt is edited
    # by 90d723a (persona/language/Redditch/pacing), 6dbee13 (concern block),
    # 6127399 (£75 -> £85), f94c7e7 (reschedule closing onto the Gate 5f
    # wording) and 3087d3b (the A3 surname read-back). B-55 did not move it —
    # the port did, and every one of those changes is covered by its own
    # regression test.
    #
    # `demo` and `theorem` still match latency-eval exactly, which is the useful
    # half of this row: the port's prompt work is confined to _build_theorem_v3
    # and has not leaked into the shared builders.
    # Re-pinned 2026-08-04 (T-0). theorem_v3 ONLY this time — demo and theorem
    # are unchanged, which is the useful signal: the AI-disclosure rule was
    # added inside _build_theorem_v3 and did not leak into the shared
    # renderers. Susie answered "Yes" to "are you a real person" because the
    # rendered theorem_v3 prompt had no disclosure instruction at all.
    "theorem_v3": "93568fb7f0496419",
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
