"""Vital Edge must never render the confirmed-booking closing.

Vital Edge books PROVISIONALLY: a published Google Calendar event is flipped to
PENDING and Jonathan confirms out of band. "All booked" is never a true sentence
on this clinic.

That prohibition is enforced by the PROMPT ONLY. Gate 5f cannot back it up: on a
successful write `_note_write_result` sets `booking_write_confirmed`
(llm_stream.py), and `_armed_write_families` arms the booking family only when
`booking_flow_active AND NOT booking_write_confirmed` (turn_handler.py). A
successful provisional booking therefore DISARMS the guard, and a completion
claim on that turn reaches the caller unchallenged.

So the prompt branch is the whole safety net, and it hangs on one string
comparison:

    clinic.json  operational.booking_system
      -> clinic_config._map_json_to_clinic_contract  (flattens to top level)
        -> clinic_template_prompt._tokens
          -> is_provisional = tk["booking_system"] == "google_calendar_provisional"

Any break in that chain flips Vital Edge to the else-branch, whose success line
is literally "All booked - you're in for [day] the [ordinal] at [time]." No test
covered the chain; an audit on 2026-08-04 reached for the raw loader
(`load_clinic`, which does NOT flatten `operational.*`) and got is_provisional
False, which is exactly what the real failure would look like.

These tests pin the rendered output, through the live entry point, so that a
refactor of the config flattening cannot silently give a real caller a false
confirmation.
"""
import re

import pytest

from app.clinic_config import get_clinic
from app.prompts.clinic_template_prompt import _tokens
from app.prompts.susie_system_prompt import build_system_prompt_parts

CLINIC_ID = "vital_edge"

# The success line from the NON-provisional branch of clinic_template_prompt.
# If this ever renders for Vital Edge, Susie tells callers a provisional
# request is a confirmed appointment.
CONFIRMED_SUCCESS_LINE = "on success say exactly: 'all booked"

# The provisional branch's opening words.
PROVISIONAL_SUCCESS_LINE = "on success the booking is provisional"


def _rendered_prompt() -> str:
    session = {
        "call_sid": "CAtest_ve_provisional",
        "clinic_id": CLINIC_ID,
        "booking_flow_active": True,
        "collected": {},
    }
    static, dynamic = build_system_prompt_parts(session)
    return f"{static}\n\n{dynamic}"


def test_booking_system_survives_the_config_flattening():
    """operational.booking_system must reach _tokens intact.

    get_clinic() flattens it to the top level; _tokens reads the top level only.
    Asserting both ends catches a break at either.
    """
    clinic = get_clinic(CLINIC_ID)
    assert clinic.get("prompt_engine") == "template_v1"
    assert clinic["booking_system"] == "google_calendar_provisional"
    assert _tokens(clinic)["booking_system"] == "google_calendar_provisional"


def test_vital_edge_renders_the_provisional_closing():
    low = _rendered_prompt().lower()
    assert PROVISIONAL_SUCCESS_LINE in low, (
        "Vital Edge is not rendering the provisional closing - is_provisional "
        "is False. Every booking will be narrated as confirmed."
    )


def test_vital_edge_never_renders_the_confirmed_closing():
    low = _rendered_prompt().lower()
    assert CONFIRMED_SUCCESS_LINE not in low, (
        "The non-provisional 'All booked' success line rendered for Vital "
        "Edge. A provisional request would be narrated to the caller as a "
        "confirmed appointment."
    )


@pytest.mark.parametrize(
    "phrase",
    ["all booked", "you're booked in", "confirmation text has been sent"],
)
def test_banned_phrases_appear_only_inside_the_prohibition(phrase):
    """The banned wording may appear ONLY where it is being forbidden.

    The provisional branch names each phrase in order to ban it, so presence
    alone is not a defect - presence in an instruction telling Susie to SAY it
    is. Every occurrence must sit within the prohibition sentence, which is
    anchored by 'Do NOT say'.
    """
    low = _rendered_prompt().lower()
    for m in re.finditer(re.escape(phrase), low):
        window = low[max(0, m.start() - 220):m.start()]
        assert "do not say" in window or "never tell" in window, (
            f"{phrase!r} occurs outside a prohibition, at offset {m.start()}: "
            f"...{low[max(0, m.start() - 120):m.end() + 60]!r}"
        )


def test_the_clinics_own_pending_message_is_used_not_the_fallback():
    """clinic.json's pending message must win over the in-code default.

    The fallback ("...sent it to Jonathan to confirm - your booking isn't
    finalised until you hear from him.") drops the payment and
    he-may-suggest-another-time clauses Jonathan asked for.
    """
    configured = (
        get_clinic(CLINIC_ID).get("prompt_facts", {}) or {}
    ).get("booking_pending_message")
    assert configured, "vital_edge has no prompt_facts.booking_pending_message"
    assert configured[:60].lower() in _rendered_prompt().lower()
