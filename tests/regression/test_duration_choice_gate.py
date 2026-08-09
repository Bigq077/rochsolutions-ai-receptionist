"""check_availability must not build a slot grid at a length nobody chose.

`CAa1e98c447774ec15340a6b84cc89cff0` (9 Aug 2026, Vital Edge, live):

    16:08:38  'deep_tissue_massage' offers [60, 90] and NO choice was
              captured — defaulting to 60
    16:08:38  check_availability … duration=60m
    16:08:40  "Number 1, Friday 14th August … Number 2, Saturday 15th"
    16:08:46  caller picks Friday 10am
    16:08:51  "would you like a 60-minute session … or a 90-minute…?"
    16:09:01  session length captured: 90 minutes
    16:09:06  check_availability … duration=90m   (the whole lookup again)

The caller picked a slot off a grid built at a length nobody had agreed, was
then asked the qualifying question, and had it recomputed underneath them. The
call ran 3m02s and booked nothing.

The instruction was not missing — vital_edge/clinic.json says "ask which length
they'd like … BEFORE offering any appointment times". `_resolve_duration_minutes`
deliberately does not block ("re-asking from inside a resolver is not this
function's job — but make it loud"). It was loud. Loud is not a gate.

Two halves are pinned here, and the SECOND is the one that matters more:

  1. the gate blocks, once, with a question naming both lengths and both
     prices;
  2. it never blocks twice. A caller who answers "the longer one" — words the
     capturer cannot parse — must still be able to book. A gate that re-asked
     every time would trade a wrong-grid defect for an unbookable call, which
     is strictly worse and is the worst failure this system has.
"""

import pytest

from app.clinic_config import get_clinic
from app.media_streams.config import FORCE_TEXT_NEXT_ITERATION
from app.tools.receptionist_tools import duration_choice_gate


@pytest.fixture
def ve():
    return get_clinic("vital_edge") or {}


@pytest.fixture
def jv():
    return get_clinic("jv_v1") or {}


# ── 1. it blocks, and the block is usable ───────────────────────────────────

def test_blocks_an_options_service_with_no_choice(ve):
    s = {}
    out = duration_choice_gate(ve, "deep_tissue_massage", s)
    assert out is not None
    assert out["error"] == "duration_choice_required"


def test_the_block_names_both_lengths_and_both_prices(ve):
    """Read from the service's own `<n>min_in_clinic_gbp` keys — the same
    source the prompt renders from — so the question cannot quote a figure
    Susie would not."""
    out = duration_choice_gate(ve, "deep_tissue_massage", {})
    msg = out["message"]
    assert "60-minute at £125" in msg
    assert "90-minute at £180" in msg


def test_the_block_forbids_retrying_the_tool(ve):
    """The turn that produced this defect ended with the model calling the
    blocked tool again. See FORCE_TEXT_NEXT_ITERATION."""
    out = duration_choice_gate(ve, "deep_tissue_massage", {})
    assert "Do NOT call check_availability yet" in out["message"]
    assert "Say that and stop" in out["message"]


def test_the_block_arms_the_force_text_flag(ve):
    """Wording alone loses: a tool result carrying "error" reads as a FAILED
    call, and retrying a failed call is correct default behaviour."""
    s = {}
    duration_choice_gate(ve, "deep_tissue_massage", s)
    assert s.get(FORCE_TEXT_NEXT_ITERATION) is True


def test_the_caller_is_not_told_about_the_correction(ve):
    out = duration_choice_gate(ve, "deep_tissue_massage", {})
    assert "Do NOT mention this to the caller" in out["message"]


def test_it_is_not_hardcoded_to_60_90(jv):
    """jv_v1's sports massage is 30/60, not 60/90. A gate that only knew Vital
    Edge's pair would silently pass every jv_v1 booking."""
    out = duration_choice_gate(jv, "sports_massage", {})
    assert out is not None
    assert "30-minute at £" in out["message"]
    assert "60-minute at £" in out["message"]


# ── 2. it must never block twice ────────────────────────────────────────────

def test_it_fires_once_and_then_lets_the_lookup_through(ve):
    """THE safety property. The caller answers "the longer one"; the capturer
    returns None because it cannot parse that; the model calls the tool again.
    If the gate blocked a second time the call could never book."""
    s = {}
    first = duration_choice_gate(ve, "deep_tissue_massage", s)
    assert first is not None, "the first call should block"

    second = duration_choice_gate(ve, "deep_tissue_massage", s)
    assert second is None, (
        "the gate blocked twice — a caller whose answer does not parse can "
        "never reach a slot list, which is worse than the defect being fixed"
    )


