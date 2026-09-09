# tests/regression/test_stage_a_acuity_multiday_is_deterministic.py
"""
Stage A — Theorem's MULTI-day slot readout never reached the deterministic
builder, so the model wrote the sentence and nothing recorded what was offered.

Theorem `CAba2e3d8eb282bb`, 9 Sep 2026 11:05, and `CA297173b07243` at 10:04 —
two different builds, same line:

    [ms_gate5] NO deterministic offer built — branch=none-matched
               mode='multi_day' has_first_day=False has_presented_days=False

`_check_availability_acuity` sets `first_day` (so SINGLE-day readouts on Theorem
DO get the deterministic builder — `deterministic single_day offer built` is in
the same logs) but never sets `presented_days`, which is what the multi_day gate
requires. Line 3599's own comment states the contract it was left on:

    # The <=2-times-per-day cap is enforced by the slot formatter
    # (SLOT_FORMATTER_SYSTEM_PROMPT, multi_day).

i.e. the model is trusted to obey a prompt, and a repair layer reverse-parses
what it said. That is the architecture `slot_offer.py` was written to replace —
its docstring says it "cannot converge" and that fifteen B-numbers live there.
Theorem's single_day path was migrated; its multi_day path never was.

WHAT IT COST. With no deterministic offer, `apply_offer_to_session` never runs,
so the offer record is never written — `slot buf: could not resolve spoken
option(s) … offer record left unchanged`. The read-back guard consults that
record. On CAba2e3d8eb282bb the caller said "6 in the evening works", the model
read back six CORRECTLY, and the guard "corrected" it to "one in the afternoon"
against a stale projection. The caller hung up.

THE ONE THING THIS MUST NOT DO. The two readers decide `presentation_mode` by
different rules, and the difference is deliberate on both sides:

  * Acuity decides from the CALLER'S REQUEST — "as soon as possible" or a named
    day gives single_day, anything else multi_day.
  * `_cap_presented_slots` decides from the DATA — one day survived means
    single_day.

They disagree whenever a caller asks for the soonest and several days have
slots. Acuity's rule is arguably the better one and it is not this stage's job
to change it, so the mode is passed IN rather than re-derived, and the other
three readers keep deriving it exactly as before.
"""
from __future__ import annotations

import pytest

from app.tools.receptionist_tools import _cap_presented_slots


def _day(date, label, times):
    return {
        "date": date, "day_label": label,
        "slot_times": list(times),
        "slot_times_spoken": [f"t{t}" for t in times],
        "slots": [{"start": f"{date}T{t}:00"} for t in times],
    }


THREE_DAYS = [
    _day("2026-09-14", "Monday 14th September",    ["09:00", "15:00", "16:00", "17:00", "18:00"]),
    _day("2026-09-15", "Tuesday 15th September",   ["09:00", "15:00", "16:00", "17:00", "18:00"]),
    _day("2026-09-16", "Wednesday 16th September", ["13:00", "18:00"]),
]


def _payload(days):
    return {"available_days": list(days), "total_days": len(days)}


# ── the defect ──────────────────────────────────────────────────────────────

def test_a_forced_multi_day_emits_presented_days():
    """The gate in llm_stream requires `presented_days` for multi_day. Without
    it the branch is `none-matched` and the model's sentence stands."""
    out = _cap_presented_slots(_payload(THREE_DAYS), {"clinic_id": "theorem_v3"},
                               mode="multi_day")
    assert out["presentation_mode"] == "multi_day"
    assert "presented_days" in out, "the multi_day gate cannot fire without this"
    assert len(out["presented_days"]) == 3


def test_the_per_day_time_cap_is_applied_in_code_not_by_the_prompt():
    """Two times per day, chosen by `_presented_indices` (B-116, unheard
    first) — not left to SLOT_FORMATTER_SYSTEM_PROMPT."""
    out = _cap_presented_slots(_payload(THREE_DAYS), {"clinic_id": "theorem_v3"},
                               mode="multi_day")
    for day in out["presented_days"]:
        assert len(day["slot_times"]) <= 2, day
        # the parallel arrays must stay aligned or a spoken label names a slot
        # the caller cannot book
        assert len(day["slot_times"]) == len(day["slot_times_spoken"]) == len(day["slots"])


