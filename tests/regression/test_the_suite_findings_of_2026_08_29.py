"""Three defects the adaptive-caller suite found on its first full run.

Each was reproduced without a phone, from a saved transcript, on 2026-08-29 —
which is the point of the harness. Each is pinned here against the shape that
produced it rather than the sentence, because the sentences get reworded.
"""
from __future__ import annotations

import asyncio

import pytest

from app.hold_speech import is_closing
from app.media_streams.llm_stream import (
    _book_verdict_deterministic,
    _phone_confirm_verdict,
)


# ── 1. A terminal action fires once ─────────────────────────────────────────

def test_a_second_transfer_is_a_no_op():
    """`wants_a_human`: Susie said "Putting you through to Priya now", the
    caller said "Cheers, thanks", and the courtesy triggered a SECOND
    transfer_to_human — a second dial leg and a second "call coming through
    now" text to a practitioner who is then waiting for a ring that already
    happened.

    Idempotent rather than an error: `request_transfer` already drives the leg
    twilio.py places, so the right answer to a repeat is the same answer with
    no second side effect. Raising would turn a harmless duplicate into a
    failed turn.
    """
    from app.tools.receptionist_tools import _exec_transfer_to_human

    session = {"clinic_id": "northgate", "twilio_from": "+447700900240"}
    first = asyncio.run(_exec_transfer_to_human({"reason": "caller asked"}, session))
    second = asyncio.run(_exec_transfer_to_human({"reason": "caller said thanks"}, session))

    assert first["transfer_initiated"] is True
    assert second["transfer_initiated"] is True
    assert second["already_in_flight"] is True
    assert second["reason"] == "caller asked", (
        "the repeat overwrote the reason the transfer was actually placed for"
    )


# ── 2. No hold phrase in front of a goodbye ─────────────────────────────────

@pytest.mark.parametrize("utterance", [
    # The exact turn from the red-flag call, which is the worst place for it:
    # the caller had just been told to contact NHS 111.
    "Alright. I'll ring 111 then. Thanks.",
    "Great, thanks. Bye.",
    "Cheers, thanks.",
    "That's all I needed, thank you.",
    "Lovely, thank you very much — see you Friday.",
])
def test_a_caller_saying_goodbye_is_not_waiting(utterance):
    assert is_closing(utterance)


@pytest.mark.parametrize("utterance", [
    # A courtesy on the front of a request is still a request, and these are the
    # turns that need a head most.
    "Thanks, could you check Thursday for me?",
    "Thanks — and is there parking?",
    "Thanks. Have you got anything later?",
    "Ok thanks, but can I move it to next week please?",
    "Will I see you on Friday then?",
    "Yeah, go ahead.",
])
def test_a_courtesy_in_front_of_a_request_is_not_a_goodbye(utterance):
    assert not is_closing(utterance)


def test_the_producer_asks_before_falling_back_to_the_contentless_head():
    """The check has to run BEFORE the UNKNOWN_SLOW fallback, because that
    fallback is exactly what fired: "Sorry, still with you — Take care of
    yourself" after "Alright. I'll ring 111 then. Thanks."."""
    import inspect

    from app.media_streams import llm_stream

    source = inspect.getsource(llm_stream)
    closing_at = source.index("is_closing as _is_closing")
    fallback_at = source.index("_kind = _WorkKind.UNKNOWN_SLOW")
    assert closing_at < fallback_at, (
        "the closing check moved below the UNKNOWN_SLOW fallback, so the "
        "contentless head fires on goodbyes again"
    )


# ── 3. A qualified yes is not a refusal ─────────────────────────────────────