def test_the_latch_survives_a_different_service(ve):
    """Once per CALL, not once per service. Two options-services and a caller
    who switches between them must not buy a second interrogation."""
    s = {}
    duration_choice_gate(ve, "deep_tissue_massage", s)
    assert duration_choice_gate(ve, "sports_massage", s) is None


def test_the_latch_is_recorded_on_the_session(ve):
    s = {}
    duration_choice_gate(ve, "deep_tissue_massage", s)
    assert s.get("_duration_gate_fired") is True


# ── 3. everything it must leave alone ───────────────────────────────────────

def test_a_captured_choice_passes_straight_through(ve):
    s = {"_service_duration_choice": 90}
    assert duration_choice_gate(ve, "deep_tissue_massage", s) is None


def test_a_captured_choice_does_not_burn_the_latch(ve):
    """A caller who says "90 minutes please" up front should still get the
    gate later if they switch to a service whose length they have not named."""
    s = {"_service_duration_choice": 90}
    duration_choice_gate(ve, "deep_tissue_massage", s)
    assert "_duration_gate_fired" not in s


def test_a_choice_that_is_not_a_valid_option_does_not_count(ve):
    """45 is not one of [60, 90]. Treating any truthy value as a choice would
    let a stray capture disarm the gate."""
    s = {"_service_duration_choice": 45}
    assert duration_choice_gate(ve, "deep_tissue_massage", s) is not None


def test_a_fixed_length_service_is_untouched(ve):
    """Vital Edge's neck/back/shoulders is a fixed 30 minutes. There is
    nothing to ask, and asking would be a defect of its own."""
    assert duration_choice_gate(ve, "neck_back_shoulders_massage", {}) is None


@pytest.mark.parametrize("svc", ["msk_initial_assessment", "acupuncture",
                                 "neuro_followup"])
def test_jv_single_length_services_are_untouched(jv, svc):
    assert duration_choice_gate(jv, svc, {}) is None


def test_an_unknown_service_is_not_blocked(ve):
    """Other gates own invalid service names; this one must not add a second
    failure mode on top of them."""
    assert duration_choice_gate(ve, "not_a_real_service", {}) is None


def test_an_empty_service_is_not_blocked(ve):
    assert duration_choice_gate(ve, "", {}) is None


def test_a_clinic_whose_services_are_strings_is_not_blocked():
    """theorem's `services` is a list of plain STRINGS, not dicts.

    This test failed on first run with AttributeError: 'str' object has no
    attribute 'get', raised inside _find_service_def — which had been latently
    broken for string-service clinics the whole time, and unreachable only
    because every existing caller ran on template clinics. Adding this gate
    made the theorem path reachable, so it would have crashed Mark's first
    availability lookup of every call. _find_service_def now skips
    non-dict entries; this pins that it stays skipped.
    """
    th = get_clinic("theorem") or {}
    assert duration_choice_gate(th, "physiotherapy assessment", {}) is None


def test_find_service_def_survives_string_services():
    """Pinned directly, not only through the gate — the bug is in the resolver
    and every future caller inherits the fix."""
    from app.tools.receptionist_tools import _find_service_def

    mixed = {"services": ["A plain string service", {"service_id": "real_one"}]}
    assert _find_service_def(mixed, "real_one") == {"service_id": "real_one"}
    assert _find_service_def(mixed, "A plain string service") is None


def test_an_empty_clinic_is_not_blocked():
    assert duration_choice_gate({}, "deep_tissue_massage", {}) is None


# ── 4. the gate is actually wired into the tool ─────────────────────────────

def test_check_availability_calls_the_gate():
    """A pure predicate nothing calls is a pure predicate that guards nothing.
    Pinned against the source because the executor's early-return path cannot
    be reached without a live calendar."""
    import inspect
    from app.tools.receptionist_tools import _exec_check_availability

    src = inspect.getsource(_exec_check_availability)
    assert "duration_choice_gate(" in src
    assert "_dur_block" in src


def test_the_gate_runs_after_the_location_gate():
    """Both block on a question the caller must answer, and the booking flow
    asks which clinic before it asks how long. Reversed, a caller who had
    confirmed neither would be asked in the wrong order."""
    import inspect
    from app.tools.receptionist_tools import _exec_check_availability

    src = inspect.getsource(_exec_check_availability)
    assert src.index("location_required") < src.index("duration_choice_gate(")
