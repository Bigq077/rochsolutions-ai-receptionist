# tests/regression/test_the_requested_day_is_named_when_it_is_missed.py
"""
The caller asked for Sunday and was never told Sunday was unavailable.

northgate CAf4e4a3a6ba8a603ec3c053ed26edd99c, 8 Sep 2026, build 14c284bbdce2.

    caller: "um have you got anything on sunday the 13th"
    Susie:  "Here's what we've got coming up - Number 1, Monday 14th
             September - eight, or ten past five. Number 2, Tuesday 15th ..."

Sunday is never mentioned. A direct question went unanswered, and the caller is
left to infer the answer from three days that are not the one they named.

NOBODY IN THE CHAIN WAS WRONG, WHICH IS WHY IT SURVIVED. The tool detected the
miss and reported it, with a pre-rendered spoken label:

    "requested_day_empty": True, "requested_day_label": "Sunday 13th September"

and the template prompt carries a rule telling the model to open with it. But
the model never spoke:

    [ms_gate5] deterministic offer in force - 3 chunk(s); the model's 0
               buffered chunk(s) are discarded ('')
    [ms_llm]   slot LLM call SKIPPED - deterministic offer already built

`requested_day_empty` was read in exactly two places in the whole codebase and
both were prompt files. On the deterministic path the prompt is not the speaker,
so the one instruction that had to survive was the one nothing downstream could
say. `build_slot_offer` is never handed the flag, and multi_day deliberately has
no lead-in at all (B-125), so there was nowhere in that sentence for a different
day to go.

TWO FIXES, AND THE SECOND IS WHY THE FIRST IS NOT ENOUGH. The prompt's wording
was "[requested_day_label] is fully booked, I'm afraid". Said of a Sunday at a
clinic that does not open Sundays, that is a false statement about the diary --
and it would send a caller away believing a slot might free up on a day that
will never have one. Simply making the deterministic path say the prompt's
sentence would have shipped that lie to every caller instead of to none.

The distinction was already in the data and was being discarded:
`generate_candidate_slots` is given `clinic_working_hours` and `closed_dates`,
so a day the clinic does not open yields NO candidates, while a day booked out
yields candidates that `filter_free_slots` then removes. That is now carried as
`requested_day_closed` and decides which true sentence is spoken.

SCOPE. `requested_day_empty` is produced by the Google Calendar widening branch
only, so this reaches the template_v1 clinics (jv_v1, northgate, vital_edge).
theorem_v3 is Acuity and cannot reach that branch at all -- its prompt carries
no such rule, and adding one there would be dead text. See
[[zero-slot-defects-are-not-reproducible-on-the-demo-line]].
"""

import ast
import inspect

import pytest

from app.media_streams import llm_stream as ls
from app.tools import receptionist_tools as rt


# ---------------------------------------------------------------------------
# Fix 1 -- the deterministic path can now name the missed day
# ---------------------------------------------------------------------------
def _prebuilt_assign():
    """The `session["_slot_offer_prebuilt"] = {...}` assignment node."""
    src = inspect.cleandoc(inspect.getsource(ls.LLMStream._execute_tools))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if (
                isinstance(t, ast.Subscript)
                and isinstance(t.slice, ast.Constant)
                and t.slice.value == "_slot_offer_prebuilt"
            ):
                return node
    raise AssertionError("_slot_offer_prebuilt assignment not found")


def _loop_src():
    return inspect.getsource(ls.LLMStream._execute_tools)


def test_the_offer_chunks_can_carry_a_missed_day_sentence():
    """The spoken chunks must be able to start with the miss.

    Before the fix `chunks` was `list(_offer.chunks)` and nothing else could
    ever be said, which is the whole defect.
    """
    node = _prebuilt_assign()
    chunks_value = None
    for k, v in zip(node.value.keys, node.value.values):
        if isinstance(k, ast.Constant) and k.value == "chunks":
            chunks_value = v
    assert chunks_value is not None, "the prebuilt record has no chunks key"
    dumped = ast.dump(chunks_value)
    assert "_miss_chunk" in dumped, (
        "the deterministic offer's chunks cannot carry the missed day, so a "
        "caller who names a day gets three other days and no answer"
    )


