"""
The appointment decides the clinic on cancel/reschedule (T-18 follow-up).

The prompt's STRICT RULE has always said location for a cancel or reschedule
comes from the lookup result's `appointment_type`, never from the session, and
that the caller's clinic preference is irrelevant. Nothing enforced it.

That was survivable while the flow asked "Awlstuh or Redditch?" — the answer
happened to set `selected_location`. Once the flow stopped asking (a1c4593,
correctly: the answer was discarded anyway), two facts collided.

  - `selected_location` DEFAULTS to "alcester" in DEFAULT_MS_SESSION. It is not
    a confirmed choice, but every downstream tool reads
    `args.get("location") or session["selected_location"]`, and
    check_availability's "location must never be guessed" gate reads the
    SESSION ONLY — so the default silently satisfies that gate on every call.
  - So the only thing standing between a Redditch appointment and Alcester
    slots was the model remembering to pass `location=` itself.

Forget it once and the patient is moved to the wrong town, and nothing in the
call sounds wrong. Same silent class as B-36.
"""

import inspect

import pytest

from app.media_streams.session import DEFAULT_MS_SESSION
from app.tools.receptionist_tools import (
    location_from_appointment_type,
    _exec_lookup_patient,
)


def test_the_default_that_makes_this_necessary_still_exists():
    """If the default ever goes, re-read this file before trusting it.

    The whole exposure rests on `selected_location` arriving pre-populated with
    a clinic nobody chose.
    """
    assert DEFAULT_MS_SESSION.get("selected_location") == "alcester", (
        "DEFAULT_MS_SESSION no longer pre-sets selected_location — the silent "
        "wrong-clinic path this guards may have changed shape"
    )


@pytest.mark.parametrize("appt_type,expected", [
    # The exact string Acuity returned on the 01:41 call, trailing stop included
    ("Theorem Clinics Alcester.", "alcester"),
    ("Theorem Clinics Redditch.", "redditch"),
    ("Theorem Clinics Redditch", "redditch"),
    ("theorem clinics redditch", "redditch"),
])
def test_the_clinic_is_read_off_the_appointment_type(appt_type, expected):
    """Note the inputs: these are ACUITY appointment types, written by the
    clinic, which spell the town "Alcester". "Awlstuh" is only the phonetic
    spelling the prompt uses for TTS and never appears in this field — it is
    handled, along with STT mishears, by _ALCESTER_VARIANTS on the spoken path.
    """
    assert location_from_appointment_type(appt_type) == expected


@pytest.mark.parametrize("appt_type", [
    "",
    "Initial Assessment - 60 minutes",   # a template clinic's type
    "Follow-up",
    None,
])
def test_types_that_name_no_clinic_return_empty(appt_type):
    """Empty means "leave the session alone" — never guess a clinic."""
    assert location_from_appointment_type(appt_type) == ""


def test_lookup_patient_applies_it_inside_emit():
    """`_emit` is the one choke point every match flows through.

    It has to be there rather than at the call site, because `next=true` — the
    caller saying "that's not the one" — returns through `_emit` too, and a
    second appointment can be at the other clinic. Setting it anywhere else
    leaves the stale location on exactly that path.
    """
    src = inspect.getsource(_exec_lookup_patient)
    assert "def _emit(" in src, (
        "_exec_lookup_patient no longer defines _emit — the choke point this relies "
        "on has moved; re-point this test at the new one"
    )
    emit_src = src[src.index("def _emit("):]
    # _emit ends at the next dedented top-level statement in the function body
    end = emit_src.index("\n    # ── Advance to the NEXT match")
    emit_src = emit_src[:end]

    assert "location_from_appointment_type" in emit_src, (
        "the location is no longer derived inside _emit — the next=true path "
        "would keep the previous appointment's clinic"
    )
    assert "selected_location" in emit_src, (
        "_emit no longer writes selected_location — downstream tools fall "
        "back to the alcester default"
    )


def test_the_appointment_type_still_reaches_the_model():
    """The prompt derives location from this field too. The session write is a
    belt-and-braces addition, not a replacement — the field must stay in the
    tool result."""
    src = inspect.getsource(_exec_lookup_patient)
    emit_src = src[src.index("def _emit("):]
    emit_src = emit_src[:emit_src.index("\n    # ── Advance to the NEXT match")]
    assert '"appointment_type": appt.get("type", "")' in emit_src