@pytest.mark.parametrize("utterance, verdict", [
    # THE defect. "I don't think" scored the whole utterance a refusal, so
    # phone_confirmed stayed False, PHONE STEP OUTSTANDING kept rendering, and
    # the model re-asked the phone question after the caller had already agreed
    # to the booking. The A4 confirmation loop; 144 of them in the obs corpus.
    ("I don't think I gave you that, but yes, that's my number", "unsure"),
    # "another" contains "no". Third instance of the substring-negator family
    # in this codebase — the screening triggers had it when "know" matched "no".
    ("I haven't got another one, but yes that's right", "yes"),
    # The refusals the ordering exists to protect, all unchanged.
    ("don't use that one", "no"),
    ("yes, but don't use that one", "no"),
    ("yes but can I give you a different number", "no"),
    ("yes, actually hold on, use my mobile", "no"),
    ("no, that's not the right number", "no"),
    ("use a different number please", "no"),
    ("nope", "no"),
    # The plain cases.
    ("Yeah, that's the one", "yes"),
    ("yes", "yes"),
    ("sorry, yes, that is correct", "yes"),
    ("erm", "unsure"),
])
def test_the_phone_confirm_verdict(utterance, verdict):
    assert _phone_confirm_verdict(utterance) == verdict


def test_unsure_can_never_satisfy_the_book_gate():
    """The safety property the fix rests on. 'unsure' is not a soft yes: it
    routes to the bounded keypad ladder and cannot store a caller ID."""
    assert _phone_confirm_verdict(
        "I don't think I gave you that, but yes, that's my number"
    ) != "yes"


@pytest.mark.parametrize("utterance, verdict", [
    ("don't book it", "no"),
    ("yes but can we do friday instead", "no"),
    ("yeah actually hang on", "no"),
    ("yes", "yes"),
    ("no", "no"),
])
def test_the_booking_consent_gate_is_untouched(utterance, verdict):
    """`_book_verdict_deterministic` keeps the UNPOSITIONED correction check on
    purpose: on the booking path a wrong write is worse than a re-ask, so FM-01
    requires a retraction anywhere in the sentence to block. The phone fix must
    not have leaked into it.

    It nearly did. A str.replace while making this change removed the
    fast_path import from BOTH functions at once, and this gate raised
    NameError until it was put back.
    """
    assert _book_verdict_deterministic(utterance) == verdict


# ── 3b. The upstream half: no turns once the caller is with a human ─────────

def test_the_latch_is_set_only_where_a_leg_is_actually_placed():
    """A blocked transfer and a transfer with no dial target both keep the
    caller WITH Susie — and the no-target path has just asked them a question
    it needs the answer to. Latching there would strand them in silence, which
    is the failure that path exists to prevent.
    """
    import inspect

    from app.media_streams.connection import WebSocketCallHandler

    source = inspect.getsource(WebSocketCallHandler._on_transfer_request)
    latch_at = source.index('self.session["transfer_placed"] = True')
    handle_at = source.index("_handle_transfer(self.call_sid, self.session)")
    no_target_at = source.index("transfer_unavailable")
    blocked_at = source.index("transfer blocked")

    assert latch_at > handle_at, "latched before the leg was placed"
    assert latch_at > no_target_at, "the no-dial-target path must not latch"
    assert latch_at > blocked_at, "a blocked transfer must not latch"


def test_a_transcript_after_the_transfer_does_not_open_a_turn():
    """`wants_a_human`: Susie said "Putting you through to Priya now", the
    caller said "Cheers, thanks", and that courtesy opened a whole new LLM turn
    which called transfer_to_human again and repeated the line.

    Answering it means Susie talking over the human she just handed the caller
    to. The guard is placed before the turn is dispatched, so there is nothing
    to suppress downstream — which matters, because suppressing the repeated
    SENTENCE would mean matching one literal of model speech.
    """
    import inspect

    from app.media_streams import connection

    source = inspect.getsource(connection.WebSocketCallHandler)
    guard_at = source.index('if self.session.get("transfer_placed"):')
    dispatch_at = source.index('"[ms_conn v3] transcript: %r"')
    assert guard_at < dispatch_at, (
        "the transfer guard moved below the transcript dispatch, so a caller's "
        "goodbye opens another turn again"
    )