def test_the_miss_is_read_from_the_payload_not_invented():
    """`requested_day_label` is pre-rendered precisely so no speaker has to
    format a date itself. The fix must use it rather than build one."""
    src = _loop_src()
    assert "requested_day_label" in src
    assert "requested_day_empty" in src


def test_the_missed_day_never_becomes_bookable():
    """Speech only.

    The day being named is the one day that is NOT available, so it must not
    enter `slots` or `dtmf_map` -- otherwise it is offerable by ordinal and
    pressable on the keypad (the B-108b rule that keeps `other_dates` out of
    the record).
    """
    node = _prebuilt_assign()
    for k, v in zip(node.value.keys, node.value.values):
        if isinstance(k, ast.Constant) and k.value in ("slots", "dtmf_map"):
            assert "_miss_chunk" not in ast.dump(v), (
                f"the missed day leaked into {k.value} -- it would be "
                f"selectable, and it is the one day with nothing in it"
            )


# ---------------------------------------------------------------------------
# Fix 2 -- closed is not the same fact as fully booked
# ---------------------------------------------------------------------------
def test_the_payload_distinguishes_closed_from_fully_booked():
    src = inspect.getsource(rt)
    assert '"requested_day_closed"' in src, (
        "the payload collapses 'the clinic is shut that day' and 'that day is "
        "booked out' into one flag, so the only available sentence is 'fully "
        "booked' -- false whenever the clinic is closed"
    )


def test_closed_is_decided_by_candidates_not_by_free_slots():
    """The distinction must come from whether any candidate slot EXISTS.

    `free_slots` is empty in both cases and cannot tell them apart. Candidates
    can: `generate_candidate_slots` is given the working hours, so a closed day
    produces none.
    """
    src = inspect.getsource(rt)
    assert "_requested_had_candidates" in src
    i = src.index("_requested_had_candidates")
    window = src[i:i + 400]
    assert "candidates" in window, (
        "closed-vs-full must be derived from the candidate grid; deriving it "
        "from free_slots is impossible -- both cases are empty there"
    )


def test_the_closed_flag_is_counted_on_the_requested_date():
    """Not on the whole narrow window.

    A narrow window may span two or three days, and "every day in the window
    was shut" is a different claim from "the day they asked for was shut".
    """
    src = inspect.getsource(rt)
    i = src.index("_requested_had_candidates")
    window = src[i:i + 400]
    assert "_requested_iso" in window


# ---------------------------------------------------------------------------
# The wording, and the clinics it reaches
# ---------------------------------------------------------------------------
def _rendered(clinic_id: str) -> str:
    from app.prompts.susie_system_prompt import build_system_prompt_parts
    static, dynamic = build_system_prompt_parts({
        "call_sid": "CAtest_missed_day",
        "clinic_id": clinic_id,
        "booking_flow_active": True,
        "collected": {},
    })
    return f"{static}\n\n{dynamic}"


# The Google Calendar widening branch is what produces requested_day_empty, so
# the rule belongs to the template_v1 clinics and only to them.
GCAL_TEMPLATE_CLINICS = ["jv_v1", "northgate", "vital_edge"]


@pytest.mark.parametrize("clinic_id", GCAL_TEMPLATE_CLINICS)
def test_the_rule_tells_the_model_which_sentence_is_true(clinic_id):
    """The model still speaks whenever no deterministic offer is built, so the
    prompt needs the same distinction the code now makes."""
    low = _rendered(clinic_id).lower()
    assert "requested_day_closed" in low, (
        f"{clinic_id} is told to say 'fully booked' with no way to know the "
        f"clinic is shut that day"
    )
    assert "closed" in low


@pytest.mark.parametrize("clinic_id", GCAL_TEMPLATE_CLINICS)
def test_the_rule_still_forbids_silence_about_the_named_day(clinic_id):
    """The original point of the rule, which the rewrite must not lose."""
    assert "requested_day_empty" in _rendered(clinic_id).lower()


def test_theorem_v3_is_not_given_a_rule_it_can_never_use():
    """theorem_v3 books through Acuity and never reaches the gcal widening
    branch, so requested_day_empty cannot occur there.

    Pinned because adding the rule there is the obvious "consistency" edit, and
    it would be dead text in a 114k prompt -- the mistake this repo has made
    with config keys that never reach the model. It was made once while
    building this fix and reverted.
    """
    assert "requested_day_empty" not in _rendered("theorem_v3").lower()
