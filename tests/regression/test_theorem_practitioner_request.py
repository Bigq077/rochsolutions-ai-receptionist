"""A practitioner request must not become a promise the tools cannot keep.

Live call CA78d0088416b92b0c2b36bf2f729700e6, 2026-08-05 20:44. The caller said
"I've seen Leanne before, can I get back in with her?". Susie agreed and offered
to book her in. The tool call that followed was:

    check_availability {"service": "physiotherapy assessment",
                        "location": "alcester", "date_hint": "Tuesday"}
    GET /availability/times?appointmentTypeID=15823699&calendarID=4256627

Leanne is nowhere in it. 4256627 is the Alcester *location* calendar, and
Tuesday is a Mark day — the prompt's own rota says Leanne is Awlstuh Thu/Fri.
The caller would have arrived expecting Leanne and found Mark.

There is no practitioner parameter on check_availability or book_appointment,
`THEOREM_PRACTITIONERS[*]["acuity_calendar_id"]` is read by nothing in the
booking path, and `filter_slots_by_practitioner_availability` is never called.
Confinement was never built, so the old prompt line "honour requests" asked for
something the tools cannot do.

Until it is built, the rota IS the mechanism: at Awlstuh, Mon/Tue/Wed is Mark
and Thu/Fri is Leanne, so steering to the right days confines the booking using
only the location calendar. These tests pin that instruction into the prompt
Mark's deployment actually renders — clinic.json does not reach his model, see
_build_theorem_v3.
"""

import pytest

from app.prompts.susie_system_prompt import _build_theorem_v3


@pytest.fixture(scope="module")
def prompt() -> str:
    static, dynamic = _build_theorem_v3({"clinic_id": "theorem_v3"})
    return f"{static}\n{dynamic}"


def test_the_rota_is_still_stated(prompt):
    assert "Leanne (BSc Hons HCPC) at Awlstuh Thu/Fri only" in prompt
    assert "Mark Dyer at Awlstuh Mon/Tue/Wed and Redditch Thu" in prompt


def test_the_bare_honour_requests_promise_is_gone(prompt):
    """The old line read "Practitioners (both qualified prescribers, honour
    requests)" and nothing else — an instruction to do the impossible, with no
    account of how. That phrasing must not come back on its own."""
    assert "honour requests" not in prompt


def test_the_model_is_told_it_cannot_book_a_person(prompt):
    """The root cause is a capability the model believed it had."""
    assert "You cannot book a named person" in prompt


def test_the_days_are_given_as_the_mechanism(prompt):
    assert "check availability for THEIR days and offer only" in prompt
    assert "Caller wants Leanne: offer Awlstuh Thursday or Friday" in prompt


def test_offering_outside_the_days_is_forbidden(prompt):
    """This is the exact defect from the call — a Tuesday offered for Leanne."""
    assert "NEVER offer a slot outside a requested practitioner's days" in prompt


def test_implying_the_person_is_forbidden_too(prompt):
    """Steering to the right day is confinement; saying "you'll see Leanne" is
    a guarantee, and the tools cannot back one."""
    assert "NEVER say or imply they will see that person" in prompt


def test_redditch_leanne_is_neither_confirmed_nor_denied(prompt):
    """The sources contradict each other and Mark has not arbitrated:
    canonical.py says "Leanne is not available at Redditch"; clinic_config.py's
    THEOREM_PRACTITIONERS gives her Redditch Mon. Asserting either one to a
    caller would be inventing an answer, so the prompt must do neither."""
    assert "do not confirm or deny" in prompt


def test_no_practitioner_parameter_exists_yet(prompt):
    """Guard on the premise of this whole fix. If a `practitioner` arg is ever
    added to the booking tools, real confinement becomes possible and this
    prompt workaround should be revisited rather than left to drift."""
    from app.tools.receptionist_tools import (
        TOOL_CHECK_AVAILABILITY,
        TOOL_BOOK_APPOINTMENT,
    )

    for schema in (TOOL_CHECK_AVAILABILITY, TOOL_BOOK_APPOINTMENT):
        assert "practitioner" not in schema["input_schema"]["properties"], (
            "A practitioner parameter now exists — the prompt-only workaround in "
            "_build_theorem_v3 was a stopgap for its absence. Route bookings to "
            "THEOREM_PRACTITIONERS[*]['acuity_calendar_id'] and revisit it."
        )
