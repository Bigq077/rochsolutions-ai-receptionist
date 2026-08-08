"""
B-60 — a 90-minute booking went into the diary as 60 minutes.

`CAbc6e6a96` (8 Aug 2026, Vital Edge, live). Reconstructed from the call log:

    18:39:10  session length captured: 90 minutes
              (from "...deep tissue to be fair uh 90 minutes like the full package")
    18:39:32  tool: check_availability args={... no duration_minutes ...}
              [availability] diary: ... duration=90m          <- capture held 90
    18:40:31  tool: book_appointment  args={... "duration_minutes" ...}
    18:40:31  Calendar event created: start 09:00 -> end 10:00 <- 60 minutes

`args.get("duration_minutes")` short-circuited the fallback, so the model's
argument silently outranked the caller's own words. The client had agreed 90
minutes at the 90-minute price; the practitioner's diary showed 60, and the
10:00 that follows stayed offerable to the next caller.

Second occurrence of this exact loss. `CA86c320ef` (4 Aug) is why
`duration_choice_from_utterance` exists, and its docstring already stated the
rule: the choice is captured in the engine, "not inferred from a tool argument
the model may or may not carry". Four call sites trusted the argument anyway.

Pinned here: the caller's captured choice wins, the model's argument can no
longer overwrite it, the slot grid and the written event agree, and single-length
clinics are untouched.
"""

import pytest

from app.tools.receptionist_tools import _resolve_duration_minutes

# Vital Edge's deep tissue: a 60/90 choice, no single typical duration.
_VE = {
    "clinic_id": "vital_edge",
    "slot_minutes": 60,
    "services": [
        {"service_id": "deep_tissue_massage", "name": "Deep Tissue Massage",
         "typical_duration_minutes_options": [60, 90]},
        {"service_id": "neck_back_shoulders_massage",
         "name": "Neck, Back and Shoulders Massage",
         "typical_duration_minutes": 30},
    ],
}

# A single-length clinic — must behave exactly as before.
_SINGLE = {
    "clinic_id": "jv_v1",
    "slot_minutes": 40,
    "services": [{"service_id": "physio", "name": "Physiotherapy",
                  "typical_duration_minutes": 40}],
}


def test_the_live_defect_the_caller_said_ninety_the_model_said_sixty():
    session = {"_service_duration_choice": 90}
    got = _resolve_duration_minutes(
        _VE, "deep_tissue_massage", {"duration_minutes": 60}, session, 60
    )
    assert got == 90, "the model's argument still outranks the caller's own words"


def test_the_model_argument_cannot_corrupt_the_captured_choice():
    # It used to overwrite it, so one wrong argument poisoned every later turn
    # in the call — including an availability re-check.
    session = {"_service_duration_choice": 90}
    _resolve_duration_minutes(
        _VE, "deep_tissue_massage", {"duration_minutes": 60}, session, 60
    )
    assert session["_service_duration_choice"] == 90


def test_the_grid_and_the_event_agree():
    # The whole point: availability is gridded at one length and the event is
    # written at another. Same inputs as the live call, both call sites.
    session = {"_service_duration_choice": 90}
    grid = _resolve_duration_minutes(_VE, "deep_tissue_massage", {}, session, 60)
    event = _resolve_duration_minutes(
        _VE, "deep_tissue_massage", {"duration_minutes": 60}, session, 60
    )
    assert grid == event == 90


def test_the_argument_is_used_when_the_caller_chose_nothing():
    session = {}
    got = _resolve_duration_minutes(
        _VE, "deep_tissue_massage", {"duration_minutes": 90}, session, 60
    )
    assert got == 90
    # ...and it latches, so the rest of the call stays consistent with it.
    assert session["_service_duration_choice"] == 90


def test_a_captured_choice_that_is_not_an_option_does_not_bind():
    # Capture is service-agnostic (the service is only known at tool time), so a
    # length captured for one service must not be forced onto another.
    session = {"_service_duration_choice": 90}
    got = _resolve_duration_minutes(
        _VE, "neck_back_shoulders_massage", {"duration_minutes": 30}, session, 60
    )
    assert got == 30


def test_no_argument_and_no_capture_falls_back_to_the_service():
    assert _resolve_duration_minutes(
        _VE, "neck_back_shoulders_massage", {}, {}, 60) == 30


def test_options_service_with_no_capture_keeps_the_shortest_default():
    # Unchanged behaviour — loud, but unchanged.
    assert _resolve_duration_minutes(_VE, "deep_tissue_massage", {}, {}, 60) == 60


@pytest.mark.parametrize("args,expected", [({}, 40), ({"duration_minutes": 60}, 60)])
def test_single_length_clinics_are_untouched(args, expected):
    # The caller's capture can only bind on an options-service, so a physio
    # clinic behaves exactly as it did before this fix.
    session = {"_service_duration_choice": 90}
    assert _resolve_duration_minutes(_SINGLE, "physio", args, session, 40) == expected


def test_an_unknown_service_falls_back_to_the_clinic_slot_length():
    assert _resolve_duration_minutes(_VE, "reiki", {}, {}, 60) == 60