# ── the thing it must not break ─────────────────────────────────────────────

def test_the_mode_is_honoured_not_re_derived():
    """Acuity decides mode from the caller's REQUEST. A caller who asked for
    the soonest gets single_day even though three days have slots — and
    `_cap_presented_slots` on its own would say multi_day."""
    out = _cap_presented_slots(_payload(THREE_DAYS), {"clinic_id": "theorem_v3"},
                               mode="single_day")
    assert out["presentation_mode"] == "single_day"
    assert "first_day" in out
    assert "presented_days" not in out


def test_one_surviving_day_forced_to_multi_day_still_works():
    """The mirror: the data says single_day, the caller's request said
    multi_day. Must not crash and must honour the caller."""
    out = _cap_presented_slots(_payload(THREE_DAYS[:1]), {"clinic_id": "theorem_v3"},
                               mode="multi_day")
    assert out["presentation_mode"] == "multi_day"
    assert len(out["presented_days"]) == 1


@pytest.mark.parametrize("clinic_id", ["northgate", "jv_v1", "vital_edge"])
def test_the_other_three_readers_are_untouched(clinic_id):
    """No `mode` passed means derive from the data, exactly as before. This is
    the containment claim: Stage A changes Theorem and nothing else."""
    session = {"clinic_id": clinic_id}
    multi = _cap_presented_slots(_payload(THREE_DAYS), session)
    single = _cap_presented_slots(_payload(THREE_DAYS[:1]), session)
    assert multi["presentation_mode"] == "multi_day"
    assert "presented_days" in multi and "first_day" not in multi
    assert single["presentation_mode"] == "single_day"
    assert "first_day" in single and "presented_days" not in single


def test_a_junk_mode_falls_back_to_the_data_rule():
    """A readout must not fail because the hint is odd."""
    for bad in ("", "MULTI", "day", 7, object()):
        out = _cap_presented_slots(_payload(THREE_DAYS), {"clinic_id": "x"}, mode=bad)
        assert out["presentation_mode"] == "multi_day"
        assert "presented_days" in out


def test_the_bookable_set_is_never_trimmed():
    """Unchanged invariant: only the SPOKEN list is capped."""
    out = _cap_presented_slots(_payload(THREE_DAYS), {"clinic_id": "theorem_v3"},
                               mode="multi_day")
    assert len(out["available_days"]) == 3
    for day in out["available_days"]:
        assert len(day["slot_times"]) == len(
            next(d for d in THREE_DAYS if d["date"] == day["date"])["slot_times"])


# ── and the dispatcher actually calls it ────────────────────────────────────

def test_the_acuity_branch_is_wired_to_the_shared_capper():
    """Asserted on the source: reaching this any other way means standing up
    the Acuity API, and the property is that ONE call sits between the Acuity
    result and the return."""
    import inspect
    from app.tools import receptionist_tools as rt

    src = inspect.getsource(rt._exec_check_availability)
    i = src.index("_check_availability_acuity(args, session)")
    j = src.index("return _acuity_result", i)
    window = src[i:j]
    assert "_cap_presented_slots(" in window, (
        "the Acuity branch no longer routes through the shared capper — that "
        "is the whole of Stage A"
    )
    assert 'mode="multi_day"' in window, (
        "the mode must be handed in, never re-derived: Acuity decides it from "
        "the caller's request and _cap_presented_slots from the data"
    )


def test_single_day_is_deliberately_left_on_acuitys_own_path():
    """Recorded so it reads as a decision rather than an oversight. Acuity's
    `first_day` block carries B-108b, B-117 and band_spent_label, which
    `_cap_presented_slots` does not produce. Converging it is stage C."""
    import inspect
    from app.tools import receptionist_tools as rt

    src = inspect.getsource(rt._exec_check_availability)
    i = src.index("_check_availability_acuity(args, session)")
    j = src.index("return _acuity_result", i)
    assert 'presentation_mode") == "multi_day"' in src[i:j], (
        "the Stage A gate is no longer multi_day-only — single_day was left "
        "on Acuity's own path on purpose"
    )
