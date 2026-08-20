"""
Vital Edge live call (2026-08-20, CAa0f76e2c2851f9eb3f28eddc38b75e3b) — the
owner was texted a phone number 13 seconds before the caller was asked whether
it was the right one.

Live-call trace:
    15:32:05      "phone DTMF STAYS ARMED — the caller spoke but no number is
                  on record yet"
    15:32:09,348  tool: request_callback {"patient_name": "Ray Roger",
                  "phone": "07796479460", ...}
    15:32:09,637  tool result: {"success": true}   -> owner SMS queued
    15:32:22,45   Susie: "...I've got you on 07796479460..."
    15:32:26      "phone DTMF STAYS ARMED — ... no number is on record yet"

The read-back at 15:32:22 was decorative. Had Ray corrected the number, the
text carrying the wrong one had already gone out.

NOT a misordering of the callback contract. The contract
(clinic_template_prompt.py, CALLBACK CONTRACT) is four steps — (1) name,
(2) confirm the number, (3) call the tool, (4) only then promise contact — and
"write before you promise" is load-bearing: it is the whole reason
`request_callback` exists (CAc36368cbeb, Dylan Wilson, 2026-08-13, where the
promise was spoken and nobody was told). Step 2 was SKIPPED, not reordered, and
the fix must not move the write behind the promise to compensate.

Step 2 had nothing mechanical behind it. `book_appointment` has held the same
boundary since 26 July with the A1 gate; its two owner-notifying siblings never
got one. `_unconfirmed_callback_number` is that gate, applied to both.

Why the gate is ONE-SHOT — the property test_the_gate_is_spent_after_one_refusal
exists to protect. Three paths in the engine set `phone_confirmed`: keypad
entry (connection.py `_commit_dtmf_phone_for_booking`) and two verbal branches
that both require a YES to a question carrying a `_PHONE_STEP_MARKERS` phrase.
There is no path by which a number SPOKEN aloud arms it — the A1 gate's own
comment claims "spoken number" as a third path and is wrong about its own
codebase. So a caller who answers the confirmation by reciting a different
number can never satisfy a permanent gate and would be asked forever. Refusing
once buys the confirmation turn; refusing twice risks the call.

That is the failure mode the A2 reason gate produced on Theorem — a gate
demanding a slot that nothing on that clinic could fill made booking
impossible. A one-shot gate cannot reach that state by construction.
"""

from __future__ import annotations

import inspect

import pytest

from app.media_streams.connection import should_notify_unreached_caller
from app.media_streams.llm_stream import _PHONE_STEP_MARKERS
from app.tools import receptionist_tools
from app.tools.receptionist_tools import (
    _exec_add_to_waitlist,
    _exec_request_callback,
)


def _unconfirmed_callback_number(session: dict, tool_name: str):
    """Resolve the gate at call time rather than importing it by name.

    Deliberate, and the same reasoning as the sibling B-68 filler test. A
    top-level `from ... import _unconfirmed_callback_number` makes the whole
    module die with ImportError on the code as it stood before the fix, which
    demonstrates only that a symbol is new. Every assertion below would then be
    "red" without a single one of them having exercised the defect. Resolving
    it here means the pre-fix run fails on the property each test is actually
    about, stated in the language of the live call.
    """
    gate = getattr(receptionist_tools, "_unconfirmed_callback_number", None)
    if gate is None:
        raise AssertionError(
            "receptionist_tools has no _unconfirmed_callback_number: nothing "
            "stops request_callback / add_to_waitlist texting the owner a "
            "number the caller was never asked to confirm (the 15:32:09 write "
            "on CAa0f76e2c2851f9eb3f28eddc38b75e3b)."
        )
    return gate(session, tool_name)


# Both owner-notifying writes. Named here rather than parametrised over one so
# that adding a third sibling and forgetting its gate fails a test rather than
# silently inheriting nothing — the "fixed one of three copies" pattern.
GATED_EXECUTORS = [
    ("request_callback", _exec_request_callback),
    ("add_to_waitlist", _exec_add_to_waitlist),
]


def _session(**kw) -> dict:
    s = {"clinic_id": "vital_edge", "collected": {"phone": "07796479460"}}
    s.update(kw)
    return s


# -- the gate itself ---------------------------------------------------------

@pytest.mark.parametrize("tool_name,_exec", GATED_EXECUTORS)
def test_an_unconfirmed_number_is_refused(tool_name, _exec):
    """The 15:32:09 write. No confirmation on record, so no write and no SMS."""
    out = _unconfirmed_callback_number(_session(), tool_name)
    assert out is not None, (
        f"{tool_name} fired with phone_confirmed unset — this is the live "
        "defect: the owner is texted a number the caller was never asked about."
    )
    assert out["success"] is False
    assert tool_name in out["error"]


@pytest.mark.parametrize("tool_name,_exec", GATED_EXECUTORS)
def test_a_confirmed_number_passes_straight_through(tool_name, _exec):
    out = _unconfirmed_callback_number(
        _session(phone_confirmed=True), tool_name
    )
    assert out is None, (
        "a caller who confirmed their number must not be asked again — that is "
        "the turn this gate is allowed to cost, spent twice."
    )


