# tests/regression/test_under_age_booking_gate.py
"""
A caller under the clinic's minimum age cannot be walked into a booking.

Vital Edge acceptance run, `CA7d7c109b`, 4 Aug 2026. Jules' note: *"after
under-18 she later asked day/time (booking nudge)"*. The decline itself was
CORRECT — Susie said the clinic does not see under-18s — and then the
conversation carried on collecting a day and a time.

**Why nothing stopped it.** Two mechanisms exist and neither runs:

  * `evaluate_policy_gate` has a real minor check and is imported ONLY by
    `flow.py`. Every live clinic is free-form (`is_freeform_clinic` is True for
    vital_edge, jv_v1 and theorem_v3), so the FlowEngine turn loop is never
    entered and the gate never executes. Its own tests
    (`test_policy_gate.py::TestTheoremHealthPolicy::test_theorem_minor_age_15_declined`
    and four siblings) have been RED inside the accepted 95-failure baseline.
  * `never_autobook: ["Anyone under 18"]` in clinic.json is read by no Python
    anywhere in `app/`.

So the only thing between a 15-year-old and a booking was the model choosing to
comply. That is the same shape as B-36 R6 and the duration bug: a rule stated in
the prompt with no engine state behind it.

**Two halves, and the second is the one that fixes the observed behaviour.** The
write gate refuses `book_appointment` — but on `CA7d7c109b` no booking was ever
attempted, so a write gate alone would have changed nothing the caller heard.
The CALL STATE line is what stops the walk toward a booking that cannot happen.

**Scoped by CONFIG, not by clinic id.** `minimum_age_years` is set for
vital_edge (18) and, from 2026-08-25, theorem (7). jv_v1's stated policy is the
opposite — "No minimum age — discounts available for under 18" — so it must
never have this key, and its absence is what keeps the gate off there.
"""
from __future__ import annotations

import pytest

from app.clinic_config import get_clinic
from app.media_streams.connection import capture_under_age
from app.prompts.clinic_template_prompt import build_clinic_prompt
from app.tools.receptionist_tools import (
    minimum_age_years,
    under_age_from_utterance,
)

VE, JV = "vital_edge", "jv_v1"


# ── the policy, read from config ───────────────────────────────────────────

def test_the_clinics_that_declare_a_minimum_age():
    """Vital Edge at 18, Theorem at 7 (added 2026-08-25, see
    test_theorem_minimum_age_has_one_value.py). jv_v1 and demo must NOT have
    the key: jv_v1's stated policy is the opposite — "No minimum age —
    discounts available for under 18" — so adding it there would start refusing
    the under-18 patients it explicitly serves at a discount."""
    assert minimum_age_years(get_clinic(VE)) == 18
    for cid in ("theorem", "theorem_v2", "theorem_v3"):
        assert minimum_age_years(get_clinic(cid)) == 7, (
            f"{cid} lost its minimum age. theorem_v2/_v3 are deepcopies made "
            "further down clinic_config, so a key added after those lines never "
            "reaches the live clinic."
        )
    for cid in (JV, "demo"):
        assert minimum_age_years(get_clinic(cid)) is None, (
            f"{cid} has acquired a minimum_age_years. jv_v1's policy is 'No "
            "minimum age' — adding this key there would start refusing its "
            "under-18 patients, who it explicitly serves at a discount."
        )


def test_the_machine_readable_age_agrees_with_the_prose():
    """The prose drives the prompt, the integer drives the engine. If they ever
    disagree, Susie says one thing and the gate does another."""
    prose = (get_clinic(VE)["pricing_and_policies"]["minimum_age"] or "").lower()
    assert "18" in prose and minimum_age_years(get_clinic(VE)) == 18


# ── detection ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("utterance,age", [
    ("my son is 15", 15),
    ("he's 15", 15),
    ("she's fifteen", 15),
    ("I'm 16 years old", 16),
    ("aged 14", 14),
    ("he is 17", 17),
    ("my daughter, she's 12", 12),
    ("I'm 17", 17),
])
def test_an_age_below_the_minimum_is_detected(utterance, age):
    assert under_age_from_utterance(get_clinic(VE), utterance) == age


@pytest.mark.parametrize("utterance", [
    "can i come at 15 past",        # a TIME
    "friday the 15th",             # a DATE
    "a 15 minute session",         # a DURATION
    "quarter past 2",
    "my number is 07502 211207",   # digits
    "90 minutes",
    "I'm 45",                      # an adult
    "he's 30",
    "I'm 18",                      # exactly the minimum — allowed
    "18",
    "I am free at 3",
    # A clock time and a person's age are the same two digits. Found by probing
    # the first version of this detector, which read every one of these as an
    # age and latched a refusal that cannot be cleared for the rest of the call.
    "my appointment is 12",
    "the time that works is 12",
    "is 3 okay",
    "the best time is 11",
    "it is 15 pounds",
    "whatever is 10 is fine",
    "",
    None,
])
def test_numbers_that_are_not_ages_do_not_arm_the_gate(utterance):
    """The dangerous direction. A false negative leaves the prompt's decline in
    force — which DID work on CA7d7c109b. A false positive refuses a legitimate
    adult and gives them no way to talk past it."""
    assert under_age_from_utterance(get_clinic(VE), utterance) is None


