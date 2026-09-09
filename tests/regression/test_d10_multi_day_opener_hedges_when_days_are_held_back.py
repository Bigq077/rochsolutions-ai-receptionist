"""The multi-day opener may only claim the diary when it is reading all of it.

D10. `build_slot_offer` chooses between two openers:

    "I've got a few days —"             when days are held back
    "Here's what we've got coming up —" when they are not

and it decided which from `len(days) > len(days[:max_days])`. That test can only
be true of an UNTRIMMED list, and every live path hands it `presented_days` —
already capped to three by `_cap_presented_slots`. So the hedge was unreachable
and the confident sentence went out unconditionally.

Confirmed on the 9 Sep 2026 12:20 northgate call: 7 days found, 3 spoken, opener
"Here's what we've got coming up". That is the exact claim its own comment says
it must not make, and it is caller-facing on all four clinics.

WHY THE FIX IS NOT A NEW PAYLOAD FIELD. `days_not_shown` already exists and
already means this, but it is written by `_check_availability_acuity` alone and
`_cap_presented_slots` is forbidden to touch it — post-processing owning that
name would let the truncated view overwrite the honest count, which is what
`test_the_honesty_fields_are_not_clobbered_downstream` guards. The first cut of
this fix wrote it there and that test caught it. So the decision lives in one
pure function, `days_were_held_back`, which PREFERS the retrieval layer's field
and falls back to found-versus-spoken for the three readers that emit none —
the google_calendar fall-through (northgate, JV), `diary` (Vital Edge) and
`published`, which is to say the clinic this was observed on.
"""
from __future__ import annotations

import pytest

from app.tools.receptionist_tools import _cap_presented_slots
from app.tools.slot_offer import build_slot_offer, days_were_held_back

HEDGED = "I've got a few days —"
CONFIDENT = "Here's what we've got coming up —"

TIMES = [("08:00", "eight in the morning"),
         ("15:30", "half past three in the afternoon")]


def _day(iso: str, label: str):
    return {
        "date": iso,
        "day_label": label,
        "slot_times": [t for t, _ in TIMES],
        "slot_times_spoken": [s for _, s in TIMES],
        "slots": [{"start": "%sT%s:00+01:00" % (iso, t), "end": ""}
                  for t, _ in TIMES],
    }


def _days(n: int):
    return [_day("2026-09-%02d" % (9 + i), "Day %d" % (9 + i)) for i in range(n)]


def _opener(n_found: int) -> str:
    """The real live chain, exactly as llm_stream runs it."""
    result = _cap_presented_slots(
        {"available_days": _days(n_found), "success": True}, session={},
    )
    offer = build_slot_offer(
        list(result["presented_days"]), more_days=days_were_held_back(result),
    )
    return offer.chunks[0]


# ── The opener follows the diary ─────────────────────────────────────────────

def test_seven_days_found_three_spoken_hedges():
    """The 9 Sep 12:20 call, exactly."""
    opener = _opener(7)
    assert opener.startswith(HEDGED)
    assert CONFIDENT not in opener


@pytest.mark.parametrize("found", [4, 5, 7, 20])
def test_any_day_held_back_hedges(found):
    assert _opener(found).startswith(HEDGED)


def test_exactly_three_days_found_still_claims_the_diary():
    """The hedge must not become unconditional — that is a lie in the other
    direction, and it is why this needed a guard rather than a fixed string."""
    opener = _opener(3)
    assert opener.startswith(CONFIDENT)
    assert HEDGED not in opener


# ── The decision has one owner ───────────────────────────────────────────────

@pytest.mark.parametrize("found", [3, 4, 7, 20])
def test_the_shared_readers_are_covered_by_found_versus_spoken(found):
    """northgate, JV and Vital Edge emit no honesty fields at all, so the
    fallback arm is the ONLY thing that fixes them."""
    result = _cap_presented_slots(
        {"available_days": _days(found), "success": True}, session={},
    )
    assert "days_not_shown" not in result
    assert days_were_held_back(result) is (found > 3)


def test_the_retrieval_layers_own_count_wins_when_it_is_there():
    """Acuity's `days_not_shown` counts what the SWEEP found minus what will be
    SPOKEN, so it sees days a single_day presentation hides — cases where
    found-versus-spoken on the trimmed payload reads 0."""
    payload = {"available_days": _days(1), "presented_days": _days(1),
               "days_not_shown": 3}
    assert days_were_held_back(payload) is True
    payload["days_not_shown"] = 0
    assert days_were_held_back(payload) is False


def test_capping_still_must_not_write_the_honesty_field():
    """Belt and braces for the collision the first cut of this fix caused."""
    import inspect
    from app.tools import receptionist_tools as rt
    assert "days_not_shown" not in inspect.getsource(rt._cap_presented_slots)


@pytest.mark.parametrize("payload", [
    None, {}, "not a dict", {"days_not_shown": None},
    {"days_not_shown": "four"}, {"available_days": None, "presented_days": []},
    {"available_days": _days(7)},          # no presented_days at all
])
def test_an_unreadable_payload_keeps_todays_opener(payload):
    assert days_were_held_back(payload) is False


# ── The parameter itself ─────────────────────────────────────────────────────

def test_more_days_absent_still_lets_the_local_count_decide():
    """Callers that pass an UNTRIMMED list keep their existing behaviour — the
    parameter is an override, not a replacement."""
    assert build_slot_offer(_days(7)).chunks[0].startswith(HEDGED)
    assert build_slot_offer(_days(3)).chunks[0].startswith(CONFIDENT)


def test_more_days_overrides_a_pre_trimmed_list_in_both_directions():
    trimmed = _days(3)
    assert build_slot_offer(trimmed, more_days=True).chunks[0].startswith(HEDGED)
    assert build_slot_offer(trimmed, more_days=False).chunks[0].startswith(CONFIDENT)


# ── Stage B's opener outranks both, and makes no completeness claim ──────────

def test_the_soonest_first_opener_is_unaffected():
    """`soonest_first` says nothing about completeness, so it is safe whether or
    not days are held back — which is why D10 did not fire on the 13:43 call."""
    for more in (True, False):
        opener = build_slot_offer(
            _days(3), lead_in="soonest_first", more_days=more,
        ).chunks[0]
        assert opener.startswith("Starting with the soonest —")
        assert HEDGED not in opener and CONFIDENT not in opener
