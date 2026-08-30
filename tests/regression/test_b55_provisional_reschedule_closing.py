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
#
# ⚠️ Re-pinned 2026-08-05 to DATE-REDACTED hashes — see `_sha`. The previous
# values were raw hashes of a prompt containing today's date, so they were only
# ever valid on the day they were taken.
UNCHANGED_CLINIC_PROMPTS = {
    "demo": "17c7162e49200716",
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
    # Re-pinned 2026-08-04 (2nd time today), 023c5092170d2f31 -> 46d6a95cf2c4f7b9.
    # ONE line left jv_v1's prompt, and its removal is the fix:
    #     "There is no home-visit option — never offer one."
    # jv_v1 sells a named Home Visit service at £80 with a declared area of
    # Bolton and Greater Manchester, and its own FAQ answers "Yes — we offer
    # home visits". The engine hung that denial off the REMOTE flag, a different
    # axis, so both template_v1 clinics were told to refuse a service they sell.
    # Owner-confirmed 2026-08-04. demo/theorem/theorem_v3 are untouched below:
    # they use a different prompt engine and never render the step-2 block.
    # Re-pinned 2026-08-05. BOOKING STEPS 1 gained a condition-led opening
    # exception, gated on the condition library — so jv_v1 moves and every
    # clinic without one (vital_edge, demo, theorem, theorem_v3) is
    # byte-identical, which is the containment claim this table exists for.
    # See tests/regression/test_condition_led_opening.py.
    # Re-pinned 2026-08-09: the FAQ block gained the answer-length rules Theorem
    # got on 2026-08-04 (e2a44f3) — ANSWER ONLY WHAT WAS ASKED with its two
    # live examples, a sentence-LENGTH cap independent of count, ONE OFFER
    # NEVER TWO, and the slot-list exemption. The edit is confined to
    # clinic_template_prompt.py, so jv_v1 moves and demo / theorem /
    # theorem_v3 are byte-identical — that containment was verified, not
    # assumed, and it is the whole reason this row is worth updating rather
    # than deleting. vital_edge moves too and is deliberately unpinned.
    # See tests/regression/test_faq_answer_length_template.py.
    # Re-pinned 2026-08-09 again: the AI DISCLOSURE rule (8d3a22f, ported from
    # theorem_v3 into _render_identity) plus the manner-vs-claim clause in
    # VOICE RULES. demo / theorem / theorem_v3 byte-identical, as above.
    # See tests/regression/test_ai_disclosure_template.py.
    # Re-pinned 2026-08-10: CALL STATE now states a withheld caller ID in words
    # rather than omitting the line, ported from theorem_v3 (4cf79d9) — the
    # template clinics carried the same hole for four days after Theorem was
    # fixed. Confined to clinic_template_prompt.py, so jv_v1 moves and demo /
    # theorem / theorem_v3 are byte-identical; theorem_v3 does not move because
    # it already had the branch. vital_edge moves too and stays unpinned here.
    # Verified, not assumed. See
    # tests/regression/test_no_caller_id_asks_for_the_keypad.py.
    # Re-pinned 2026-08-11: b60be0364138866c -> fda61dff2429f6a2. NOT an engine
    # change — jv_v1 gained prompt_facts.reason_question in clinic.json, so rule
    # 1b now renders the clinic's own wording plus the once-only tightening that
    # opting in carries. Owner decision, same day: Joint Venture DOES ask what
    # the appointment is for; the never-ask decision is Theorem's alone.
    #
    # This row moving is the OPT-IN working as designed, and the containment
    # claim is unchanged: demo / theorem / theorem_v3 are byte-identical, which
    # is what proves the reason-question mechanism is still gated per clinic and
    # has not leaked into shared text. Verified, not assumed.
    # See tests/regression/test_reason_gate_is_clinic_scoped.py.
    # Re-pinned 2026-08-11 (2nd time today): fda61dff2429f6a2 -> 2c377ec8e1d6b181.
    # The RESCHEDULE / CANCEL flow stopped assuming a caller ID exists — turn 2
    # now branches (a)/(b) on what CALL STATE actually holds, and two sentences
    # that asked about "the number you're calling on" were made true for a
    # number that was TYPED. Confined to clinic_template_prompt.py, so EXACTLY
    # the two template_v1 clinics move (jv_v1 here, vital_edge deliberately
    # unpinned in this table) and demo / theorem / theorem_v3 are
    # byte-identical. Verified, not assumed.
    # See tests/regression/test_reschedule_phone_without_caller_id_template.py.
    # Re-pinned 2026-08-15 Job 3c.5: ban "time preference noted" form-filling
    # in ACKNOWLEDGEMENT RULE (CAce1457d1).
    # Re-pinned 2026-08-15 Job 3c.2: OUT-OF-WINDOW acknowledgement in SLOT
    # PRESENTATION (CAce1457d1).
    # Re-pinned 2026-08-16 Batch 1.1: withheld keypad line says why first
    # (CA86dfad89 A9a).
    # Re-pinned 2026-08-21, 63eb3b1a2899513b -> 243a1be416ea9fc9. ONE line of
    # jv_v1's prompt moved, and it is a safety fix, not drift: the
    # trauma_fracture screen question used to open "are you able to use it or
    # put weight through it", which made "yes" the REASSURING answer. The block
    # it sits in reads "IF ANY YES / positive -> do NOT book", and
    # classify_screen_answer grades an affirmative lead as red_flag, so both
    # layers had it backwards: the caller who could walk was refused and sent to
    # A&E, and the caller who could not was cleared and booked in for hands-on
    # physio. Now asks "is it too painful to use it or put your weight through
    # it". Verified by diffing the rendered prompt: exactly that one ASK line
    # differs, and demo/theorem/theorem_v3 are byte-identical, which is the
    # containment claim this table exists for.
    # Re-pinned 2026-08-21, 11fc9c7fcab478d9 -> a3348f65d2f5c68c (jv_v1) and
    # 10bd9f5d9cb71e45 -> b2fb93a133d11f0b (vital_edge). Hold-speech work: the
    # "One filler phrase per tool call maximum" block became a positive rule
    # never to open a reply with a holding phrase, because the system already
    # speaks one and the model was saying a second (95 fragments across 73 of
    # the stored calls). Only the template_v1 clinics move; theorem_v3 and demo
    # stay byte-identical, which is the containment claim.
    # Re-pinned 2026-08-21, 243a1be416ea9fc9 -> 11fc9c7fcab478d9. Phase 4 tone
    # pass: FOUR ASK lines moved and nothing else. The lead-ins used to
    # apologise for the question ("Sorry to ask", "Just to be safe before we
    # book anything"), which is what made a benign caller hear a red-flag
    # screen as an accusation; they now say the question is routine and asked
    # of everyone. The clinical half of each question is byte-identical, and
    # trauma_fracture + inflammatory were deliberately not touched.
    # Verified by rendering every clinic before and after: exactly those four
    # ASK lines differ, and demo / theorem / theorem_v3 / vital_edge are
    # byte-identical, which is the containment claim this table exists for.
    # Deliberately NOT reworded: any hint at the expected ANSWER ("almost
    # everyone says no to these"). classify_screen_answer grades a negative
    # lead as `clear`, so priming manufactures false clears — see
    # tests/regression/test_a_screen_question_is_framed_as_routine.py.
    # Re-pinned 2026-08-22: jv_v1 a3348f65d2f5c68c -> 271fe1f70c369bb9,
    # vital_edge b2fb93a133d11f0b -> 26e9742b3252cf35. The identity block
    # gained two rules after a routing request was answered badly in both
    # directions: theorem CA82ec06 opened "No -" at a caller asking to be
    # put through, jv CA9ca88398 said "I'm the receptionist here" with the
    # AI word missing. Both template clinics render the shared block, so
    # both move together and neither can move alone.
    #
    # CONTAINMENT, which is what this table is for: demo, theorem and
    # theorem_v3 are byte-identical across the change. Theorem runs a
    # hardcoded prompt of its own, so nothing here reaches Mark's line.
    # Re-pinned 2026-08-28, c66d6e9aff4c8787 -> a55429e037c05913. The shared
    # template named REAL PRACTITIONERS in engine code: _render_insurance said
    # "Marcus will be in touch", and two callback examples said "Jonathan/
    # Marcus". So each template clinic's model was told about the OTHER
    # clinic's practitioner — vital_edge's prompt carried "Marcus" and jv_v1's
    # carried "Jonathan" — and a fourth clinic would have got both. All four
    # now render tk["practitioner"].
    #
    # Found by tests/tenancy/: a clinic built entirely from config still had
    # the donor's practitioner in its rendered prompt, which can only come from
    # engine code. Nothing else in this file's containment claim changes —
    # demo / theorem / theorem_v3 use a different engine and are byte-identical.
    #
    # Re-pinned 2026-08-30, a55429e037c05913 -> d4fb03b5e5b56c7e. O-1: the
    # booking sequence gained rung "1c. SESSION LENGTH", which orders the
    # length question BEFORE the timing question in step 2. The question itself
    # is not new — it rendered as a free-floating "DURATION QUESTION FOR
    # <SERVICE>" fact with no position in the ladder, which is why it only ever
    # got asked when `duration_choice_gate` blocked the tool, i.e. after the
    # caller had already answered a timing question. Owner-reported from the
    # demo line.
    #
    # jv_v1 moves because it sells Sports Massage at [30, 60]; it is the only
    # clinic in this table that does. demo, theorem and theorem_v3 sell no
    # multi-length service and are byte-identical across the change — which is
    # the containment claim this table exists to prove, and the reason the rung
    # is gated on `_spine_has_duration_choice(clinic)` (a predicate over the
    # catalogue) rather than on a clinic id.
    "jv_v1": "d4fb03b5e5b56c7e",
    "theorem": "8565be9a48a7a9aa",
    # Re-pinned 2026-08-25: 'Children under fifteen not seen' -> 'Children
    # under seven not seen'. Mark's minimum age is 7 (owner-confirmed
    # 2026-07-10) and the prompt was the only source still saying fifteen —
    # see tests/regression/test_theorem_minimum_age_has_one_value.py. The
    # rendered prompt diff is exactly that ONE line; verified before
    # re-pinning rather than accepted because the test went red.
    # Moved 2026-08-25, deliberately: 76f54d49d6b62012 -> e626b57ddfc6d84c.
    # The CLINIC block opened "Adults fifteen and over only" — a SIXTH source of
    # Mark's age policy, missed by the five-source sweep because it is worded as
    # an adults-only claim rather than a "children under N" one. It was the
    # sentence a caller actually heard: on CA750c8d70d2ecab156fc87540749fc863
    # (Mark's live line, 14:51) a parent asked about their son's ankle and Susie
    # said "we do see patients from fifteen years old". They rang off. Now
    # "Patients seen from seven years old."
    #
    # CONTAINMENT: demo, jv_v1, theorem and vital_edge all HELD — the edit is
    # inside _build_theorem_v3's CLINIC block, which only theorem_v3 renders.
    # Moved 2026-08-25, deliberately: e626b57ddfc6d84c -> d41b67bc0bd992f2.
    # Softening the CLINIC block to "Children under seven not seen" (the fix
    # above) had a side effect nobody asked for: Susie stopped asking a child's
    # age at all. The old "Adults fifteen and over only" wording read as a hard
    # restriction and the model volunteered the check off its own bat; there
    # has never been a RULE telling it to. Two calls on build 8819dc50bd4b went
    # straight to booking for a child whose age was never established, which
    # left the deterministic under-age gate — which only arms from an age the
    # caller STATES — dormant on exactly the calls it exists for.
    #
    # So the ask is now a rule rather than an accident. The rendered static diff
    # is exactly that ONE added line; verified against HEAD before re-pinning
    # rather than accepted because the test went red.
    #
    # CONTAINMENT: demo, jv_v1, theorem and vital_edge all HELD — the edit is
    # inside _build_theorem_v3's POLICIES block, which only theorem_v3 renders.
    "theorem_v3": "d41b67bc0bd992f2",
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
    """Hash the rendered prompt with today's date redacted.

    Raw hashes of this prompt rot at midnight — it interpolates the current
    date — so all four pins below were failing on 2026-08-05 for no reason but
    the calendar, and had been quietly contributing to the standing baseline.
    A scope guarantee that fails daily stops being read, which is the opposite
    of what it is for.

    Weekday names, month names and digit runs are redacted; the WORDING, which
    is what "did this fix leak into another clinic's script" actually asks, is
    still hashed. Kept identical to the helper in
    `test_b57_theorem_cancel_gate.py` so the two tables move together.
    """
    text = _DATEISH_RE.sub("<date>", _rendered(clinic_id))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


_DATEISH_RE = re.compile(
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\b"
    r"|\d+",
    re.IGNORECASE,
)


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
