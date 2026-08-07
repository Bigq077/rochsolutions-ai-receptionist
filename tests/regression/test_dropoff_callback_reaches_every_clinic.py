"""
Regression (O-2): a caller who gave their details and dropped must reach a human.

`WebSocketCallHandler.cleanup` carries a drop-off callback safety net — name and
phone captured, nothing else notified anyone, so text the practitioner. It was
gated on:

    _dc_clinic.get("booking_system") == "google_calendar_provisional"

which is Vital Edge and nothing else. Theorem books into Acuity, JV into plain
Google Calendar, so neither ever got the net. CA6e1024db (2026-08-07, theorem_v3)
gave a name, picked a slot, typed eleven digits and hung up, and nobody was told.

The register recorded this as "nothing calls take_message on abandonment". That
is not it: `take_message` does not exist anywhere in the codebase — no schema, no
executor — and the mechanism that does exist was simply gated to one clinic. The
gate is now an explicit per-clinic opt-out that defaults to on, so a new clinic
inherits the net rather than having to be remembered.

These tests read the source rather than driving cleanup, because cleanup is
~700 lines of teardown with Twilio, Sheets and Redis in it. The property under
test is which condition the branch is gated on, and that is exactly what a
source assertion can pin. If the block is ever restructured this file must be
rewritten with it — that is the intended cost.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.clinic_config import get_clinic
from app.media_streams.connection import WebSocketCallHandler

LIVE_CLINICS = ["theorem_v3", "theorem", "jv_v1", "vital_edge"]


def _strip_comments(src: str) -> str:
    """
    Code only. These assertions are about what the branch is gated on, and the
    comment above it necessarily quotes the old condition to explain the fix —
    matching prose would make the test pass or fail on the wording.
    """
    return "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )


def _dropoff_block() -> str:
    """The drop-off safety-net block, isolated from the rest of cleanup."""
    src = inspect.getsource(WebSocketCallHandler)
    start = src.index("Drop-off callback safety net")
    end = src.index("_dropoff_callback_sent\"] = True", start)
    return _strip_comments(src[start:end])


def test_the_net_is_not_gated_on_one_clinics_booking_system():
    block = _dropoff_block()
    assert "google_calendar_provisional" not in block, (
        "the drop-off net is gated on the provisional booking system again — "
        "that is Vital Edge only, and Theorem and JV lose the lead silently"
    )


def test_the_gate_is_an_explicit_opt_out_that_defaults_on():
    block = _dropoff_block()
    assert "dropoff_callback_enabled" in block, "the opt-out key is gone"
    assert re.search(r'dropoff_callback_enabled"\)\s+is not False', block), (
        "the gate must default ON — `is not False` means an unset key still "
        "sends. `.get(...)` truthiness would silently disable every clinic "
        "that has not been edited, which is the bug this fixes"
    )


@pytest.mark.parametrize("clinic_id", LIVE_CLINICS)
def test_no_live_clinic_has_opted_out(clinic_id):
    """
    The opt-out exists for a clinic that genuinely does not want these. None do
    today; if one is added, that is a decision someone should have to make
    deliberately, and this test is where they will be told about it.
    """
    clinic = get_clinic(clinic_id) or {}
    assert clinic.get("dropoff_callback_enabled") is not False, (
        f"{clinic_id} has opted out of drop-off callbacks — intended?"
    )


@pytest.mark.parametrize("clinic_id", LIVE_CLINICS)
def test_every_live_clinic_has_somewhere_to_send_it(clinic_id):
    """A net with no destination is not a net."""
    clinic = get_clinic(clinic_id) or {}
    assert (clinic.get("transfer_phone") or "").strip(), (
        f"{clinic_id} has no transfer_phone — the drop-off ping has nowhere to go"
    )


def test_the_guards_that_prevent_a_duplicate_are_still_there():
    """
    Widening the gate is only safe because the other conditions still scope it:
    details actually captured, nobody notified by another route, not already
    sent this call.
    """
    block = _dropoff_block()
    for guard in ("_dc_name and _dc_phone", "_dc_owner_notified",
                  "_dropoff_callback_sent"):
        assert guard in block, f"{guard} is gone — the net can now double-send"

    for notified in ("_waitlist_pinged", "provisional_booking", "booking_confirmed",
                     "cancel_confirmed", "transfer_attempted"):
        assert notified in block, (
            f"{notified} no longer suppresses the ping — the practitioner gets "
            f"a second text about a call that already reached them"
        )


def test_take_message_still_does_not_exist():
    """
    Pins the correction, so the register's wording cannot send someone hunting
    for a tool that was never written. If a real take_message is added later,
    delete this test — do not make it pass by aliasing something else.
    """
    from app.tools.receptionist_tools import TOOL_EXECUTORS, build_tool_schemas
    assert "take_message" not in TOOL_EXECUTORS
    for clinic_id in LIVE_CLINICS:
        names = {t.get("name") for t in build_tool_schemas(clinic_id)}
        assert "take_message" not in names
        assert "add_to_waitlist" in names, (
            f"{clinic_id} lost add_to_waitlist — that is the only tool that "
            f"turns an in-call 'ring me back' into a text to a human"
        )
