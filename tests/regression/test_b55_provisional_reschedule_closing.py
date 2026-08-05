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
# Re-pinned 2026-08-05 for B-39 (retention question scoped to the cancel path
# and bounded to one ask). jv_v1 and theorem_v3 move deliberately; demo and
# theorem are verified unchanged.
UNCHANGED_CLINIC_PROMPTS = {
# Re-pinned 2026-08-04 (T-4, commit 76cef3d). demo, theorem and theorem_v3 all
# moved together because the caller-ID read-back fix edits all three renderers:
# build_system_prompt, get_system_prompt's known-context block, and the
# _build_theorem_v3 worked examples. Susie used to offer "just say use this
# number" without ever speaking the digits, so callers confirmed a number they
# had never heard. Intended drift, not B-55 leaking into confirmed-booking
# clinics — the property this table exists to prove still holds.
    "demo": "a26c6a5ec53d4a88",
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
    "jv_v1": "7d90d044ca7534b2",
    "theorem": "edb23ef9e7aea7ed",
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
    # Re-pinned again 2026-08-04 (T-5). theorem_v3 only, once more: the FAQ
    # answer-length rule lives inside _build_theorem_v3. Susie was running
    # 20s answers because "don't volunteer information not asked about"
    # was present and ignored — hours + parking drew hours, parking, the
    # train-station walk and two competing offers.
    # Re-pinned 2026-08-05 (T-18). theorem_v3 only, again — demo and theorem
    # are unchanged, which is the useful half: the RESCHEDULE / CANCEL FLOW
    # was ported from latency-eval into _build_theorem_v3 and did not leak
    # into the shared renderers. The flow used to be code-driven (the model
    # acked, code injected the clinic and phone questions); the model now owns
    # the opening turn, reads the caller-ID number back digit-grouped, and no
    # code path literal-matches its speech. See
    # tests/regression/test_reschedule_flow_is_model_driven.py.
    # Re-pinned 2026-08-05 again — joint injections, Mark's new service, added
    # to _build_theorem_v3 from theoremhealth.co.uk/joint-injections. theorem_v3
    # only; demo and theorem unchanged. See
    # tests/regression/test_joint_injections_service.py.
    # Re-pinned 2026-08-05 (T-18 follow-up). Three blocks outside the
    # RESCHEDULE / CANCEL FLOW were still teaching the code-driven contract —
    # the banned-openers carve-out ("the system handles that automatically"),
    # ONE QUESTION PER TURN making ack-and-stop the global default, and the
    # new-booking flow's first step swallowing reschedule intent. theorem_v3
    # only; demo and theorem unchanged.
    # Re-pinned 2026-08-05 (owner's call). The reschedule/cancel flow asks
    # which clinic again, and asks it FIRST — the model asks it in the same
    # turn as the ack, and stores the answer with collect_and_store. theorem_v3
    # only; demo and theorem unchanged.
    # Re-pinned 2026-08-05 after the first live injection call (CA0f74573f):
    # the JOINT INJECTIONS block gained a HOW TO USE THIS SECTION rule after
    # it was read as a script — 21.6s and an unasked £235. theorem_v3 only.
    "theorem_v3": "16bba02393e9af60",
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


# Every rendered prompt embeds today's date and the current week's boundaries —
# "5 August 2026 (London time). This week runs until Sunday 9 August 2026. Next
# week runs Monday 10 August 2026 to Sunday 16 August 2026." So a raw hash of
# the prompt changes at every midnight, and this table went red on 2026-08-05
# with nobody having touched a prompt file.
#
# That is a false alarm on a suite whose real failures are tracked by DIFFING
# the failing set, so noise here costs real signal. Dates are normalised out
# before hashing: the table still proves a prompt EDIT did not leak into
# another clinic, which is all it was ever for, and it no longer fails for the
# passage of time.
_DATE_NOISE = (
    re.compile(r"\d{1,2} (?:January|February|March|April|May|June|July|August"
               r"|September|October|November|December) \d{4}"),
    re.compile(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
               r" \d{1,2}(?:st|nd|rd|th)? (?:January|February|March|April|May|June"
               r"|July|August|September|October|November|December)"),
    re.compile(r"\d{4}-\d{2}-\d{2}"),
)


def _date_normalised(text: str) -> str:
    for pattern in _DATE_NOISE:
        text = pattern.sub("<DATE>", text)
    return text


def _sha(clinic_id: str) -> str:
    body = _date_normalised(_rendered(clinic_id))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


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
