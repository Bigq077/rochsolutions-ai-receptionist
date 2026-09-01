# tests/regression/test_b118_a_refused_lookup_still_caps_the_readout.py
"""
B-118 - CA14c0707a1fdd99f90439c69c90cc7e01, 28 Aug 2026, theorem_v3,
build f2cc28dc (B-116 and B-117 ALREADY LIVE and both working).

    13:00:27  slot buf: 3 spoken option(s) recorded — 12:00, 13:00, 14:00
              slot buf: acknowledged the spent band (mornings) (B-117)
              -> "I've given you all the mornings I have that day, I'm afraid.
                  Number 1, midday. Number 2, one in the afternoon..."

Correct. Then:

    13:00:46  check_availability BLOCKED — slots already retrieved this turn
    13:00:48  "Tuesday 8th September — nine in the morning. Ten in the morning.
               Midday. One in the afternoon. Two in the afternoon. Three in the
               afternoon. Four in the afternoon."

All seven, mornings included, twenty seconds after saying the mornings were
gone. outcome=abandoned, judge score 2.

THE DOOR. Three refusal payloads carried diary data as raw available_days plus
an English instruction -- "Read out AT MOST 3 times, soonest first". A prose cap
is not a cap. And after B-116 that instruction was actively WRONG: the soonest
times on a spent-band day are precisely the ones already heard, so obeying it
would also have contradicted B-117.

THE FIX is not a fourth rule. A refusal now carries `first_day` built by
`_cap_presented_slots` -- the same selector a real lookup uses, which since
B-116 prefers times the caller has not heard.
"""
from __future__ import annotations

import inspect

import app.media_streams.llm_stream as ls
from app.tools.slot_followup import record_spoken_slots

DAY = "2026-09-08"
ALL_SEVEN = ["09:00", "10:00", "12:00", "13:00", "14:00", "15:00", "16:00"]
HEARD = ["09:00", "10:00"]


def _slots(times):
    return [{"start": f"{DAY}T{t}:00+01:00", "end": f"{DAY}T{t}:59+01:00"} for t in times]


def _day(times):
    return {
        "date": DAY,
        "day_label": "Tuesday 8th September",
        "slot_times": list(times),
        "slot_times_spoken": [f"spoken-{t}" for t in times],
        "slots": _slots(times),
        "times_found_on_day": len(ALL_SEVEN),
        "times_not_shown": len(ALL_SEVEN) - len(times),
    }


def _session_mid_call():
    """The live state: the banded pair heard, then B-98 opened the day."""
    s = {"available_days": [_day(HEARD)]}
    record_spoken_slots(s, _day(HEARD)["slots"])
    s["available_days"] = [_day(ALL_SEVEN)]
    return s


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------
def test_a_refusal_hands_back_a_capped_day_not_the_whole_diary():
    s = _session_mid_call()
    out = ls._presentation_for_refusal(s, s["available_days"])
    assert "first_day" in out, "a refusal with no first_day is the B-118 door"
    # The B-118 property is that a refusal is CAPPED rather than handing back
    # the whole diary; which three it names is the 1 Sept spread rule.
    assert len(out["first_day"]["slot_times"]) == 3, "a refusal must be capped"
    assert out["first_day"]["slot_times"] == ["12:00", "13:00", "15:00"]


def test_the_refusal_does_not_lead_with_times_already_heard():
    s = _session_mid_call()
    out = ls._presentation_for_refusal(s, s["available_days"])
    for t in HEARD:
        assert t not in out["first_day"]["slot_times"]


def test_all_three_refusals_that_carry_diary_data_use_the_helper():
    """which_day, slot_offer_still_live and already_retrieved. Fixing one and
    leaving two is how this family has regrown every time."""
    src = inspect.getsource(ls)
    assert src.count("_presentation_for_refusal(") >= 4      # 3 uses + the def
    assert '"available_days": session.get("available_days", {}),' not in src


def test_the_prose_cap_is_gone():
    """It was never enforceable, and after B-116 "soonest first" also asks for
    exactly the times the caller has already heard.

    Checked against the strings the MODEL is actually shown, not the module
    text -- the docstrings above quote the old wording on purpose, and a plain
    substring scan would pass or fail on that instead.
    """
    import ast

    tree = ast.parse(inspect.getsource(ls))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)
    live = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docstrings]
    blob = " ".join(live)
    assert "AT MOST 3 times" not in blob
    assert "soonest first" not in blob
    assert "Use the data in available_days" not in blob


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------
def test_available_days_stays_whole():
    """_resolve_slot_iso, DTMF and the unspoken follow-up all read every
    bookable time. Trimming this would be B-97 coming back."""
    s = _session_mid_call()
    out = ls._presentation_for_refusal(s, s["available_days"])
    assert out["available_days"][0]["slot_times"] == ALL_SEVEN


def test_the_availability_arming_check_still_sees_a_payload():
    """_note_availability_seen arms on result['available_days']; a refusal that
    stopped carrying it would silently change that."""
    s = _session_mid_call()
    out = ls._presentation_for_refusal(s, s["available_days"])
    assert out.get("available_days")


def test_a_first_time_caller_gets_a_capped_spread_readout():
    """DELIBERATE REVERSAL -- owner decision, 1 Sept 2026. Was
    `test_a_first_time_caller_is_unaffected`, asserting `ALL_SEVEN[:3]`.

    A refusal is built by the same selector as a real lookup -- which is the
    whole point of B-118 -- so it inherits the spread rule along with it. The
    B-118 property, capped rather than the whole diary, is asserted on its own
    line above the literal.
    """
    s = {"available_days": [_day(ALL_SEVEN)]}
    out = ls._presentation_for_refusal(s, s["available_days"])
    assert len(out["first_day"]["slot_times"]) == 3, "a refusal must be capped"
    assert out["first_day"]["slot_times"] == ["09:00", "10:00", "16:00"]


def test_it_never_raises():
    """A refusal must still refuse if the presentation helper cannot run."""
    for junk in (None, {}, [], "nonsense", [{"no": "slots"}]):
        ls._presentation_for_refusal({}, junk)