@pytest.mark.parametrize("tool_name,_exec", GATED_EXECUTORS)
def test_truthy_is_not_confirmed(tool_name, _exec):
    """`is True`, matching A1. A stray truthy value is not a confirmation."""
    for _junk in ("yes", 1, "07796479460"):
        assert _unconfirmed_callback_number(
            _session(phone_confirmed=_junk), tool_name
        ) is not None, f"phone_confirmed={_junk!r} was treated as confirmed"


def test_the_gate_is_spent_after_one_refusal():
    """One-shot. A caller who answers with a spoken number must not be looped.

    Nothing in the engine converts a spoken number into phone_confirmed, so a
    permanent gate would re-ask this caller until they gave up or the call died.
    """
    s = _session()
    assert _unconfirmed_callback_number(s, "request_callback") is not None
    assert _unconfirmed_callback_number(s, "request_callback") is None, (
        "the gate refused twice — a caller who answers the confirmation by "
        "speaking a different number can never arm phone_confirmed, so this "
        "loops until the call ends (the A2-on-Theorem failure)."
    )


def test_the_gate_is_spent_across_both_tools():
    """The budget is one refusal per CALL, not one per tool.

    Two tools each refusing once is two confirmation turns for one number,
    which is the same loop wearing a different hat.
    """
    s = _session()
    assert _unconfirmed_callback_number(s, "request_callback") is not None
    assert _unconfirmed_callback_number(s, "add_to_waitlist") is None


# -- loop termination --------------------------------------------------------

def test_the_refusal_dictates_a_sentence_that_can_arm_the_flag():
    """The refusal must coach the ONE question shape that closes the loop.

    `_phone_question_on_the_table` substring-matches the assistant's last
    question against `_PHONE_STEP_MARKERS`; a caller's "yes" only arms
    phone_confirmed when that test passes. If this message is ever reworded to
    something that carries no marker, the verbal exit stops working and every
    callback costs a wasted turn. Cheap to break, invisible when broken.
    """
    msg = _unconfirmed_callback_number(_session(), "request_callback")["error"]
    low = msg.lower()
    hit = [mk for mk in _PHONE_STEP_MARKERS if mk in low]
    assert hit, (
        "the refusal coaches no _PHONE_STEP_MARKERS phrase, so a caller's "
        "'yes' to it cannot arm phone_confirmed:\n" + msg
    )


def test_the_refusal_forbids_the_promise():
    """The contract's point: nothing may be promised until the write succeeds."""
    low = _unconfirmed_callback_number(
        _session(), "request_callback"
    )["error"].lower()
    assert "do not tell them" in low and "in touch" in low
    assert "keypad" in low, (
        "keypad entry is the only path that arms phone_confirmed for a caller "
        "who wants a DIFFERENT number; the refusal has to offer it."
    )


# -- the caller who drops on the confirmation turn ---------------------------

def test_a_refused_caller_who_hangs_up_still_reaches_staff():
    """The gate must not trade a wrong number for a silently lost lead.

    Refusing the write means the clinic has not been told. If the caller drops
    on the confirmation turn, teardown's drop-off net is the only thing left —
    and it reads exactly `human_requested` and NOT `_waitlist_pinged`.
    """
    s = _session()
    _unconfirmed_callback_number(s, "request_callback")
    assert s.get("human_requested") is True
    assert not s.get("_waitlist_pinged"), (
        "_waitlist_pinged suppresses the drop-off ping — setting it here means "
        "a refused caller who hangs up reaches nobody at all, which is the "
        "Dylan Wilson miss this tool exists to prevent."
    )
    assert should_notify_unreached_caller(s) is True


def test_a_refusal_leaves_no_callback_request_on_the_record():
    """A refused write must not look like a completed one to the call record."""
    s = _session()
    _unconfirmed_callback_number(s, "request_callback")
    assert "callback_request" not in s


# -- placement inside the executors ------------------------------------------

@pytest.mark.parametrize("tool_name,_exec", GATED_EXECUTORS)
def test_the_gate_runs_before_anything_is_written_or_sent(tool_name, _exec):
    """Source assertion: the gate has to precede every side effect.

    Driving these executors for real is not an option — patching
    `sms.send_sms` does not stop the send, because `booking_sms`, `owner_alert`
    and `smart_sms_router` each bind their own reference at import. A test that
    got this wrong texted the owner six times.
    """
    src = inspect.getsource(_exec)
    assert "_unconfirmed_callback_number(" in src, (
        f"{tool_name} has no phone gate — it can still text the owner a number "
        "nobody confirmed."
    )
    gate_at = src.index("_unconfirmed_callback_number(")

    for effect in (
        "_queue_owner_callback_sms(",
        'session["human_requested"] = True',
        'session.setdefault("callback_request"',
        'session["_waitlist_pinged"] = True',
    ):
        at = src.find(effect)
        if at == -1:
            continue
        assert gate_at < at, (
            f"in {tool_name}, `{effect}` runs BEFORE the phone gate. The whole "
            "defect is a side effect landing ahead of the confirmation."
        )