@pytest.mark.parametrize("cid", [JV, "theorem_v3", "demo"])
def test_a_clinic_with_no_age_policy_never_detects(cid):
    assert under_age_from_utterance(get_clinic(cid), "my son is 15") is None


# ── the latch ──────────────────────────────────────────────────────────────

def test_the_capture_arms_the_session():
    session = {"clinic_id": VE}
    assert capture_under_age(session, "my son is 15") == 15
    assert session["_under_age_declared"] == 15


def test_the_latch_is_never_cleared_by_a_later_utterance():
    """An age is a fact about the caller, not a preference. A later turn that
    happens to contain a number must not unlatch a safeguarding decision."""
    session = {"clinic_id": VE}
    capture_under_age(session, "he's 15")
    capture_under_age(session, "actually can we do 3 o'clock")
    assert session["_under_age_declared"] == 15


@pytest.mark.parametrize("cid", [JV, "theorem_v3", None])
def test_the_capture_does_nothing_for_a_clinic_without_the_policy(cid):
    session = {"clinic_id": cid}
    assert capture_under_age(session, "my son is 15") is None
    assert "_under_age_declared" not in session


def test_the_capture_never_raises():
    for session in ({}, {"clinic_id": None}, {"clinic_id": "no-such-clinic"}):
        assert capture_under_age(session, "he's 15") is None


def test_the_capture_is_wired_into_the_turn_loop():
    import inspect
    from app.media_streams import connection as conn
    assert "capture_under_age(self.session, utterance)" in inspect.getsource(conn), (
        "the capture is orphaned — nothing arms the gate on a real call"
    )


# ── the CALL STATE line: what actually stops the nudge ─────────────────────

def test_the_caller_state_tells_the_model_to_stop():
    _, dyn = build_clinic_prompt(
        {"clinic_id": VE, "_under_age_declared": 15}, get_clinic(VE)
    )
    assert "UNDER this clinic's minimum age" in dyn
    low = dyn.lower()
    for instruction in ("do not offer times", "do not ask for a day",
                        "do not suggest booking later"):
        assert instruction in low, (
            f"CALL STATE no longer says {instruction!r} — this is the half that "
            "fixes CA7d7c109b, where the decline was correct and the booking "
            "walk continued anyway"
        )


def test_no_line_before_an_age_is_stated():
    _, dyn = build_clinic_prompt({"clinic_id": VE}, get_clinic(VE))
    assert "UNDER this clinic" not in dyn


def test_a_clinic_without_the_policy_never_renders_the_line():
    """Belt and braces: only capture_under_age writes the flag and it is already
    clinic-gated, but a clinic serving under-18s must not be one stray session
    key away from refusing them."""
    _, dyn = build_clinic_prompt(
        {"clinic_id": JV, "_under_age_declared": 15}, get_clinic(JV)
    )
    assert "UNDER this clinic" not in dyn


# ── the write gate ─────────────────────────────────────────────────────────

def test_the_predicate_is_what_the_gate_asks():
    """Behavioural, not source-reading. The first version of this file asserted
    the gate only via inspect.getsource — and the control proved it: disabling
    the branch left all 39 tests green. Extraction is what makes it real."""
    from app.media_streams.llm_stream import under_age_blocks_booking as blocks
    assert blocks({"_under_age_declared": 15}) is True
    assert blocks({"_under_age_declared": 17}) is True
    assert blocks({}) is False
    assert blocks({"_under_age_declared": None}) is False


def test_book_appointment_is_refused_while_the_latch_is_set():
    """The backstop. It is not what the caller hears — no booking was even
    attempted on CA7d7c109b — but it is what makes the outcome impossible
    rather than merely discouraged."""
    import inspect
    from app.media_streams import llm_stream as ls
    src = inspect.getsource(ls.LLMStream._execute_tools)
    i = src.find("under_age_declined")
    assert i != -1, "the under-age write gate is gone from _execute_tools"
    j = src.find("under_age_blocks_booking(session)")
    assert j != -1, "the gate no longer asks the predicate"
    # Same branch, not merely nearby: nothing may open another arm between the
    # condition and the refusal it produces.
    assert j < i and "elif" not in src[j:i], (
        "the refusal has drifted out of the branch its condition guards"
    )
    assert 'tool_name == "book_appointment"' in src[max(0, j - 200):j]
    assert "do not book" in src[i:i + 900].lower()


def test_the_gate_runs_before_the_other_booking_checks():
    """First in the chain: there is no point validating a slot or a confirmation
    for an appointment the clinic will not accept."""
    import inspect
    from app.media_streams import llm_stream as ls
    src = inspect.getsource(ls.LLMStream._execute_tools)
    assert src.find("under_age_declined") < src.find("_slot_date_disagrees_with_speech"), (
        "the under-age gate no longer precedes the slot-date guard"
    )


# ── containment ────────────────────────────────────────────────────────────

def test_every_other_clinic_renders_byte_identical():
    from tests.regression.test_b55_provisional_reschedule_closing import (
        UNCHANGED_CLINIC_PROMPTS, _sha,
    )
    for clinic_id, expected in UNCHANGED_CLINIC_PROMPTS.items():
        assert _sha(clinic_id) == expected, (
            f"{clinic_id}'s prompt moved — the age gate has leaked out of "
            "Vital Edge"
        )
